"""DatasetSpec -> SQL. Replaces grain_compiler.py.

Pure module: no database connection, no pandas, no LLM. Mirrors the
constraint in services/sql_builder.py so the whole class is unit-testable
without a database.

FAN-OUT PREVENTION
------------------
Measures are grouped by their resolved JoinPath. Each group becomes its own
CTE, aggregated at its own grain, and only the aggregate is joined back to the
entity. A measure sourced from Orders is therefore computed in a CTE that
contains Orders and nothing else - so OrderItems cannot inflate it.

MULTI-HOP
---------
A path with more than one hop needs two aggregation levels:

    OrderItems  -> aggregate to OrderID
                -> join up to Orders to reach CustomerID
                -> aggregate again to CustomerID

Whether that is exact depends on the function:

    DECOMPOSABLE   SUM, MIN, MAX, COUNT   (COUNT's second level is SUM)
    NOT            AVG, MEDIAN, STDDEV, COUNT_DISTINCT

A non-decomposable function is aggregated ONCE over the joined path instead,
grouped by the entity key. That is safe because the CTE contains only the
path's own tables.

NEVER SILENTLY DROP A MEASURE. Every failure raises SpecCompileError.
"""

from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from services.sql_builder import (
    _assert_extract_safe,
    _assert_safe_identifier,
    _check_db_type,
    _quote,
    _quote_table,
)
from services.sql_builder import DEFAULT_SCHEMA

from etl_copilot.spec_models import (
    DECOMPOSABLE,
    SECOND_LEVEL,
    ZERO_WHEN_ABSENT,
    AggFunction,
    CompiledPlan,
    DatasetSpec,
    JoinPath,
    MeasureLineage,
    MeasureSpec,
    ReconQuery,
    SubqueryPlan,
)

_OPERATORS = {
    "eq": "=",
    "ne": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "is_null": "IS NULL",
    "is_not_null": "IS NOT NULL",
}


class SpecCompileError(Exception):
    """A spec that cannot be compiled into correct SQL. Never swallowed."""


def _path_fingerprint(path: JoinPath) -> str:
    return "|".join(
        "%s.%s=%s.%s" % (h.left_table, h.left_column, h.right_table, h.right_column)
        for h in path.hops
    )


class DatasetCompiler:
    """Compiles a validated DatasetSpec into one SQL statement."""

    def __init__(self, db_type: str, schema: Optional[str] = None):
        self.db_type = _check_db_type(db_type)
        self.schema = schema if schema is not None else DEFAULT_SCHEMA[self.db_type]

    # -- helpers ---------------------------------------------------------

    def _q(self, name: str, kind: str = "column name") -> str:
        return _quote(self.db_type, name, kind)

    def _qt(self, table: str) -> str:
        return _quote_table(self.db_type, self.schema, table)

    def _measure_expression(self, measure: MeasureSpec, alias: str) -> str:
        """The aggregate expression for one measure, qualified by `alias`."""

        function = measure.aggregation
        column = measure.source_column

        if function is AggFunction.COUNT and column is None:
            return "COUNT(*)"

        if column is None:
            raise SpecCompileError(
                "Measure '%s' (%s) has no source_column."
                % (measure.name, function.value)
            )

        _assert_safe_identifier(column, "column name")
        qualified = "%s.%s" % (alias, self._q(column))

        if function is AggFunction.COUNT_DISTINCT:
            return "COUNT(DISTINCT %s)" % qualified

        if function is AggFunction.STDDEV:
            name = "STDEV" if self.db_type == "mssql" else "STDDEV"
            return "%s(%s)" % (name, qualified)

        if function is AggFunction.MEDIAN:
            raise SpecCompileError(
                "MEDIAN is not supported by the compiler yet (measure '%s')."
                % measure.name
            )

        if function in (
            AggFunction.SUM,
            AggFunction.AVG,
            AggFunction.MIN,
            AggFunction.MAX,
            AggFunction.COUNT,
        ):
            return "%s(%s)" % (function.value, qualified)

        raise SpecCompileError(
            "Unsupported aggregation '%s' on measure '%s'."
            % (function, measure.name)
        )

    def _coalesce(self, expression: str, measure: MeasureSpec) -> str:
        """COALESCE only where a LEFT JOIN miss genuinely means zero.

        Carries over etl_agent._aggregate_rows: SUM/COUNT/COUNT_DISTINCT
        become 0; AVG/MEDIAN/STDDEV stay NULL, because an average of nothing
        is undefined and filling it with 0 feeds a false signal to training.
        """
        wants_zero = measure.zero_when_absent
        if wants_zero is None:
            wants_zero = measure.aggregation in ZERO_WHEN_ABSENT

        if wants_zero:
            return "COALESCE(%s, 0)" % expression
        return expression

    def _where(
        self,
        filters,
        alias: str,
        prefix: str,
        params: Dict[str, Any],
    ) -> List[str]:
        """Bound-parameter WHERE fragments. Values are NEVER interpolated."""

        clauses: List[str] = []

        for index, spec_filter in enumerate(filters or []):
            operator = str(spec_filter.operator).strip().lower()

            if operator not in _OPERATORS:
                raise SpecCompileError(
                    "Unsupported filter operator '%s'." % spec_filter.operator
                )

            _assert_safe_identifier(spec_filter.column, "column name")
            column = "%s.%s" % (alias, self._q(spec_filter.column))

            if operator in ("is_null", "is_not_null"):
                clauses.append("%s %s" % (column, _OPERATORS[operator]))
                continue

            key = "%s_%d" % (prefix, index)
            params[key] = spec_filter.value
            clauses.append("%s %s :%s" % (column, _OPERATORS[operator], key))

        return clauses

    # -- grouping --------------------------------------------------------

    def _group_by_path(self, spec: DatasetSpec):
        """{(source_table, path_fingerprint): (JoinPath, [measures])}"""

        by_source = {path.source_table: path for path in spec.join_paths}
        groups = OrderedDict()

        for measure in spec.measures:
            path = by_source.get(measure.source_table)

            if path is None:
                raise SpecCompileError(
                    "No join path for measure '%s' (source '%s'). A missing path "
                    "must fail, never drop the measure."
                    % (measure.name, measure.source_table)
                )

            if not path.verified:
                raise SpecCompileError(
                    "Join path to '%s' is not verified (measure '%s')."
                    % (measure.source_table, measure.name)
                )

            key = (measure.source_table, _path_fingerprint(path))
            if key not in groups:
                groups[key] = (path, [])
            groups[key][1].append(measure)

        return groups

    # -- CTE builders ----------------------------------------------------

    def _entity_sourced_cte(self, path, measures, name, params):
        """Measures sourced from the entity table itself."""
        alias = "s0"
        entity_key = self.spec_entity_key

        selects = ["%s.%s AS %s" % (alias, self._q(entity_key), self._q("entity_key"))]
        for measure in measures:
            selects.append(
                "%s AS %s"
                % (self._measure_expression(measure, alias), self._q(measure.name))
            )

        lines = [
            "SELECT %s" % ", ".join(selects),
            "FROM %s AS %s" % (self._qt(path.source_table), alias),
        ]

        where = self._where(self._measure_filters(measures), alias, name, params)
        if where:
            lines.append("WHERE %s" % " AND ".join(where))

        lines.append("GROUP BY %s.%s" % (alias, self._q(entity_key)))
        return "\n    ".join(lines)

    def _measure_filters(self, measures):
        collected = []
        for measure in measures:
            collected.extend(measure.filters or [])
        return collected

    def _single_hop_cte(self, path, measures, name, params):
        """One hop: aggregate the source table grouped by the joining column."""

        hop = path.hops[0]
        alias = "s0"

        selects = [
            "%s.%s AS %s" % (alias, self._q(hop.right_column), self._q("entity_key"))
        ]
        for measure in measures:
            selects.append(
                "%s AS %s"
                % (self._measure_expression(measure, alias), self._q(measure.name))
            )

        lines = [
            "SELECT %s" % ", ".join(selects),
            "FROM %s AS %s" % (self._qt(hop.right_table), alias),
        ]

        where = self._where(self._measure_filters(measures), alias, name, params)
        if where:
            lines.append("WHERE %s" % " AND ".join(where))

        lines.append("GROUP BY %s.%s" % (alias, self._q(hop.right_column)))
        return "\n    ".join(lines)

    def _multi_hop_single_level_cte(self, path, measures, name, params):
        """Non-decomposable functions: aggregate ONCE over the joined path.

        Safe because the CTE contains only this path's tables, so no other
        measure can be inflated by the fan-out.
        """

        aliases = {}
        lines_from = []

        # b0 is the first table after the entity; the entity key lives on it.
        for index, hop in enumerate(path.hops):
            alias = "b%d" % index
            aliases[hop.right_table] = alias

            if index == 0:
                left_alias = "b0"
                lines_from.append(
                    "FROM %s AS %s" % (self._qt(hop.right_table), alias)
                )
            else:
                left_alias = aliases[hop.left_table]
                lines_from.append(
                    "JOIN %s AS %s ON %s.%s = %s.%s"
                    % (
                        self._qt(hop.right_table),
                        alias,
                        left_alias,
                        self._q(hop.left_column),
                        alias,
                        self._q(hop.right_column),
                    )
                )

        first_hop = path.hops[0]
        key_expr = "b0.%s" % self._q(first_hop.right_column)
        source_alias = aliases[path.source_table]

        selects = ["%s AS %s" % (key_expr, self._q("entity_key"))]
        for measure in measures:
            selects.append(
                "%s AS %s"
                % (self._measure_expression(measure, source_alias), self._q(measure.name))
            )

        lines = ["SELECT %s" % ", ".join(selects)] + lines_from

        where = self._where(self._measure_filters(measures), source_alias, name, params)
        if where:
            lines.append("WHERE %s" % " AND ".join(where))

        lines.append("GROUP BY %s" % key_expr)
        return "\n    ".join(lines)

    def _multi_hop_nested_ctes(self, path, measures, base_name, params):
        """Decomposable functions across N hops: aggregate, climb, re-aggregate."""

        # Level 1: aggregate the furthest table to the column that joins it
        # to its parent.
        last_hop = path.hops[-1]
        inner_alias = "s0"
        inner_name = "%s_lvl1" % base_name

        inner_selects = [
            "%s.%s AS %s"
            % (inner_alias, self._q(last_hop.right_column), self._q("join_key"))
        ]
        for measure in measures:
            inner_selects.append(
                "%s AS %s"
                % (
                    self._measure_expression(measure, inner_alias),
                    self._q("%s__lvl1" % measure.name),
                )
            )

        inner_lines = [
            "SELECT %s" % ", ".join(inner_selects),
            "FROM %s AS %s" % (self._qt(last_hop.right_table), inner_alias),
        ]

        where = self._where(
            self._measure_filters(measures), inner_alias, inner_name, params
        )
        if where:
            inner_lines.append("WHERE %s" % " AND ".join(where))

        inner_lines.append(
            "GROUP BY %s.%s" % (inner_alias, self._q(last_hop.right_column))
        )

        inner_sql = "\n    ".join(inner_lines)

        # Level 2: climb the remaining hops (bridge tables only, nothing
        # measured there) and aggregate to the entity key.
        bridge_hops = path.hops[:-1]
        first_hop = path.hops[0]

        outer_alias = "b0"
        outer_from = [
            "FROM %s AS %s" % (self._qt(first_hop.right_table), outer_alias)
        ]

        aliases = {first_hop.right_table: outer_alias}

        for index, hop in enumerate(bridge_hops[1:], start=1):
            alias = "b%d" % index
            aliases[hop.right_table] = alias
            outer_from.append(
                "JOIN %s AS %s ON %s.%s = %s.%s"
                % (
                    self._qt(hop.right_table),
                    alias,
                    aliases[hop.left_table],
                    self._q(hop.left_column),
                    alias,
                    self._q(hop.right_column),
                )
            )

        parent_alias = aliases[last_hop.left_table]
        outer_from.append(
            "LEFT JOIN %s AS i ON %s.%s = i.%s"
            % (
                inner_name,
                parent_alias,
                self._q(last_hop.left_column),
                self._q("join_key"),
            )
        )

        key_expr = "%s.%s" % (outer_alias, self._q(first_hop.right_column))
        outer_selects = ["%s AS %s" % (key_expr, self._q("entity_key"))]

        for measure in measures:
            second = SECOND_LEVEL.get(measure.aggregation)
            if second is None:
                raise SpecCompileError(
                    "Measure '%s' (%s) cannot be decomposed across %d hops."
                    % (measure.name, measure.aggregation.value, len(path.hops))
                )

            outer_selects.append(
                "%s(i.%s) AS %s"
                % (
                    second.value,
                    self._q("%s__lvl1" % measure.name),
                    self._q(measure.name),
                )
            )

        outer_lines = ["SELECT %s" % ", ".join(outer_selects)] + outer_from
        outer_lines.append("GROUP BY %s" % key_expr)
        outer_sql = "\n    ".join(outer_lines)

        return [
            SubqueryPlan(
                name=inner_name,
                sql=inner_sql,
                source_table=last_hop.right_table,
                measure_names=[m.name for m in measures],
                level=1,
            ),
            SubqueryPlan(
                name=base_name,
                sql=outer_sql,
                source_table=path.source_table,
                measure_names=[m.name for m in measures],
                level=2,
            ),
        ]

    # -- reconciliation queries -----------------------------------------

    def _reconciliation(self, spec: DatasetSpec, params) -> List[ReconQuery]:
        queries: List[ReconQuery] = []

        entity_key = spec.entity_key[0]
        queries.append(
            ReconQuery(
                kind="entity_count",
                sql=_assert_extract_safe(
                    "SELECT COUNT(DISTINCT %s) AS %s\nFROM %s"
                    % (
                        self._q(entity_key),
                        self._q("expected_rows"),
                        self._qt(spec.entity_table),
                    )
                ),
            )
        )

        for measure in spec.measures:
            if measure.aggregation is not AggFunction.SUM:
                continue
            if measure.source_column is None:
                continue

            queries.append(
                ReconQuery(
                    kind="measure_total",
                    measure_name=measure.name,
                    sql=_assert_extract_safe(
                        "SELECT SUM(%s) AS %s\nFROM %s"
                        % (
                            self._q(measure.source_column),
                            self._q("expected_total"),
                            self._qt(measure.source_table),
                        )
                    ),
                )
            )

        return queries

    # -- lineage ---------------------------------------------------------

    def _lineage(self, spec: DatasetSpec) -> List[MeasureLineage]:
        by_source = {path.source_table: path for path in spec.join_paths}
        records: List[MeasureLineage] = []

        for measure in spec.measures:
            path = by_source.get(measure.source_table)
            hops = path.hops if path else []

            route = [spec.entity_table] + [hop.right_table for hop in hops]
            zero = measure.zero_when_absent
            if zero is None:
                zero = measure.aggregation in ZERO_WHEN_ABSENT

            nested = bool(path and path.requires_nested_aggregation
                          and measure.aggregation in DECOMPOSABLE)

            explanation = "%s of %s.%s across %s" % (
                measure.aggregation.value,
                measure.source_table,
                measure.source_column or "*",
                " -> ".join(route),
            )
            if zero:
                explanation += ". Entities with no related rows show 0."
            else:
                explanation += ". Entities with no related rows show NULL."

            records.append(
                MeasureLineage(
                    measure_name=measure.name,
                    source="%s.%s" % (measure.source_table, measure.source_column or "*"),
                    aggregation=measure.aggregation.value,
                    join_path=route,
                    path_verified=bool(path and path.verified),
                    cardinality_per_hop=[hop.cardinality or "unknown" for hop in hops],
                    zero_when_absent=bool(zero),
                    nested_aggregation=nested,
                    explanation=explanation,
                )
            )

        return records

    # -- public ----------------------------------------------------------

    def compile(self, spec: DatasetSpec) -> CompiledPlan:
        if not spec.entity_key:
            raise SpecCompileError("The spec has no entity_key.")

        self.spec_entity_key = spec.entity_key[0]
        entity_key = self.spec_entity_key
        params: Dict[str, Any] = {}

        groups = self._group_by_path(spec)

        subqueries: List[SubqueryPlan] = []
        join_lines: List[str] = []
        measure_selects: List[str] = []
        tables_referenced = [spec.entity_table]

        counter = 0

        for (source_table, _fingerprint), (path, measures) in groups.items():
            counter += 1
            cte_name = "%s_agg%d" % (source_table.lower(), counter)
            alias = "a%d" % counter

            if not path.hops:
                sql = self._entity_sourced_cte(path, measures, cte_name, params)
                subqueries.append(
                    SubqueryPlan(
                        name=cte_name,
                        sql=sql,
                        source_table=source_table,
                        measure_names=[m.name for m in measures],
                        level=1,
                    )
                )

            elif len(path.hops) == 1:
                sql = self._single_hop_cte(path, measures, cte_name, params)
                subqueries.append(
                    SubqueryPlan(
                        name=cte_name,
                        sql=sql,
                        source_table=source_table,
                        measure_names=[m.name for m in measures],
                        level=1,
                    )
                )

            else:
                decomposable = [m for m in measures if m.aggregation in DECOMPOSABLE]
                other = [m for m in measures if m.aggregation not in DECOMPOSABLE]

                if decomposable:
                    subqueries.extend(
                        self._multi_hop_nested_ctes(
                            path, decomposable, cte_name, params
                        )
                    )

                if other:
                    flat_name = "%s_flat%d" % (source_table.lower(), counter)
                    subqueries.append(
                        SubqueryPlan(
                            name=flat_name,
                            sql=self._multi_hop_single_level_cte(
                                path, other, flat_name, params
                            ),
                            source_table=source_table,
                            measure_names=[m.name for m in other],
                            level=1,
                        )
                    )
                    flat_alias = "a%df" % counter
                    join_lines.append(
                        "LEFT JOIN %s AS %s ON t0.%s = %s.%s"
                        % (
                            flat_name,
                            flat_alias,
                            self._q(entity_key),
                            flat_alias,
                            self._q("entity_key"),
                        )
                    )
                    for measure in other:
                        measure_selects.append(
                            "%s AS %s"
                            % (
                                self._coalesce(
                                    "%s.%s" % (flat_alias, self._q(measure.name)),
                                    measure,
                                ),
                                self._q(measure.name),
                            )
                        )

                if not decomposable:
                    for hop in path.hops:
                        if hop.right_table not in tables_referenced:
                            tables_referenced.append(hop.right_table)
                    continue

                measures = decomposable

            join_lines.append(
                "LEFT JOIN %s AS %s ON t0.%s = %s.%s"
                % (
                    cte_name,
                    alias,
                    self._q(entity_key),
                    alias,
                    self._q("entity_key"),
                )
            )

            for measure in measures:
                measure_selects.append(
                    "%s AS %s"
                    % (
                        self._coalesce(
                            "%s.%s" % (alias, self._q(measure.name)), measure
                        ),
                        self._q(measure.name),
                    )
                )

            for hop in path.hops:
                if hop.right_table not in tables_referenced:
                    tables_referenced.append(hop.right_table)

        # ---- outer SELECT ----
        output_columns = [entity_key]
        entity_selects = ["t0.%s AS %s" % (self._q(entity_key), self._q(entity_key))]

        for attribute in spec.attributes:
            if attribute.source_table != spec.entity_table:
                raise SpecCompileError(
                    "Attribute '%s.%s' is not on the entity table. Model it as a "
                    "measure instead." % (attribute.source_table, attribute.source_column)
                )
            alias_name = attribute.name or attribute.source_column
            _assert_safe_identifier(alias_name, "column alias")
            entity_selects.append(
                "t0.%s AS %s"
                % (self._q(attribute.source_column), self._q(alias_name))
            )
            output_columns.append(alias_name)

        for measure in spec.measures:
            output_columns.append(measure.name)

        lines = ["SELECT %s" % ", ".join(entity_selects + measure_selects)]
        lines.append("FROM %s AS t0" % self._qt(spec.entity_table))
        lines.extend(join_lines)

        entity_where = self._where(spec.filters, "t0", "entity", params)
        if entity_where:
            lines.append("WHERE %s" % " AND ".join(entity_where))

        body = "\n".join(lines)

        if subqueries:
            cte_block = ",\n".join(
                "%s AS (\n    %s\n)" % (plan.name, plan.sql) for plan in subqueries
            )
            sql = "WITH %s\n%s" % (cte_block, body)
        else:
            sql = body

        sql = _assert_extract_safe(sql)

        return CompiledPlan(
            sql=sql,
            params=params,
            output_columns=output_columns,
            declared_grain=list(spec.entity_key),
            tables_referenced=tables_referenced,
            subqueries=subqueries,
            reconciliation=self._reconciliation(spec, params),
            lineage=self._lineage(spec),
            spec_version=spec.spec_version,
        )
