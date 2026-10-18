"""Compile approved pipeline JSON into deterministic source SQL and stages."""

import ast
import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

from services.sql_builder import SqlBuildError, _quote


_FILTER_OPERATORS = {
    "eq": "=",
    "ne": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}
_FILTER_OPERATOR_ALIASES = {
    "=": "eq",
    "==": "eq",
    "equals": "eq",
    "!=": "ne",
    "<>": "ne",
    "not_equals": "ne",
    ">": "gt",
    "greater_than": "gt",
    ">=": "gte",
    "greater_than_or_equal": "gte",
    "<": "lt",
    "less_than": "lt",
    "<=": "lte",
    "less_than_or_equal": "lte",
    "is null": "is_null",
    "is not null": "is_not_null",
}
_AGGREGATIONS = {
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "COUNT",
    "COUNT_DISTINCT",
    "STDDEV",
}


class PipelineExecutionError(ValueError):
    """A saved execution definition is malformed or unsupported."""


def _resolve_column(
    alias_map: Mapping[str, str],
    column: str,
    table: Optional[str] = None,
) -> str:
    if not isinstance(column, str) or not column.strip():
        raise PipelineExecutionError("Saved column references must be non-empty strings.")
    if table is not None and not isinstance(table, str):
        raise PipelineExecutionError("Saved source table references must be strings.")
    matches = [
        alias
        for alias, source in alias_map.items()
        if source.rsplit(".", 1)[-1].casefold() == column.casefold()
        and (
            table is None
            or source.rsplit(".", 1)[0].casefold() == table.casefold()
        )
    ]
    if len(matches) != 1:
        raise PipelineExecutionError(
            "Saved column reference '%s%s' resolves to %d output columns."
            % ("%s." % table if table else "", column, len(matches))
        )
    return matches[0]


class _ExpressionCompiler(ast.NodeVisitor):
    def __init__(self, db_type: str, alias_map: Mapping[str, str]):
        self.db_type = db_type
        self.alias_map = alias_map

    def visit_Name(self, node: ast.Name) -> str:
        try:
            alias = _resolve_column(self.alias_map, node.id)
        except PipelineExecutionError as exc:
            raise PipelineExecutionError(str(exc)) from exc
        return "src.%s" % _quote(self.db_type, alias)

    def visit_Attribute(self, node: ast.Attribute) -> str:
        if not isinstance(node.value, ast.Name):
            raise PipelineExecutionError("Only table.column references are supported.")
        alias = _resolve_column(self.alias_map, node.attr, node.value.id)
        return "src.%s" % _quote(self.db_type, alias)

    def visit_Constant(self, node: ast.Constant) -> str:
        if isinstance(node.value, bool):
            return "1" if node.value else "0"
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return repr(node.value)
        raise PipelineExecutionError(
            "Derived expressions may contain only numeric constants and columns."
        )

    def visit_BinOp(self, node: ast.BinOp) -> str:
        operators = {
            ast.Add: "+",
            ast.Sub: "-",
            ast.Mult: "*",
            ast.Div: "/",
            ast.Mod: "%",
        }
        operator = operators.get(type(node.op))
        if operator is None:
            raise PipelineExecutionError("Unsupported operator in derived expression.")
        return "(%s %s %s)" % (
            self.visit(node.left),
            operator,
            self.visit(node.right),
        )

    def visit_UnaryOp(self, node: ast.UnaryOp) -> str:
        if isinstance(node.op, ast.UAdd):
            return "(+%s)" % self.visit(node.operand)
        if isinstance(node.op, ast.USub):
            return "(-%s)" % self.visit(node.operand)
        raise PipelineExecutionError("Unsupported unary operator in derived expression.")

    def visit_Call(self, node: ast.Call) -> str:
        if (
            not isinstance(node.func, ast.Name)
            or node.func.id.casefold() != "nullif"
            or len(node.args) != 2
            or node.keywords
        ):
            raise PipelineExecutionError(
                "Only NULLIF(value, value) is supported in derived expressions."
            )
        return "NULLIF(%s, %s)" % (
            self.visit(node.args[0]),
            self.visit(node.args[1]),
        )

    def generic_visit(self, node: ast.AST) -> str:
        raise PipelineExecutionError(
            "Unsupported syntax in derived expression: %s."
            % type(node).__name__
        )


def build_execution_graph(pipeline_json: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Describe the stable execution order frozen into a pipeline version."""
    stages = [
        ("extract_and_join", True),
        ("filters", bool(pipeline_json.get("filters"))),
        ("derived_columns", bool(pipeline_json.get("derived_columns"))),
        ("aggregations", bool(pipeline_json.get("aggregations"))),
        (
            "cleaning",
            bool(pipeline_json.get("cleaning_steps") or pipeline_json.get("transformations")),
        ),
        ("quality_validation", bool(pipeline_json.get("quality_rules"))),
        ("register_dataset", True),
    ]
    graph: List[Dict[str, Any]] = []
    previous = None
    for stage, enabled in stages:
        if not enabled:
            continue
        graph.append({"stage": stage, "depends_on": [previous] if previous else []})
        previous = stage
    return graph


def _validate_aggregate_join_safety(pipeline_json: Mapping[str, Any]) -> None:
    aggregations = pipeline_json.get("aggregations", [])
    if not aggregations:
        return
    joins = pipeline_json.get("joins", [])
    fanout = []
    remaining = []
    for join in joins:
        if not isinstance(join, dict):
            raise PipelineExecutionError("Saved join must be an object.")
        cardinality = str(join.get("cardinality") or "").upper()
        if cardinality not in ("1:1", "1:M", "M:1", "M:M"):
            raise PipelineExecutionError(
                "Saved aggregations require known join cardinality to avoid fan-out."
            )
        remaining.append((join, cardinality))

    base_table = pipeline_json.get("base_table")
    if not isinstance(base_table, str) or not base_table:
        tables = pipeline_json.get("tables") or []
        base_table = tables[0] if tables else None
    joined_tables = {base_table} if base_table else set()
    while remaining:
        next_edge = next(
            (
                (index, edge, cardinality)
                for index, (edge, cardinality) in enumerate(remaining)
                if edge.get("left_table") in joined_tables
                or edge.get("right_table") in joined_tables
            ),
            None,
        )
        if next_edge is None:
            break
        index, edge, cardinality = next_edge
        remaining.pop(index)
        left, right = edge.get("left_table"), edge.get("right_table")
        new_table = right if left in joined_tables else left
        many_side = {
            "1:M": right,
            "M:1": left,
            "M:M": None,
        }.get(cardinality)
        if cardinality == "M:M" or new_table == many_side:
            fanout.append((edge, cardinality, new_table))
        joined_tables.add(new_table)
    if len(fanout) > 1:
        raise PipelineExecutionError(
            "Saved aggregations across multiple fan-out joins are not safe to execute."
        )
    if not fanout:
        return
    join, cardinality, many_table = fanout[0]
    if cardinality == "M:M":
        raise PipelineExecutionError(
            "Saved aggregations across a many-to-many join are not safe to execute."
        )
    if any(item.get("source_table") != many_table for item in aggregations):
        raise PipelineExecutionError(
            "Saved aggregations on the one-side of a fan-out join require pre-aggregation."
        )


def validate_derived_columns(
    db_type: str,
    alias_map: Mapping[str, str],
    derived_columns: Any,
) -> None:
    """Validate the restricted expression language without executing a query."""
    if not isinstance(derived_columns, list):
        raise PipelineExecutionError("Saved derived_columns must be a list.")
    resolved_aliases = dict(alias_map)
    for index, item in enumerate(derived_columns):
        if not isinstance(item, dict):
            raise PipelineExecutionError(
                "Saved derived column #%d must be an object." % (index + 1)
            )
        name = item.get("name")
        expression = item.get("expression") or item.get("expression_hint")
        if not isinstance(name, str) or not name.strip():
            raise PipelineExecutionError("Saved derived column requires a name.")
        existing_names = {
            str(alias).casefold() for alias in resolved_aliases
        } | {
            str(source).rsplit(".", 1)[-1].casefold()
            for source in resolved_aliases.values()
        }
        if name.casefold() in existing_names:
            raise PipelineExecutionError(
                "Derived column name '%s' conflicts with an input column." % name
            )
        if not isinstance(expression, str) or not expression.strip():
            raise PipelineExecutionError(
                "Saved derived column '%s' has no expression." % name
            )
        try:
            tree = ast.parse(expression, mode="eval")
            _ExpressionCompiler(db_type, resolved_aliases).visit(tree.body)
            _quote(db_type, name, "column alias")
        except (SyntaxError, SqlBuildError) as exc:
            raise PipelineExecutionError(
                "Saved derived column '%s' has invalid expression syntax." % name
            ) from exc
        resolved_aliases[name] = "derived.%s" % name


def compile_saved_pipeline_sql(
    db_type: str,
    base_sql: str,
    alias_map: Mapping[str, str],
    pipeline_json: Mapping[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """Wrap the saved join query with parameterized filters and approved logic."""
    def quote(value: str) -> str:
        return _quote(db_type, value, "column alias")
    params: Dict[str, Any] = {}
    filters = pipeline_json.get("filters", [])
    if not isinstance(filters, list):
        raise PipelineExecutionError("Saved filters must be a list.")

    where = []
    for index, item in enumerate(filters):
        if not isinstance(item, dict):
            raise PipelineExecutionError("Saved filter #%d must be an object." % (index + 1))
        operator_key = str(item.get("operator", "")).strip().lower()
        operator_key = _FILTER_OPERATOR_ALIASES.get(operator_key, operator_key)
        operator = _FILTER_OPERATORS.get(operator_key)
        if operator_key in ("is_null", "is_not_null"):
            operator = "IS NULL" if operator_key == "is_null" else "IS NOT NULL"
        if operator is None:
            raise PipelineExecutionError("Saved filter uses an unsupported operator.")
        alias = _resolve_column(
            alias_map,
            item.get("column") or item.get("column_name"),
            item.get("source_table") or item.get("table_name"),
        )
        column_sql = "src.%s" % quote(alias)
        if operator_key in ("is_null", "is_not_null"):
            where.append("%s %s" % (column_sql, operator))
            continue
        if "value" not in item or item["value"] is None:
            raise PipelineExecutionError("Saved filter requires a non-null value.")
        value = item["value"]
        if (
            not isinstance(value, (str, bool, int, float))
            or isinstance(value, float) and not math.isfinite(value)
        ):
            raise PipelineExecutionError("Saved filter value must be a finite JSON scalar.")
        parameter = "pipeline_filter_%d" % index
        params[parameter] = value
        where.append("%s %s :%s" % (column_sql, operator, parameter))

    filtered_sql = "SELECT src.* FROM (%s) AS src" % base_sql
    if where:
        filtered_sql += "\nWHERE " + " AND ".join(where)

    derived = pipeline_json.get("derived_columns", [])
    validate_derived_columns(db_type, alias_map, derived)
    for index, item in enumerate(derived):
        name = item.get("name")
        expression = item.get("expression") or item.get("expression_hint")
        if not isinstance(name, str) or not name.strip():
            raise PipelineExecutionError("Saved derived column requires a name.")
        if name.casefold() in {str(alias).casefold() for alias in alias_map}:
            raise PipelineExecutionError("Derived column name '%s' conflicts with an input column." % name)
        if not isinstance(expression, str) or not expression.strip():
            raise PipelineExecutionError("Saved derived column '%s' has no expression." % name)
        try:
            tree = ast.parse(expression, mode="eval")
            expression_sql = _ExpressionCompiler(db_type, alias_map).visit(tree.body)
        except (SyntaxError, PipelineExecutionError) as exc:
            if isinstance(exc, PipelineExecutionError):
                raise
            raise PipelineExecutionError(
                "Saved derived column '%s' has invalid expression syntax." % name
            ) from exc
        filtered_sql = (
            "SELECT derived_src.*, %s AS %s FROM (%s) AS derived_src"
            % (expression_sql.replace("src.", "derived_src."), quote(name), filtered_sql)
        )
        alias_map = dict(alias_map)
        alias_map[name] = "derived.%s" % name

    aggregations = pipeline_json.get("aggregations", [])
    group_by = pipeline_json.get("group_by", [])
    if not isinstance(aggregations, list) or not isinstance(group_by, list):
        raise PipelineExecutionError("Saved aggregations and group_by must be lists.")
    if not aggregations:
        if group_by:
            raise PipelineExecutionError("Saved group_by requires at least one aggregation.")
        return filtered_sql, params
    _validate_aggregate_join_safety(pipeline_json)

    group_columns = []
    for item in group_by:
        if not isinstance(item, dict):
            raise PipelineExecutionError("Each group_by item must be an object.")
        alias = _resolve_column(
            alias_map,
            item.get("column") or item.get("column_name"),
            item.get("source_table") or item.get("table_name"),
        )
        group_columns.append(alias)

    projection = ["agg_src.%s AS %s" % (quote(alias), quote(alias)) for alias in group_columns]
    group_sql = ["agg_src.%s" % quote(alias) for alias in group_columns]
    output_names = {alias.casefold() for alias in group_columns}
    for index, item in enumerate(aggregations):
        if not isinstance(item, dict):
            raise PipelineExecutionError("Saved aggregation #%d must be an object." % (index + 1))
        function = str(item.get("function") or item.get("aggregation") or "").upper()
        name = item.get("name") or item.get("output_name")
        if function not in _AGGREGATIONS:
            raise PipelineExecutionError("Unsupported saved aggregation function '%s'." % function)
        if not isinstance(name, str) or not name.strip():
            raise PipelineExecutionError("Saved aggregation requires an output name.")
        if name.casefold() in output_names:
            raise PipelineExecutionError(
                "Aggregation output name '%s' is duplicated." % name
            )
        output_names.add(name.casefold())
        column = item.get("source_column") or item.get("column")
        if column == "*" and function == "COUNT":
            expression = "COUNT(*)"
        else:
            alias = _resolve_column(alias_map, column, item.get("source_table"))
            qualified = "agg_src.%s" % quote(alias)
            if function == "COUNT_DISTINCT":
                expression = "COUNT(DISTINCT %s)" % qualified
            elif function == "STDDEV":
                expression = "%s(%s)" % ("STDEV" if db_type == "mssql" else "STDDEV", qualified)
            else:
                expression = "%s(%s)" % (function, qualified)
        projection.append("%s AS %s" % (expression, quote(name)))
    if not projection:
        raise PipelineExecutionError("Saved aggregation produces no output columns.")
    sql = "SELECT %s FROM (%s) AS agg_src" % (", ".join(projection), filtered_sql)
    if group_sql:
        sql += "\nGROUP BY " + ", ".join(group_sql)
    return sql, params
