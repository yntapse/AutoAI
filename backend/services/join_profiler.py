"""
Join profiler for the multi-table dataset builder (Sprint 1 - Join Advisor).

Answers one question with real data instead of asking the user to know SQL:

    "If I connect these two columns, what actually happens to my rows?"

It does that in three steps, each a pure function:

    build_profile_query()  ->  ONE SELECT that measures the candidate pair
    profile_from_row()     ->  the returned row, as a JoinProfile
    recommend()            ->  a plain-language INNER/LEFT recommendation

DESIGN CONSTRAINTS (deliberate, do not "fix" these):

  * Pure module. Standard library only. No FastAPI, no SQLAlchemy, no pandas,
    no database handle, no environment variables, no file I/O. Every function
    is a pure function of its arguments, so the whole module is unit-testable
    without a database.

  * The identifier-quoting helper is re-implemented locally rather than
    imported from services.db_connector or services.sql_builder. Importing
    either would drag in SQLAlchemy and pandas and destroy the property above.
    The duplication is a few lines and is covered by tests in each file.

  * The emitted statement starts with SELECT and contains no semicolon, so it
    passes services.db_connector._validate_query() unchanged and inherits the
    existing read-only, timeout and row-cap protections for free.

  * NOTHING here decides anything on the user's behalf. recommend() returns a
    recommendation and the evidence behind it. The caller still presents both
    join types and the user still chooses. No LLM is involved: every number is
    a COUNT and every verdict follows the fixed rule table in recommend().

MEASUREMENT MODEL:

    base table B, key column a        (the base table's rows become ML rows)
    related table R, key column b

    INNER JOIN result = inner_join_rows
    LEFT  JOIN result = inner_join_rows + base rows with no match

  A row whose key is NULL never matches anything, so it counts as unmatched
  under both readings - it is dropped by INNER and kept (with blanks) by LEFT.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional

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

ONE_TO_ONE = "one_to_one"
ONE_TO_MANY = "one_to_many"
MANY_TO_ONE = "many_to_one"
MANY_TO_MANY = "many_to_many"

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_DANGER = "danger"

# The ten measurements the profile query returns, in SELECT order.
PROFILE_COLUMNS = (
    "base_rows",
    "related_rows",
    "base_null_keys",
    "related_null_keys",
    "base_distinct_keys",
    "related_distinct_keys",
    "base_matched_rows",
    "related_matched_rows",
    "inner_join_rows",
    "max_rows_per_key",
)

# Identifiers come from database introspection, but the request body is
# client-controlled, so they are validated before they reach a quoted string.
_FORBIDDEN_IDENTIFIER_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_IDENTIFIER_LENGTH = 128


class JoinProfileError(Exception):
    """Raised when a candidate relationship cannot be profiled.

    The message is shown to the end user as an HTTP 400 detail, so it must be
    readable and must never contain credentials or a connection string.
    """


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class JoinProfile:
    """What the data actually says about one candidate relationship.

    The first ten fields are measured by the database. The rest are derived
    arithmetically in profile_from_row() - no second query.
    """

    # --- measured ---
    base_rows: int
    related_rows: int
    base_matched_rows: int
    related_matched_rows: int
    base_null_keys: int
    related_null_keys: int
    base_distinct_keys: int
    related_distinct_keys: int
    max_rows_per_key: int

    # --- derived ---
    base_unmatched_rows: int
    related_unmatched_rows: int
    base_key_unique: bool
    related_key_unique: bool
    match_percentage: float
    cardinality: str
    inner_result_rows: int
    left_result_rows: int
    row_multiplication: bool


@dataclass
class JoinRecommendation:
    """A suggestion plus the evidence for it. Never applied automatically."""

    recommended: Optional[str]  # "inner" | "left" | None
    severity: str  # "info" | "warning" | "danger"
    headline: str
    reasons: List[str]


# ---------------------------------------------------------------------------
# Identifier quoting
# ---------------------------------------------------------------------------


def _check_db_type(db_type: str) -> str:
    normalised = (db_type or "").strip().lower()
    if normalised not in SUPPORTED_DB_TYPES:
        raise JoinProfileError(
            f"Unsupported database type '{db_type}'. "
            f"Supported: {', '.join(SUPPORTED_DB_TYPES)}."
        )
    return normalised


def _assert_safe_identifier(name: str, kind: str = "identifier") -> str:
    """Reject anything that cannot be safely represented inside quotes."""
    if name is None or not str(name).strip():
        raise JoinProfileError(f"An empty {kind} was supplied.")

    text = str(name)

    if _FORBIDDEN_IDENTIFIER_CHARS.search(text):
        raise JoinProfileError(
            f"The {kind} contains control characters and cannot be used."
        )

    if len(text) > _MAX_IDENTIFIER_LENGTH:
        raise JoinProfileError(
            f"The {kind} is longer than {_MAX_IDENTIFIER_LENGTH} characters."
        )

    return text


def _quote(db_type: str, name: str, kind: str = "identifier") -> str:
    """Quote one identifier part for the given dialect.

    SQL Server uses [brackets] and escapes a closing bracket by doubling it.
    PostgreSQL uses "double quotes" and escapes a quote by doubling it.
    """
    db_type = _check_db_type(db_type)
    safe = _assert_safe_identifier(name, kind)

    if db_type == MSSQL:
        return "[" + safe.replace("]", "]]") + "]"
    return '"' + safe.replace('"', '""') + '"'


def _quote_table(db_type: str, schema: Optional[str], table: str) -> str:
    """Schema-qualified table reference, so the profile never depends on the
    connection's search path."""
    quoted_table = _quote(db_type, table, "table name")
    if not schema:
        return quoted_table
    return f"{_quote(db_type, schema, 'schema name')}.{quoted_table}"


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def _assert_extract_safe(sql: str) -> str:
    """Guarantee the statement survives db_connector._validate_query().

    That function rejects anything not starting with SELECT/WITH and anything
    containing a semicolon. Failing here - rather than in the connector - gives
    a clear error naming this module as the cause.
    """
    if ";" in sql:
        raise JoinProfileError(
            "The generated profile SQL contains a semicolon and would be "
            "rejected. This is a bug in the join profiler."
        )
    if not sql.lstrip().upper().startswith("SELECT"):
        raise JoinProfileError(
            "The generated profile SQL does not start with SELECT. "
            "This is a bug in the join profiler."
        )
    return sql


def build_profile_query(
    db_type: str,
    schema: Optional[str],
    base_table: str,
    base_column: str,
    related_table: str,
    related_column: str,
) -> str:
    """Build ONE SELECT that measures a candidate relationship.

    Returns a single statement whose one row carries the ten values named in
    PROFILE_COLUMNS, in that order. Every measurement is a scalar subquery, so
    the whole profile costs one round trip.

    Args:
        db_type:        "mssql" or "postgresql".
        schema:         schema holding both tables; defaults per dialect.
        base_table:     the table whose rows become the dataset's rows.
        base_column:    the base table's key column.
        related_table:  the table being attached.
        related_column: the related table's key column.

    Raises:
        JoinProfileError on an unsupported dialect, an unsafe identifier, or a
        self-referencing relationship (out of Sprint 1 scope, and rejected the
        same way by sql_builder._validate_graph).
    """
    db_type = _check_db_type(db_type)
    resolved_schema = schema if schema is not None else DEFAULT_SCHEMA[db_type]

    _assert_safe_identifier(base_table, "base table name")
    _assert_safe_identifier(related_table, "related table name")
    _assert_safe_identifier(base_column, "base column name")
    _assert_safe_identifier(related_column, "related column name")

    if str(base_table) == str(related_table):
        raise JoinProfileError(
            f"'{base_table}' cannot be joined to itself. "
            f"Self-referencing relationships are not supported."
        )

    b_table = _quote_table(db_type, resolved_schema, base_table)
    r_table = _quote_table(db_type, resolved_schema, related_table)
    b_col = _quote(db_type, base_column, "column name")
    r_col = _quote(db_type, related_column, "column name")

    def alias(name: str) -> str:
        return _quote(db_type, name, "column alias")

    # Each subquery gets its own table alias so nothing is ambiguous, even if
    # the two tables ever shared a name.
    parts = [
        # 1. total rows on each side
        f"(SELECT COUNT(*) FROM {b_table} AS b1) AS {alias('base_rows')}",
        f"(SELECT COUNT(*) FROM {r_table} AS r1) AS {alias('related_rows')}",
        # 2. rows whose key is NULL - these never match anything
        f"(SELECT COUNT(*) FROM {b_table} AS b2 WHERE b2.{b_col} IS NULL)"
        f" AS {alias('base_null_keys')}",
        f"(SELECT COUNT(*) FROM {r_table} AS r2 WHERE r2.{r_col} IS NULL)"
        f" AS {alias('related_null_keys')}",
        # 3. distinct usable keys - drives the uniqueness test
        f"(SELECT COUNT(DISTINCT b3.{b_col}) FROM {b_table} AS b3"
        f" WHERE b3.{b_col} IS NOT NULL) AS {alias('base_distinct_keys')}",
        f"(SELECT COUNT(DISTINCT r3.{r_col}) FROM {r_table} AS r3"
        f" WHERE r3.{r_col} IS NOT NULL) AS {alias('related_distinct_keys')}",
        # 4. rows that have at least one partner on the other side
        f"(SELECT COUNT(*) FROM {b_table} AS b4 WHERE b4.{b_col} IS NOT NULL"
        f" AND EXISTS (SELECT 1 FROM {r_table} AS r4"
        f" WHERE r4.{r_col} = b4.{b_col})) AS {alias('base_matched_rows')}",
        f"(SELECT COUNT(*) FROM {r_table} AS r5 WHERE r5.{r_col} IS NOT NULL"
        f" AND EXISTS (SELECT 1 FROM {b_table} AS b5"
        f" WHERE b5.{b_col} = r5.{r_col})) AS {alias('related_matched_rows')}",
        # 5. the exact INNER JOIN size - not an estimate
        f"(SELECT COUNT(*) FROM {b_table} AS b6"
        f" INNER JOIN {r_table} AS r6 ON b6.{b_col} = r6.{r_col})"
        f" AS {alias('inner_join_rows')}",
        # 6. worst-case fan-out: the most related rows sharing one key
        f"(SELECT COALESCE(MAX(g.{_quote(db_type, 'key_count', 'column alias')}), 0)"
        f" FROM (SELECT COUNT(*) AS {_quote(db_type, 'key_count', 'column alias')}"
        f" FROM {r_table} AS r7 WHERE r7.{r_col} IS NOT NULL"
        f" GROUP BY r7.{r_col}) AS g) AS {alias('max_rows_per_key')}",
    ]

    sql = "SELECT\n  " + ",\n  ".join(parts)
    return _assert_extract_safe(sql)


# ---------------------------------------------------------------------------
# Row interpretation
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> int:
    """Coerce a database count to a plain int.

    Drivers return ints, Decimals, numpy integers or None depending on dialect
    and column type. None and anything uncoercible become 0 - a missing count
    must never crash the advisor.
    """
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def _normalise_keys(row: Mapping[str, Any]) -> dict:
    """Lower-case the row's keys.

    Some drivers return column aliases with the casing the server chose, so
    the profile is read case-insensitively.
    """
    normalised = {}
    for key, value in dict(row).items():
        normalised[str(key).strip().lower()] = value
    return normalised


def profile_from_row(row: Mapping[str, Any]) -> JoinProfile:
    """Turn one profile-query row into a JoinProfile.

    Handles NULLs, Decimals and missing keys, derives every secondary figure,
    and never divides by zero.

    Raises:
        JoinProfileError if the row carries none of the expected columns,
        which means the query and this reader have drifted apart.
    """
    if row is None:
        raise JoinProfileError("The profile query returned no row.")

    data = _normalise_keys(row)

    if not any(column in data for column in PROFILE_COLUMNS):
        raise JoinProfileError(
            "The profile query result did not contain the expected columns. "
            "This is a bug in the join profiler."
        )

    base_rows = _as_int(data.get("base_rows"))
    related_rows = _as_int(data.get("related_rows"))
    base_null_keys = _as_int(data.get("base_null_keys"))
    related_null_keys = _as_int(data.get("related_null_keys"))
    base_distinct_keys = _as_int(data.get("base_distinct_keys"))
    related_distinct_keys = _as_int(data.get("related_distinct_keys"))
    base_matched_rows = _as_int(data.get("base_matched_rows"))
    related_matched_rows = _as_int(data.get("related_matched_rows"))
    inner_join_rows = _as_int(data.get("inner_join_rows"))
    max_rows_per_key = _as_int(data.get("max_rows_per_key"))

    # A matched count can never exceed the table's own row count; clamp rather
    # than produce a negative "unmatched" figure if a driver surprises us.
    base_matched_rows = min(base_matched_rows, base_rows)
    related_matched_rows = min(related_matched_rows, related_rows)

    base_unmatched_rows = max(0, base_rows - base_matched_rows)
    related_unmatched_rows = max(0, related_rows - related_matched_rows)

    match_percentage = (
        round(base_matched_rows / base_rows * 100.0, 2) if base_rows > 0 else 0.0
    )

    # A key is "unique" only if there is at least one usable value AND every
    # usable value appears exactly once. All-NULL columns are not unique: they
    # carry no information, and rule 1 in recommend() catches that case first.
    base_usable = max(0, base_rows - base_null_keys)
    related_usable = max(0, related_rows - related_null_keys)
    base_key_unique = base_usable > 0 and base_distinct_keys == base_usable
    related_key_unique = related_usable > 0 and related_distinct_keys == related_usable

    if base_key_unique and related_key_unique:
        cardinality = ONE_TO_ONE
    elif base_key_unique and not related_key_unique:
        # One base row can attract many related rows.
        cardinality = ONE_TO_MANY
    elif not base_key_unique and related_key_unique:
        # Many base rows point at the same related row - row count is safe.
        cardinality = MANY_TO_ONE
    else:
        cardinality = MANY_TO_MANY

    inner_result_rows = inner_join_rows
    left_result_rows = inner_join_rows + base_unmatched_rows

    # The join multiplies rows when the INNER result is larger than the number
    # of base rows that took part in it.
    row_multiplication = inner_join_rows > base_matched_rows

    return JoinProfile(
        base_rows=base_rows,
        related_rows=related_rows,
        base_matched_rows=base_matched_rows,
        related_matched_rows=related_matched_rows,
        base_null_keys=base_null_keys,
        related_null_keys=related_null_keys,
        base_distinct_keys=base_distinct_keys,
        related_distinct_keys=related_distinct_keys,
        max_rows_per_key=max_rows_per_key,
        base_unmatched_rows=base_unmatched_rows,
        related_unmatched_rows=related_unmatched_rows,
        base_key_unique=base_key_unique,
        related_key_unique=related_key_unique,
        match_percentage=match_percentage,
        cardinality=cardinality,
        inner_result_rows=inner_result_rows,
        left_result_rows=left_result_rows,
        row_multiplication=row_multiplication,
    )


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------


def _n(value: int) -> str:
    """Thousands-separated number, for sentences a non-technical user reads."""
    return f"{int(value):,}"


def _null_key_note(profile: JoinProfile, base_table: str) -> Optional[str]:
    """Explain empty key values, which are invisible but change the result."""
    if profile.base_null_keys <= 0:
        return None
    return (
        f"{_n(profile.base_null_keys)} {base_table} "
        f"{'record has' if profile.base_null_keys == 1 else 'records have'} "
        f"an empty value in the selected column. Empty values never match, so "
        f"those records are dropped by INNER JOIN and kept with blank related "
        f"columns by LEFT JOIN."
    )


def _effect_lines(profile: JoinProfile, base_table: str) -> List[str]:
    """The two sentences that always close a recommendation."""
    return [
        f"LEFT JOIN keeps all {_n(profile.base_rows)} {base_table} records "
        f"(result: about {_n(profile.left_result_rows)} rows).",
        f"INNER JOIN keeps only the matching ones "
        f"(result: about {_n(profile.inner_result_rows)} rows).",
    ]


def recommend(
    profile: JoinProfile,
    base_table: str,
    related_table: str,
    row_limit: int,
) -> JoinRecommendation:
    """Turn a JoinProfile into a plain-language recommendation.

    The rules are fixed and evaluated in priority order - the first match
    wins. No LLM, no heuristic scoring: the same numbers always produce the
    same advice, and every sentence cites a figure the user can check.

    Args:
        profile:       the measured JoinProfile.
        base_table:    the base table's name, used in every sentence.
        related_table: the related table's name.
        row_limit:     the dataset row cap (db_connector.MAX_ROWS).

    Returns:
        JoinRecommendation. `recommended` is None only when neither join type
        is sensible, which means the chosen columns are probably not related.
    """
    base_table = str(base_table)
    related_table = str(related_table)
    null_note = _null_key_note(profile, base_table)

    # --- Rule 1: nothing matches at all -----------------------------------
    if profile.base_matched_rows == 0:
        reasons = [
            f"None of the {_n(profile.base_rows)} {base_table} records have a "
            f"matching record in {related_table}.",
            f"An INNER JOIN would return 0 rows, and a LEFT JOIN would leave "
            f"every {related_table} column blank.",
            f"The selected columns may not represent a valid relationship - "
            f"check that they hold the same kind of value.",
        ]
        if null_note:
            reasons.append(null_note)
        return JoinRecommendation(
            recommended=None,
            severity=SEVERITY_DANGER,
            headline=f"No {base_table} records match {related_table}",
            reasons=reasons,
        )

    # --- Rule 2: many-to-many ---------------------------------------------
    if profile.cardinality == MANY_TO_MANY:
        reasons = [
            f"Neither column holds unique values: {base_table} has "
            f"{_n(profile.base_distinct_keys)} distinct keys across "
            f"{_n(profile.base_rows)} records, and {related_table} has "
            f"{_n(profile.related_distinct_keys)} across "
            f"{_n(profile.related_rows)}.",
            f"This join may multiply rows significantly - about "
            f"{_n(profile.inner_result_rows)} rows from "
            f"{_n(profile.base_rows)} {base_table} records.",
            f"One key matches up to {_n(profile.max_rows_per_key)} "
            f"{related_table} records.",
            "Review this relationship before continuing. A different column "
            "pair may be the one you want.",
        ]
        if null_note:
            reasons.append(null_note)
        return JoinRecommendation(
            recommended=LEFT,
            severity=SEVERITY_DANGER,
            headline="Potential many-to-many relationship detected",
            reasons=reasons,
        )

    # --- Rule 3: the result would not fit ---------------------------------
    if profile.inner_result_rows > row_limit or profile.left_result_rows > row_limit:
        reasons = [
            f"INNER JOIN would produce about "
            f"{_n(profile.inner_result_rows)} rows and LEFT JOIN about "
            f"{_n(profile.left_result_rows)} rows.",
            f"The dataset limit is {_n(row_limit)} rows.",
            "INNER JOIN is the smaller of the two, but you will still need to "
            "reduce the data - select fewer tables, or filter the source.",
        ]
        if null_note:
            reasons.append(null_note)
        return JoinRecommendation(
            recommended=INNER,
            severity=SEVERITY_DANGER,
            headline=f"This join is too large (limit {_n(row_limit)} rows)",
            reasons=reasons,
        )

    # --- Rule 4: one-to-many, rows will multiply --------------------------
    if profile.row_multiplication:
        reasons = [
            f"Each {base_table} record can match several {related_table} "
            f"records - up to {_n(profile.max_rows_per_key)}.",
            f"This relationship may increase the dataset from "
            f"{_n(profile.base_rows)} {base_table} records to approximately "
            f"{_n(profile.left_result_rows)} rows.",
            f"Each {base_table} record will then appear more than once.",
        ]
        if profile.base_unmatched_rows > 0:
            reasons.append(
                f"{_n(profile.base_matched_rows)} of {_n(profile.base_rows)} "
                f"{base_table} records have a match; "
                f"{_n(profile.base_unmatched_rows)} do not."
            )
        reasons.extend(_effect_lines(profile, base_table))
        if null_note:
            reasons.append(null_note)
        return JoinRecommendation(
            recommended=LEFT,
            severity=SEVERITY_WARNING,
            headline="This relationship is one-to-many and will add rows",
            reasons=reasons,
        )

    # --- Rule 5: some base records have no match --------------------------
    if profile.base_unmatched_rows > 0:
        reasons = [
            f"{_n(profile.base_matched_rows)} of {_n(profile.base_rows)} "
            f"{base_table} records have a matching {related_table} record "
            f"({profile.match_percentage:g}%).",
            f"{_n(profile.base_unmatched_rows)} {base_table} "
            f"{'record has' if profile.base_unmatched_rows == 1 else 'records have'} "
            f"no matching record.",
            f"LEFT JOIN keeps all {_n(profile.base_rows)} {base_table} records. "
            f"The {related_table} columns will be blank for those "
            f"{_n(profile.base_unmatched_rows)}.",
            f"INNER JOIN keeps only the {_n(profile.base_matched_rows)} "
            f"matching records and discards the rest.",
        ]
        if null_note:
            reasons.append(null_note)
        return JoinRecommendation(
            recommended=LEFT,
            severity=SEVERITY_WARNING,
            headline=f"{_n(profile.base_unmatched_rows)} {base_table} records "
            f"have no match",
            reasons=reasons,
        )

    # --- Rule 6: every base record matches exactly once --------------------
    reasons = [
        f"All {_n(profile.base_rows)} {base_table} records have a matching "
        f"{related_table} record.",
        "No records are lost and no records are duplicated.",
        f"INNER JOIN and LEFT JOIN both produce about "
        f"{_n(profile.inner_result_rows)} rows here, so INNER JOIN is the "
        f"simpler choice.",
    ]
    if null_note:
        reasons.append(null_note)
    return JoinRecommendation(
        recommended=INNER,
        severity=SEVERITY_INFO,
        headline=f"Every {base_table} record has a match",
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Smoke test - run this file directly to see the six rules in action.
#   python -m services.join_profiler
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(
        build_profile_query(
            "mssql", "dbo", "Customers", "CustomerID", "EmployeeData", "EmployeeID"
        )
    )
    print()

    SCENARIOS = {
        "rule 6 - perfect match": dict(
            base_rows=100,
            related_rows=100,
            base_null_keys=0,
            related_null_keys=0,
            base_distinct_keys=100,
            related_distinct_keys=100,
            base_matched_rows=100,
            related_matched_rows=100,
            inner_join_rows=100,
            max_rows_per_key=1,
        ),
        "rule 5 - partial match": dict(
            base_rows=100,
            related_rows=120,
            base_null_keys=0,
            related_null_keys=0,
            base_distinct_keys=100,
            related_distinct_keys=120,
            base_matched_rows=80,
            related_matched_rows=80,
            inner_join_rows=80,
            max_rows_per_key=1,
        ),
        "rule 4 - one to many": dict(
            base_rows=100,
            related_rows=500,
            base_null_keys=0,
            related_null_keys=0,
            base_distinct_keys=100,
            related_distinct_keys=100,
            base_matched_rows=100,
            related_matched_rows=500,
            inner_join_rows=500,
            max_rows_per_key=9,
        ),
        "rule 3 - oversized": dict(
            base_rows=1000,
            related_rows=900000,
            base_null_keys=0,
            related_null_keys=0,
            base_distinct_keys=1000,
            related_distinct_keys=1000,
            base_matched_rows=1000,
            related_matched_rows=900000,
            inner_join_rows=900000,
            max_rows_per_key=1200,
        ),
        "rule 2 - many to many": dict(
            base_rows=100,
            related_rows=300,
            base_null_keys=0,
            related_null_keys=0,
            base_distinct_keys=40,
            related_distinct_keys=50,
            base_matched_rows=90,
            related_matched_rows=250,
            inner_join_rows=700,
            max_rows_per_key=12,
        ),
        "rule 1 - no match": dict(
            base_rows=100,
            related_rows=80,
            base_null_keys=5,
            related_null_keys=0,
            base_distinct_keys=95,
            related_distinct_keys=80,
            base_matched_rows=0,
            related_matched_rows=0,
            inner_join_rows=0,
            max_rows_per_key=1,
        ),
    }

    for label, row in SCENARIOS.items():
        prof = profile_from_row(row)
        rec = recommend(prof, "Customers", "EmployeeData", 50_000)
        print(f"── {label} " + "─" * (52 - len(label)))
        print(
            f"   cardinality={prof.cardinality}  match={prof.match_percentage}%"
            f"  inner={prof.inner_result_rows}  left={prof.left_result_rows}"
        )
        print(f"   [{rec.severity}] recommended={rec.recommended} :: {rec.headline}")
        for reason in rec.reasons:
            print(f"     - {reason}")
        print()
