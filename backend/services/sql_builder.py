"""
SQL builder for the multi-table dataset builder (Sprint 1).

Turns a set of selected tables plus a set of user-approved relationships into
ONE SELECT statement that the existing extraction pipeline can run unchanged.

    tables + approved relationships  ->  build_select()  ->  single SELECT
                                                              |
                                        db_connector.extract_to_dataframe()

DESIGN CONSTRAINTS (deliberate, do not "fix" these):

  * Pure module. Standard library only. No FastAPI, no SQLAlchemy, no pandas,
    no ORM, no database handle. Every function here is a pure function of its
    arguments, so the whole module is unit-testable without a database.

  * The identifier-quoting helper is re-implemented locally rather than
    imported from services.db_connector. Importing that module would drag in
    SQLAlchemy and pandas and destroy the property above. The duplication is
    six lines and is covered by tests in both files.

  * Every emitted statement starts with SELECT and contains no semicolon, so
    it passes services.db_connector._validate_query() unchanged. That is
    asserted before any statement is returned - see _assert_extract_safe().

  * EVERY output column is aliased, and aliases are unique across the whole
    result set. Two columns called "id" would make pandas return a DataFrame
    (not a Series) for df[target], and float(df[target].var()) would raise
    inside project creation. This is the single most important invariant here.

JOIN SEMANTICS:

  join_type describes how the NEW table attaches to the chain built so far -
  it is NOT a property of the edge's direction.

      inner  ->  keep only rows that match in the new table
      left   ->  keep every row accumulated so far, matched or not

  The base table's rows are the ML rows, so this is the reading that matches
  user intent. It also makes edge direction irrelevant to the planner:
  "Orders -> Customers" and "Customers -> Orders" produce identical SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POSTGRESQL = "postgresql"
MSSQL = "mssql"
SUPPORTED_DB_TYPES = (POSTGRESQL, MSSQL)

# Mirrors services.db_connector.DEFAULT_SCHEMA. Duplicated to keep this module
# dependency-free; the router passes an explicit schema in practice.
DEFAULT_SCHEMA = {POSTGRESQL: "public", MSSQL: "dbo"}

INNER = "inner"
LEFT = "left"
SUPPORTED_JOIN_TYPES = (INNER, LEFT)

_SQL_JOIN_KEYWORD = {INNER: "INNER JOIN", LEFT: "LEFT JOIN"}

# Sprint 1 cap. Raising this is a product decision, not a technical one: the
# row-explosion risk grows multiplicatively with every additional table.
MAX_TABLES = 3

# Identifiers come from database introspection, but the request body is
# client-controlled, so they are validated before they reach a quoted string.
_FORBIDDEN_IDENTIFIER_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_IDENTIFIER_LENGTH = 128

_NON_ALIAS_CHARS = re.compile(r"[^a-z0-9_]+")
_REPEATED_UNDERSCORE = re.compile(r"_{2,}")


class SqlBuildError(Exception):
    """Raised when a join specification cannot be compiled into valid SQL.

    The message is shown to the end user as an HTTP 400 detail, so it must be
    readable and must never contain credentials or a connection string.
    """


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JoinEdge:
    """One user-approved relationship between two selected tables.

    join_type is applied to whichever table is attached second - see the
    module docstring. Both orientations of the same edge are equivalent.
    """

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    join_type: str = INNER

    def endpoints(self) -> Tuple[str, str]:
        return (self.left_table, self.right_table)

    def column_for(self, table: str) -> str:
        """The column this edge uses on the given side."""
        if table == self.left_table:
            return self.left_column
        if table == self.right_table:
            return self.right_column
        raise SqlBuildError(
            f"'{table}' is not an endpoint of the relationship "
            f"{self.left_table}.{self.left_column} = "
            f"{self.right_table}.{self.right_column}."
        )

    def other_side(self, table: str) -> str:
        if table == self.left_table:
            return self.right_table
        if table == self.right_table:
            return self.left_table
        raise SqlBuildError(f"'{table}' is not an endpoint of this relationship.")

    def describe(self) -> str:
        return (
            f"{self.left_table}.{self.left_column} = "
            f"{self.right_table}.{self.right_column}"
        )


@dataclass
class BuildResult:
    """Everything the router needs to run the query and explain the result."""

    sql: str
    # alias -> "Table.column". Insertion order IS the SELECT order.
    alias_map: Dict[str, str] = field(default_factory=dict)
    # source table name -> SQL alias ("Customers" -> "t0")
    table_aliases: Dict[str, str] = field(default_factory=dict)
    # edges in the order they are joined; each one attaches a new table
    join_order: List[JoinEdge] = field(default_factory=list)

    @property
    def column_names(self) -> List[str]:
        """Output column names, in SELECT order."""
        return list(self.alias_map.keys())


# ---------------------------------------------------------------------------
# Identifier quoting
# ---------------------------------------------------------------------------


def _check_db_type(db_type: str) -> str:
    normalised = (db_type or "").strip().lower()
    if normalised not in SUPPORTED_DB_TYPES:
        raise SqlBuildError(
            f"Unsupported database type '{db_type}'. "
            f"Supported: {', '.join(SUPPORTED_DB_TYPES)}."
        )
    return normalised


def _assert_safe_identifier(name: str, kind: str = "identifier") -> str:
    """Reject anything that cannot be safely represented inside quotes."""
    if name is None or not str(name).strip():
        raise SqlBuildError(f"An empty {kind} was supplied.")

    text = str(name)

    if _FORBIDDEN_IDENTIFIER_CHARS.search(text):
        raise SqlBuildError(
            f"The {kind} '{text!r}' contains control characters and cannot be used."
        )

    if len(text) > _MAX_IDENTIFIER_LENGTH:
        raise SqlBuildError(
            f"The {kind} is longer than {_MAX_IDENTIFIER_LENGTH} characters."
        )

    return text


def _quote(db_type: str, name: str, kind: str = "identifier") -> str:
    """Quote one identifier part for the given dialect.

    SQL Server uses [brackets] and escapes a closing bracket by doubling it.
    PostgreSQL uses "double quotes" and escapes a quote by doubling it.

    This mirrors services.db_connector.quote_identifier(); see the module
    docstring for why it is duplicated rather than imported.
    """
    db_type = _check_db_type(db_type)
    safe = _assert_safe_identifier(name, kind)

    if db_type == MSSQL:
        return "[" + safe.replace("]", "]]") + "]"
    return '"' + safe.replace('"', '""') + '"'


def _quote_table(db_type: str, schema: Optional[str], table: str) -> str:
    """Schema-qualified table reference, so the join never depends on the
    connection's search path."""
    quoted_table = _quote(db_type, table, "table name")
    if not schema:
        return quoted_table
    return f"{_quote(db_type, schema, 'schema name')}.{quoted_table}"


# ---------------------------------------------------------------------------
# Alias generation
# ---------------------------------------------------------------------------


def _sanitize_alias(text: str) -> str:
    """Reduce an identifier to a safe, lower-case alias fragment."""
    lowered = str(text).strip().lower()
    cleaned = _NON_ALIAS_CHARS.sub("_", lowered)
    cleaned = _REPEATED_UNDERSCORE.sub("_", cleaned).strip("_")

    if not cleaned:
        return "col"
    if cleaned[0].isdigit():
        return f"c_{cleaned}"
    return cleaned


def _assign_table_aliases(
    base_table: str,
    join_order: Sequence[JoinEdge],
) -> Dict[str, str]:
    """Map each source table to a positional SQL alias: t0, t1, t2...

    t0 is always the base table. The rest follow the join order, so the alias
    numbering matches the order tables appear in the FROM/JOIN chain.
    """
    aliases: Dict[str, str] = {base_table: "t0"}
    index = 1

    for edge in join_order:
        # join_order is normalised so right_table is the newly attached one.
        new_table = edge.right_table
        if new_table not in aliases:
            aliases[new_table] = f"t{index}"
            index += 1

    return aliases


def _assign_column_aliases(
    tables_in_order: Sequence[str],
    columns_by_table: Dict[str, List[str]],
) -> Dict[Tuple[str, str], str]:
    """Give every (table, column) pair a unique output alias.

    Three passes:
      1. Count how often each lower-cased column name occurs across ALL the
         selected tables.
      2. A name that occurs once keeps its own name; a name that collides is
         prefixed with its table name.
      3. Anything still colliding after sanitisation gets a _2, _3 suffix.

    This is the invariant that protects _build_dataset_profile() and
    df[target].var() downstream.
    """
    occurrences: Dict[str, int] = {}
    for table in tables_in_order:
        for column in columns_by_table.get(table, []):
            key = str(column).strip().lower()
            occurrences[key] = occurrences.get(key, 0) + 1

    assigned: Dict[Tuple[str, str], str] = {}
    used: Set[str] = set()

    for table in tables_in_order:
        for column in columns_by_table.get(table, []):
            lowered = str(column).strip().lower()

            if occurrences.get(lowered, 0) > 1:
                candidate = f"{_sanitize_alias(table)}_{_sanitize_alias(column)}"
            else:
                candidate = _sanitize_alias(column)

            # Pass 3: guarantee global uniqueness.
            final = candidate
            suffix = 2
            while final in used:
                final = f"{candidate}_{suffix}"
                suffix += 1

            used.add(final)
            assigned[(table, str(column))] = final

    return assigned


# ---------------------------------------------------------------------------
# Graph validation and planning
# ---------------------------------------------------------------------------


def _normalise_join_type(join_type: str) -> str:
    normalised = (join_type or INNER).strip().lower()
    if normalised not in SUPPORTED_JOIN_TYPES:
        raise SqlBuildError(
            f"Unsupported join type '{join_type}'. "
            f"Supported: {', '.join(SUPPORTED_JOIN_TYPES)}."
        )
    return normalised


def _validate_graph(
    base_table: str,
    tables: Sequence[str],
    relationships: Sequence[JoinEdge],
    columns_by_table: Optional[Dict[str, List[str]]] = None,
    max_tables: int = MAX_TABLES,
) -> None:
    """Reject any specification that cannot compile into a correct join.

    Checks run in a fixed order so error messages are deterministic and the
    user is told about the most fundamental problem first.
    """
    # --- 1. table list ---
    if not tables:
        raise SqlBuildError("Select at least one table.")

    seen: Set[str] = set()
    for table in tables:
        _assert_safe_identifier(table, "table name")
        if table in seen:
            raise SqlBuildError(f"Table '{table}' is selected more than once.")
        seen.add(table)

    if len(tables) > max_tables:
        raise SqlBuildError(
            f"{len(tables)} tables selected. A maximum of {max_tables} tables "
            f"can be combined at once."
        )

    # --- 2. base table ---
    _assert_safe_identifier(base_table, "base table name")
    if base_table not in seen:
        raise SqlBuildError(
            f"The base table '{base_table}' is not in the selected tables."
        )

    # --- 3. per-edge validity ---
    for edge in relationships:
        _normalise_join_type(edge.join_type)

        for table in edge.endpoints():
            if table not in seen:
                raise SqlBuildError(
                    f"The relationship {edge.describe()} refers to '{table}', "
                    f"which is not one of the selected tables."
                )

        _assert_safe_identifier(edge.left_column, "column name")
        _assert_safe_identifier(edge.right_column, "column name")

        if edge.left_table == edge.right_table:
            raise SqlBuildError(
                f"'{edge.left_table}' cannot be joined to itself. "
                f"Self-referencing relationships are not supported."
            )

        if columns_by_table is not None:
            for table, column in (
                (edge.left_table, edge.left_column),
                (edge.right_table, edge.right_column),
            ):
                available = {str(c).lower() for c in columns_by_table.get(table, [])}
                if available and str(column).lower() not in available:
                    raise SqlBuildError(
                        f"Column '{column}' does not exist in '{table}'."
                    )

    # --- 4. duplicate edges (same unordered column pair) ---
    pair_keys: Set[frozenset] = set()
    for edge in relationships:
        key = frozenset(
            {
                (edge.left_table, str(edge.left_column).lower()),
                (edge.right_table, str(edge.right_column).lower()),
            }
        )
        if key in pair_keys:
            raise SqlBuildError(
                f"The relationship {edge.describe()} is defined more than once."
            )
        pair_keys.add(key)

    # --- 5. cycles, via union-find ---
    parent: Dict[str, str] = {table: table for table in tables}

    def _find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for edge in relationships:
        root_left = _find(edge.left_table)
        root_right = _find(edge.right_table)
        if root_left == root_right:
            raise SqlBuildError(
                f"The relationship {edge.describe()} creates a loop between "
                f"tables that are already connected. Remove one relationship."
            )
        parent[root_left] = root_right

    # --- 6. connectivity ---
    base_root = _find(base_table)
    unreachable = sorted(t for t in tables if _find(t) != base_root)
    if unreachable:
        joined = ", ".join(f"'{t}'" for t in unreachable)
        raise SqlBuildError(
            f"{joined} {'is' if len(unreachable) == 1 else 'are'} not connected "
            f"to '{base_table}'. Add a relationship, or remove "
            f"{'that table' if len(unreachable) == 1 else 'those tables'}."
        )


def _plan_join_order(
    base_table: str,
    relationships: Sequence[JoinEdge],
) -> List[JoinEdge]:
    """Breadth-first walk outward from the base table.

    Returns edges normalised so that:
        left_table  is already in the FROM/JOIN chain
        right_table is the table being attached by this edge

    That guarantees every emitted JOIN's ON clause references only aliases
    that already exist at that point in the statement.
    """
    adjacency: Dict[str, List[JoinEdge]] = {}
    for edge in relationships:
        adjacency.setdefault(edge.left_table, []).append(edge)
        adjacency.setdefault(edge.right_table, []).append(edge)

    visited: Set[str] = {base_table}
    queue: List[str] = [base_table]
    ordered: List[JoinEdge] = []
    consumed: Set[int] = set()

    while queue:
        current = queue.pop(0)

        for edge in adjacency.get(current, []):
            if id(edge) in consumed:
                continue

            neighbour = edge.other_side(current)
            if neighbour in visited:
                continue

            consumed.add(id(edge))
            visited.add(neighbour)
            queue.append(neighbour)

            # Normalise: the already-joined side becomes the left side.
            # join_type is preserved and reinterpreted as "how the NEW table
            # attaches", so flipping the edge never changes semantics.
            ordered.append(
                JoinEdge(
                    left_table=current,
                    left_column=edge.column_for(current),
                    right_table=neighbour,
                    right_column=edge.column_for(neighbour),
                    join_type=_normalise_join_type(edge.join_type),
                )
            )

    return ordered


# ---------------------------------------------------------------------------
# Statement assembly
# ---------------------------------------------------------------------------


def _assert_extract_safe(sql: str) -> str:
    """Guarantee the statement survives db_connector._validate_query().

    SELECT and WITH statements are allowed. Semicolons are still forbidden.
    """
    if ";" in sql:
        raise SqlBuildError(
            "The generated SQL contains a semicolon and would be rejected. "
            "This is a bug in the query builder."
        )

    stripped = sql.lstrip().upper()

    if not (stripped.startswith("SELECT") or stripped.startswith("WITH")):
        raise SqlBuildError(
            "The generated SQL does not start with SELECT or WITH. "
            "This is a bug in the query builder."
        )

    return sql


def _build_from_clause(
    db_type: str,
    schema: Optional[str],
    base_table: str,
    join_order: Sequence[JoinEdge],
    table_aliases: Dict[str, str],
) -> List[str]:
    """FROM + JOIN lines, shared by build_select and build_count_select."""
    lines = [
        f"FROM {_quote_table(db_type, schema, base_table)} "
        f"AS {table_aliases[base_table]}"
    ]

    for edge in join_order:
        keyword = _SQL_JOIN_KEYWORD[_normalise_join_type(edge.join_type)]
        left_alias = table_aliases[edge.left_table]
        right_alias = table_aliases[edge.right_table]

        lines.append(
            f"{keyword} {_quote_table(db_type, schema, edge.right_table)} "
            f"AS {right_alias} "
            f"ON {left_alias}.{_quote(db_type, edge.left_column, 'column name')}"
            f" = "
            f"{right_alias}.{_quote(db_type, edge.right_column, 'column name')}"
        )

    return lines


def _tables_in_select_order(
    base_table: str,
    join_order: Sequence[JoinEdge],
) -> List[str]:
    ordered = [base_table]
    for edge in join_order:
        if edge.right_table not in ordered:
            ordered.append(edge.right_table)
    return ordered


def build_select(
    db_type: str,
    base_table: str,
    tables: Sequence[str],
    relationships: Sequence[JoinEdge],
    columns_by_table: Dict[str, List[str]],
    schema: Optional[str] = None,
    limit: Optional[int] = None,
    max_tables: int = MAX_TABLES,
) -> BuildResult:
    """Compile a join specification into one aliased SELECT statement.

    Args:
        db_type:          "mssql" or "postgresql".
        base_table:       the table whose rows become the dataset's rows.
        tables:           every selected table, including base_table.
        relationships:    user-APPROVED edges only. Nothing else reaches SQL.
        columns_by_table: {table: [column names]} in output order. To project
                          a subset of columns, pass a filtered dict - this
                          function projects exactly what it is given.
        schema:           schema for every table; defaults per dialect.
        limit:            row cap. Emitted as TOP n (mssql) or LIMIT n (pg).
        max_tables:       override the Sprint 1 cap in tests.

    Returns:
        BuildResult with the SQL, the alias maps and the planned join order.

    Raises:
        SqlBuildError on any invalid specification. The message is user-safe.
    """
    db_type = _check_db_type(db_type)
    resolved_schema = schema if schema is not None else DEFAULT_SCHEMA[db_type]

    if not columns_by_table:
        raise SqlBuildError("No columns were supplied for the selected tables.")

    _validate_graph(
        base_table=base_table,
        tables=tables,
        relationships=relationships,
        columns_by_table=columns_by_table,
        max_tables=max_tables,
    )

    join_order = _plan_join_order(base_table, relationships)
    table_aliases = _assign_table_aliases(base_table, join_order)
    tables_in_order = _tables_in_select_order(base_table, join_order)

    for table in tables_in_order:
        if not columns_by_table.get(table):
            raise SqlBuildError(f"No columns were supplied for table '{table}'.")

    column_aliases = _assign_column_aliases(tables_in_order, columns_by_table)

    # --- SELECT list ---
    select_parts: List[str] = []
    alias_map: Dict[str, str] = {}

    for table in tables_in_order:
        table_alias = table_aliases[table]
        for column in columns_by_table[table]:
            output_alias = column_aliases[(table, str(column))]
            select_parts.append(
                f"{table_alias}.{_quote(db_type, column, 'column name')} "
                f"AS {_quote(db_type, output_alias, 'column alias')}"
            )
            alias_map[output_alias] = f"{table}.{column}"

    if not select_parts:
        raise SqlBuildError("The generated query would select no columns.")

    # --- row limiting: dialect split ---
    top_clause = ""
    tail_clause = ""
    if limit is not None:
        if int(limit) <= 0:
            raise SqlBuildError("The row limit must be greater than zero.")
        if db_type == MSSQL:
            top_clause = f"TOP {int(limit)} "
        else:
            tail_clause = f"\nLIMIT {int(limit)}"

    lines = [f"SELECT {top_clause}" + ", ".join(select_parts)]
    lines.extend(
        _build_from_clause(
            db_type, resolved_schema, base_table, join_order, table_aliases
        )
    )

    sql = _assert_extract_safe("\n".join(lines) + tail_clause)

    return BuildResult(
        sql=sql,
        alias_map=alias_map,
        table_aliases=table_aliases,
        join_order=join_order,
    )


def build_aggregate_select(
    db_type: str,
    table: str,
    group_by: Sequence[str],
    aggregates: Sequence[Tuple[str, str, str]],
    filters: Optional[Sequence[Tuple[str, str, Any]]] = None,
    schema: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Build one safe aggregate SELECT statement.

    Args:
        db_type:
            "mssql" or "postgresql".

        table:
            Source table.

        group_by:
            Columns to group by.

        aggregates:
            Tuples of:
                (output_alias, aggregate_function, source_column)

            Example:
                ("total_sales", "SUM", "OrderAmount")

        filters:
            Optional tuples of:
                (column, operator, value)

            Values are returned separately as bound parameters.

        schema:
            Optional database schema.

    Returns:
        (sql, parameters)

    Example SQL:
        SELECT
            [CustomerID] AS [CustomerID],
            SUM([OrderAmount]) AS [total_sales]
        FROM [dbo].[Orders]
        GROUP BY [CustomerID]
    """

    db_type = _check_db_type(db_type)
    resolved_schema = schema if schema is not None else DEFAULT_SCHEMA[db_type]

    _assert_safe_identifier(table, "table name")

    allowed_aggregates = {
        "SUM",
        "AVG",
        "MIN",
        "MAX",
        "COUNT",
        "COUNT_DISTINCT",
    }

    if not group_by and not aggregates:
        raise SqlBuildError(
            "An aggregate query requires at least one group-by column " "or aggregate."
        )

    # ---------------------------------------------------------------
    # GROUP BY columns
    # ---------------------------------------------------------------

    group_parts: List[str] = []

    for column in group_by:
        _assert_safe_identifier(column, "column name")

        group_parts.append(_quote(db_type, column, "column name"))

    # ---------------------------------------------------------------
    # Aggregate expressions
    # ---------------------------------------------------------------

    select_parts: List[str] = []

    for item in aggregates:
        if len(item) != 3:
            raise SqlBuildError(
                "Each aggregate must contain "
                "(output_alias, function, source_column)."
            )

        output_alias, function_name, source_column = item

        _assert_safe_identifier(output_alias, "column alias")

        function_name = str(function_name).strip().upper()

        if function_name not in allowed_aggregates:
            raise SqlBuildError(
                f"Unsupported aggregate function '{function_name}'. "
                f"Supported: {', '.join(sorted(allowed_aggregates))}."
            )

        if source_column == "*":
            if function_name != "COUNT":
                raise SqlBuildError("Only COUNT can aggregate over '*'.")

            expression = "COUNT(*)"

        elif function_name == "COUNT_DISTINCT":
            _assert_safe_identifier(source_column, "column name")

            expression = (
                f"COUNT(DISTINCT " f"{_quote(db_type, source_column, 'column name')})"
            )

        else:
            _assert_safe_identifier(source_column, "column name")

            expression = (
                f"{function_name}("
                f"{_quote(db_type, source_column, 'column name')}"
                f")"
            )

        select_parts.append(
            f"{expression} AS " f"{_quote(db_type, output_alias, 'column alias')}"
        )

    if not select_parts and not group_parts:
        raise SqlBuildError("The generated aggregate query would select no columns.")

    # ---------------------------------------------------------------
    # SELECT projection
    # ---------------------------------------------------------------

    projection = group_parts + select_parts

    # ---------------------------------------------------------------
    # WHERE filters
    # ---------------------------------------------------------------

    parameters: Dict[str, Any] = {}
    where_parts: List[str] = []

    allowed_operators = {
        "=",
        "!=",
        "<>",
        ">",
        ">=",
        "<",
        "<=",
        "LIKE",
    }

    for index, item in enumerate(filters or []):
        if len(item) != 3:
            raise SqlBuildError(
                "Each filter must contain " "(column, operator, value)."
            )

        column, operator, value = item

        _assert_safe_identifier(column, "column name")

        operator = str(operator).strip().upper()

        if operator not in allowed_operators:
            raise SqlBuildError(f"Unsupported filter operator '{operator}'.")

        parameter_name = f"p{index}"

        where_parts.append(
            f"{_quote(db_type, column, 'column name')} " f"{operator} :{parameter_name}"
        )

        parameters[parameter_name] = value

    # ---------------------------------------------------------------
    # Final SQL
    # ---------------------------------------------------------------

    lines = [
        "SELECT " + ", ".join(projection),
        f"FROM {_quote_table(db_type, resolved_schema, table)}",
    ]

    if where_parts:
        lines.append("WHERE " + " AND ".join(where_parts))

    if group_parts:
        lines.append("GROUP BY " + ", ".join(group_parts))

    sql = _assert_extract_safe("\n".join(lines))

    return sql, parameters


def build_count_select(
    db_type: str,
    base_table: str,
    tables: Sequence[str],
    relationships: Sequence[JoinEdge],
    columns_by_table: Optional[Dict[str, List[str]]] = None,
    schema: Optional[str] = None,
    max_tables: int = MAX_TABLES,
) -> str:
    """COUNT(*) over the identical FROM/JOIN chain build_select() would emit.

    The preview endpoint runs this BEFORE fetching rows, so a fan-out join is
    reported to the user at preview time instead of failing at extract time
    against db_connector.MAX_ROWS.

    columns_by_table is optional here - no columns are projected - but pass it
    when available so column-existence errors surface identically.
    """
    db_type = _check_db_type(db_type)
    resolved_schema = schema if schema is not None else DEFAULT_SCHEMA[db_type]

    _validate_graph(
        base_table=base_table,
        tables=tables,
        relationships=relationships,
        columns_by_table=columns_by_table,
        max_tables=max_tables,
    )

    join_order = _plan_join_order(base_table, relationships)
    table_aliases = _assign_table_aliases(base_table, join_order)

    lines = [f"SELECT COUNT(*) AS {_quote(db_type, 'row_count', 'column alias')}"]
    lines.extend(
        _build_from_clause(
            db_type, resolved_schema, base_table, join_order, table_aliases
        )
    )

    return _assert_extract_safe("\n".join(lines))


# ---------------------------------------------------------------------------
# Convenience: build JoinEdge objects from plain dicts (router boundary)
# ---------------------------------------------------------------------------


def edges_from_dicts(raw: Sequence[Dict[str, Any]]) -> List[JoinEdge]:
    """Convert request-body dicts into JoinEdge objects.

    Keeps the router free of dataclass construction details and gives one
    place where a malformed relationship payload is rejected.
    """
    edges: List[JoinEdge] = []

    for index, item in enumerate(raw or []):
        try:
            edges.append(
                JoinEdge(
                    left_table=str(item["left_table"]),
                    left_column=str(item["left_column"]),
                    right_table=str(item["right_table"]),
                    right_column=str(item["right_column"]),
                    join_type=_normalise_join_type(item.get("join_type", INNER)),
                )
            )
        except SqlBuildError:
            raise
        except (KeyError, TypeError) as exc:
            raise SqlBuildError(
                f"Relationship #{index + 1} is missing required fields "
                f"(left_table, left_column, right_table, right_column)."
            ) from exc

    return edges
