"""
ETL Agent - the single agent that coordinates the ETL half of the platform.

    user request
        -> observe
        -> LLM chooses a registered tool
        -> backend executes it
        -> observe the result
        -> LLM chooses the next tool
        -> ... (max MAX_STEPS rounds)
        -> finish_etl
        -> file_id

Boundary with the rest of the platform
--------------------------------------
This agent stops at file_id. Everything after that - experiments, training,
model selection - stays with the existing AutoML agent (main.run_agent_loop),
which is not touched here.

What this module owns
---------------------
  * ETL_TOOLS, a registry of ETL-specific tools (metadata, relationship,
    build, aggregate, validate, finish)
  * the Observe/Think/Act loop
  * ETLSession, the in-memory state for ONE run (a DataFrame and a little
    bookkeeping). Nothing is persisted; there are no new database tables.

What this module deliberately does NOT own
------------------------------------------
  * row-level transformations       -> services.transform_mcp_server (13 tools,
                                       registered straight into this agent's
                                       schema, executed by its own execute_tool)
  * join SQL                        -> services.sql_builder.build_select
  * join recommendations            -> services.join_profiler (deterministic)
  * database access                 -> services.db_connector
  * CSV write + Project row         -> routers.datamodel._create_project_from_frame

None of those files are modified. They are imported and called.

Security posture (first cut - the hardening layer comes later)
--------------------------------------------------------------
  * The LLM never writes SQL or Python. It picks a registered tool name and
    JSON arguments; this module builds every statement.
  * Tool results are summaries: names, types, counts, percentages, cardinality
    statistics. No row is ever placed in a message. The DataFrame stays in
    ETLSession, which is never serialised into the conversation.
  * Connection details live in ETLSession and are never put in a message.
  * A tool never raises into the loop. Failures come back as
    {"ok": False, "error": "..."} so the agent can recover while steps remain.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from services import transform_mcp_server
from services.db_connector import (
    MAX_ROWS,
    DataSourceError,
    build_connection_string,
    default_schema,
    extract_to_dataframe,
    list_columns,
    list_foreign_keys,
    list_tables,
)
from services.join_profiler import (
    JoinProfileError,
    build_profile_query,
    profile_from_row,
    recommend,
)
from services.sql_builder import (
    MAX_TABLES,
    SqlBuildError,
    build_count_select,
    build_select,
    edges_from_dicts,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_STEPS = 10
MAX_TOOL_CALLS_PER_STEP = 3
MAX_PROFILED_COLUMNS = 60
MAX_TEXT_CHARS = 500
MAX_SCRUB_VALUES = 200   # distinct values sampled per text column
MAX_SCRUB_TOTAL = 2000   # overall cap, so the scrub stays cheap on wide data
VALUE_WITHHELD = "[value withheld]"
LLM_TEMPERATURE = 0.0
LLM_TIMEOUT_NOTE = "The provider call failed."

TERMINAL_TOOL = "finish_etl"


class ETLAgentError(Exception):
    """A condition the loop cannot recover from (no provider, bad request)."""


# ---------------------------------------------------------------------------
# Session state - one per run, in memory, discarded when the request ends
# ---------------------------------------------------------------------------


class ETLSession:
    """Everything one ETL run needs. Never serialised into an LLM message."""

    def __init__(
        self,
        connection_string: str,
        db_type: str,
        schema: str,
        db: Any = None,
        allowed_tables: Optional[List[str]] = None,
    ):
        self.connection_string = connection_string   # secret - never in a message
        self.db_type = db_type
        self.schema = schema
        self.db = db                                  # SQLAlchemy Session, for finish_etl

        self.allowed_tables: Optional[List[str]] = list(allowed_tables) if allowed_tables else None

        self.df: Optional[pd.DataFrame] = None        # the working dataset
        self.build_info: Optional[Dict[str, Any]] = None
        self.transform_log: List[str] = []
        self.analyzed: Dict[str, Dict[str, Any]] = {}  # "A.x->B.y" -> recommendation

        # --- grain state -------------------------------------------------
        # A measure may only be aggregated at the grain of the table it came
        # from. These three carry the evidence for that: which key identifies
        # a row of each source table, which output column came from which
        # table, and which tables have their rows repeated by a downstream
        # one-to-many join.
        self.primary_keys_by_table: Dict[str, List[str]] = {}
        self.grain: Dict[str, Any] = {}
        self.internal_columns: set = set()  # kept for grain, dropped before the CSV

        # Set to the dataset's fingerprint when validate_dataset reports a
        # clean bill of health. Cleared the moment the dataset actually
        # changes. While it is set, a redundant cleanup or validation call is
        # not executed - see _redundant_after_clean_validation.
        self.clean_validation: Optional[str] = None

        self.result: Optional[Dict[str, Any]] = None   # set by finish_etl

        # --- the privacy boundary's state -------------------------------
        # Names that provably came from the SCHEMA (the database told us) or
        # that the MODEL ITSELF chose. Any other name in a dataset is
        # value-derived until proven otherwise, and is never shown to the LLM.
        self.safe_names: set = set()
        self._placeholders: Dict[str, str] = {}
        self._value_cache_key: Any = None
        self._value_cache: List[str] = []
        self.register_names(schema, *(allowed_tables or []))

    # -- helpers ----------------------------------------------------------

    def has_dataset(self) -> bool:
        return self.df is not None

    def dataset_fingerprint(self) -> Optional[str]:
        """A cheap identity for the current dataset's CONTENT.

        Shape and column names alone are not enough: fill_nulls changes values
        without changing either, and that has to count as a real change. The
        row hash catches it. Never raises - an exotic dtype degrades to a
        shape-only fingerprint rather than breaking the guard.
        """
        if self.df is None:
            return None
        shape = f"{self.df.shape}:{tuple(str(c) for c in self.df.columns)}"
        try:
            from pandas.util import hash_pandas_object  # noqa: PLC0415

            return f"{shape}:{int(hash_pandas_object(self.df, index=False).sum())}"
        except Exception:  # noqa: BLE001
            return shape

    # -- name safety ------------------------------------------------------

    def register_names(self, *names: Any) -> None:
        """Record names that are safe to show the LLM.

        Called by every tool that learns a name from the database schema, and
        by execute_tool for names the model supplied itself. A name the model
        already knows cannot be leaked to it.
        """
        for name in names:
            if isinstance(name, str) and name.strip():
                self.safe_names.add(name.strip().lower())

    def public_name(self, name: Any) -> str:
        """The LLM-safe spelling of one name.

        A registered name passes through unchanged. A compound built only from
        registered parts ("Customers.CustomerID", "A.x = B.y") passes too.
        Anything else is a value-derived name - pandas' one-hot encoding is the
        known producer - and becomes a neutral placeholder. The value behind it
        is never disclosed, and the same name always maps to the same
        placeholder within a run so the LLM can still reason about it.
        """
        text = str(name)
        if text.strip().lower() in self.safe_names:
            return text
        parts = [part for part in re.split(r"[.\[\]=\s,]+", text) if part]
        if parts and all(part.strip().lower() in self.safe_names for part in parts):
            return text
        return self._placeholder(text)

    def _placeholder(self, text: str) -> str:
        key = str(text).strip().lower()
        if key not in self._placeholders:
            self._placeholders[key] = f"derived_column_{len(self._placeholders) + 1}"
        return self._placeholders[key]

    def unsafe_frame_names(self) -> List[str]:
        """Current dataset columns whose names are not provably schema-derived.
        Longest first, so replacing them in free text cannot leave a fragment."""
        if self.df is None:
            return []
        return sorted(
            (
                str(column)
                for column in self.df.columns
                if str(column).strip().lower() not in self.safe_names
            ),
            key=len,
            reverse=True,
        )

    def dataset_values(self) -> List[str]:
        """A bounded sample of the dataset's own text values, longest first.

        Free text is the only unstructured channel through the boundary. Every
        summary the tools write today is template-built from names and numbers,
        so nothing should ever match - but "should" is what produced the
        one-hot leak, so the boundary checks rather than trusts.

        Bounded and cached: only object columns, only values of 4+ characters,
        at most MAX_SCRUB_VALUES per column and MAX_SCRUB_TOTAL overall.
        Numeric values are not scrubbed - counts and statistics are allowed
        metadata, and blanking digits would destroy them.
        """
        if self.df is None:
            return []

        fingerprint = (id(self.df), self.df.shape)
        if self._value_cache_key == fingerprint:
            return self._value_cache

        values: List[str] = []
        for column in self.df.columns:
            series = self.df[column]
            if series.dtype != object:
                continue
            try:
                distinct = series.dropna().unique()[:MAX_SCRUB_VALUES]
            except Exception:  # noqa: BLE001 - an exotic dtype must not break the boundary
                continue
            for value in distinct:
                text = str(value).strip()
                if len(text) >= 4:
                    values.append(text)
            if len(values) >= MAX_SCRUB_TOTAL:
                break

        values = sorted(set(values), key=len, reverse=True)[:MAX_SCRUB_TOTAL]
        self._value_cache_key, self._value_cache = fingerprint, values
        return values

    def scrub_text(self, text: Any) -> str:
        """Strip value-derived names AND dataset values from a sentence."""
        out = str(text)
        for name in self.unsafe_frame_names():
            if name in out:
                out = out.replace(name, self._placeholder(name))
        for value in self.dataset_values():
            if value in out:
                out = out.replace(value, VALUE_WITHHELD)
        return out[:MAX_TEXT_CHARS]

    def check_table(self, table: str) -> None:
        if self.allowed_tables and table not in self.allowed_tables:
            raise ToolFailure(
                f"'{table}' is not in the table list for this request. "
                f"Available: {', '.join(self.allowed_tables)}."
            )

    def check_columns(self, columns: List[str]) -> None:
        if self.df is None:
            raise ToolFailure("No dataset has been built yet. Call build_dataset first.")
        unknown = [c for c in columns if c not in self.df.columns]
        if unknown:
            raise ToolFailure(
                f"Column(s) not in the dataset: {', '.join(map(str, unknown))}. "
                f"Available: {', '.join(map(str, self.df.columns))}."
            )


class ToolFailure(Exception):
    """A user-safe tool failure. Becomes {"ok": False, ...} for the LLM."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ETL_TOOLS: Dict[str, Dict[str, Any]] = {}


def etl_tool(
    name: str,
    description: str,
    parameters: Dict[str, Any],
    requires_dataset: bool = False,
    terminal: bool = False,
):
    """Register one ETL tool. Same shape as transform_mcp_server.register_tool,
    but the function takes (session, **arguments) and returns a dict."""

    def decorator(func: Callable):
        ETL_TOOLS[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "function": func,
            "requires_dataset": requires_dataset,
            "terminal": terminal,
        }
        return func

    return decorator


# ---------------------------------------------------------------------------
# Summarisers - the ONLY thing that turns data into text for the LLM
# ---------------------------------------------------------------------------


def _frame_summary(df: pd.DataFrame, include_columns: bool = True) -> Dict[str, Any]:
    """Shape, names, dtypes and counts. No cell value ever leaves here."""
    summary: Dict[str, Any] = {
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "duplicate_rows": int(df.duplicated().sum()),
    }
    if not include_columns:
        return summary

    rows = max(len(df), 1)
    details = []
    for name in df.columns:
        nulls = int(df[name].isnull().sum())
        details.append(
            {
                "name": str(name),
                "dtype": str(df[name].dtype),
                "null_count": nulls,
                # float() matters: a numpy scalar here breaks FastAPI's
                # response serialization at the very end of a successful run.
                "null_percentage": float(round(100.0 * nulls / rows, 2)),
                "distinct_count": int(df[name].nunique(dropna=True)),
            }
        )
    summary["column_details"] = details
    return summary


# ---------------------------------------------------------------------------
# TOOLS - database / metadata
# ---------------------------------------------------------------------------


@etl_tool(
    name="inspect_tables",
    description=(
        "List the tables available in the connected database schema. "
        "Returns names only - no data."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
def _inspect_tables(session: ETLSession) -> Dict[str, Any]:
    try:
        names = list_tables(session.connection_string, schema=session.schema)
    except DataSourceError as exc:
        raise ToolFailure(str(exc))

    if session.allowed_tables:
        names = [n for n in names if n in session.allowed_tables]

    session.register_names(*names)  # schema-derived: safe to show the LLM

    return {
        "ok": True,
        "schema": session.schema,
        "table_count": len(names),
        "tables": names,
        "summary": f"{len(names)} table(s) available in schema {session.schema}.",
    }


@etl_tool(
    name="inspect_columns",
    description=(
        "Return the column names, data types, nullability and primary-key flags "
        "for one or more tables. Cheap - metadata only, no row counts."
    ),
    parameters={
        "type": "object",
        "properties": {
            "table_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1 to 4 table names.",
            }
        },
        "required": ["table_names"],
    },
)
def _inspect_columns(session: ETLSession, table_names: List[str]) -> Dict[str, Any]:
    if not isinstance(table_names, list) or not table_names:
        raise ToolFailure("table_names must be a non-empty list of table names.")
    if len(table_names) > 4:
        raise ToolFailure("Inspect at most 4 tables in one call.")

    tables = [str(t) for t in table_names]
    for table in tables:
        session.check_table(table)

    try:
        metadata = list_columns(session.connection_string, tables, schema=session.schema)
    except DataSourceError as exc:
        raise ToolFailure(str(exc))

    out = {
        table: [
            {
                "name": str(column.get("name")),
                "type": str(column.get("type")),
                "nullable": bool(column.get("nullable", True)),
                "primary_key": bool(column.get("primary_key", False)),
            }
            for column in metadata.get(table, [])
        ]
        for table in tables
    }

    session.register_names(*tables)
    for column_list in out.values():
        session.register_names(*(column["name"] for column in column_list))

    return {
        "ok": True,
        "tables": out,
        "summary": "; ".join(f"{t}: {len(c)} columns" for t, c in out.items()),
    }


@etl_tool(
    name="inspect_table",
    description=(
        "Deep inspection of ONE table: columns, types, nullability, primary "
        "keys, total row count, and per-column null and distinct counts. "
        "Counts only - never any row values. Use this to find candidate key "
        "columns and to judge data quality."
    ),
    parameters={
        "type": "object",
        "properties": {"table_name": {"type": "string"}},
        "required": ["table_name"],
    },
)
def _inspect_table(session: ETLSession, table_name: str) -> Dict[str, Any]:
    table = str(table_name)
    session.check_table(table)

    try:
        metadata = list_columns(session.connection_string, [table], schema=session.schema)
    except DataSourceError as exc:
        raise ToolFailure(str(exc))

    raw_columns = metadata.get(table) or []
    if not raw_columns:
        raise ToolFailure(f"Table '{table}' has no readable columns.")
    if len(raw_columns) > MAX_PROFILED_COLUMNS:
        raise ToolFailure(
            f"'{table}' has {len(raw_columns)} columns, more than the "
            f"{MAX_PROFILED_COLUMNS} this tool profiles. Use inspect_columns instead."
        )

    columns = [
        {
            "name": str(column.get("name")),
            "type": str(column.get("type")),
            "nullable": bool(column.get("nullable", True)),
            "primary_key": bool(column.get("primary_key", False)),
        }
        for column in raw_columns
    ]

    session.register_names(table, *(column["name"] for column in columns))

    row_count, per_column = _profile_counts(session, table, [c["name"] for c in columns])

    for column in columns:
        counts = per_column.get(column["name"])
        if not counts:
            continue
        nulls = counts["nulls"]
        distincts = counts["distincts"]
        column["null_count"] = nulls
        column["distinct_count"] = distincts
        column["null_percentage"] = round(100.0 * nulls / row_count, 2) if row_count else 0.0
        column["looks_like_key"] = bool(row_count and distincts == row_count and nulls == 0)

    key_columns = [c["name"] for c in columns if c.get("looks_like_key")]

    return {
        "ok": True,
        "table": table,
        "row_count": row_count,
        "column_count": len(columns),
        "columns": columns,
        "candidate_key_columns": key_columns,
        "summary": (
            f"{table}: {row_count} rows, {len(columns)} columns. "
            f"Unique non-null column(s): {', '.join(key_columns) if key_columns else 'none'}."
        ),
    }


def _profile_counts(session: ETLSession, table: str, column_names: List[str]):
    """One aggregate SELECT: COUNT(*), COUNT(col), COUNT(DISTINCT col).

    Built here from identifiers the database itself reported, executed through
    db_connector.extract_to_dataframe (the same validated query path every
    other feature uses). Returns counts; fetches no row of the table.
    """
    from services.db_connector import quote_identifier

    def _qualified() -> str:
        if session.schema:
            return (
                f"{quote_identifier(session.db_type, session.schema)}."
                f"{quote_identifier(session.db_type, table)}"
            )
        return quote_identifier(session.db_type, table)

    try:
        selects = ["COUNT(*) AS total_rows"]
        for index, name in enumerate(column_names):
            quoted = quote_identifier(session.db_type, name)
            selects.append(f"COUNT({quoted}) AS nn_{index}")
            selects.append(f"COUNT(DISTINCT {quoted}) AS dc_{index}")
        sql = "SELECT " + ", ".join(selects) + f" FROM {_qualified()}"
        frame = extract_to_dataframe(session.connection_string, sql)
    except (DataSourceError, Exception) as exc:  # noqa: BLE001 - reduced below
        logger.info("Count profiling failed for %s: %s", table, exc)
        return 0, {}

    if frame.empty:
        return 0, {}

    values = list(frame.iloc[0])
    total = int(values[0] or 0)
    per_column: Dict[str, Dict[str, int]] = {}
    for index, name in enumerate(column_names):
        non_null = int(values[1 + index * 2] or 0)
        distinct = int(values[2 + index * 2] or 0)
        per_column[name] = {"nulls": total - non_null, "distincts": distinct}
    return total, per_column


@etl_tool(
    name="find_declared_relationships",
    description=(
        "Return the foreign keys the database schema declares between the "
        "given tables. An empty result does NOT mean the tables are unrelated - "
        "many databases have no declared keys. Fall back to matching column "
        "names, then verify with analyze_relationship."
    ),
    parameters={
        "type": "object",
        "properties": {
            "table_names": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["table_names"],
    },
)
def _find_declared_relationships(
    session: ETLSession, table_names: List[str]
) -> Dict[str, Any]:
    if not isinstance(table_names, list) or len(table_names) < 2:
        raise ToolFailure("Give at least two table names.")

    tables = [str(t) for t in table_names]
    for table in tables:
        session.check_table(table)

    try:
        edges = list_foreign_keys(session.connection_string, tables, schema=session.schema)
    except DataSourceError as exc:
        raise ToolFailure(str(exc))

    relationships = [
        {
            "left_table": str(edge.get("left_table")),
            "left_column": str(edge.get("left_column")),
            "right_table": str(edge.get("right_table")),
            "right_column": str(edge.get("right_column")),
        }
        for edge in (edges or [])
    ]

    for relationship in relationships:
        session.register_names(*relationship.values())

    return {
        "ok": True,
        "tables": tables,
        "relationship_count": len(relationships),
        "relationships": relationships,
        "summary": (
            f"{len(relationships)} declared foreign key(s) among {len(tables)} tables."
            if relationships
            else "No declared foreign keys. Infer candidates from column names "
            "and verify each with analyze_relationship."
        ),
    }


# ---------------------------------------------------------------------------
# TOOLS - relationship (delegates to the existing deterministic Join Advisor)
# ---------------------------------------------------------------------------


@etl_tool(
    name="analyze_relationship",
    description=(
        "Measure one candidate join against the real data and return match "
        "rates, null keys, cardinality, projected INNER and LEFT row counts, "
        "and a recommended join type. The recommendation is computed by a "
        "fixed rule table, not by you - prefer it. Counts only, no row values. "
        "Every join you propose must be verified with this tool first."
    ),
    parameters={
        "type": "object",
        "properties": {
            "base_table": {"type": "string"},
            "base_column": {"type": "string"},
            "related_table": {"type": "string"},
            "related_column": {"type": "string"},
        },
        "required": ["base_table", "base_column", "related_table", "related_column"],
    },
)
def _analyze_relationship(
    session: ETLSession,
    base_table: str,
    base_column: str,
    related_table: str,
    related_column: str,
) -> Dict[str, Any]:
    base_table, related_table = str(base_table), str(related_table)
    base_column, related_column = str(base_column), str(related_column)

    session.check_table(base_table)
    session.check_table(related_table)

    try:
        sql = build_profile_query(
            db_type=session.db_type,
            schema=session.schema,
            base_table=base_table,
            base_column=base_column,
            related_table=related_table,
            related_column=related_column,
        )
    except JoinProfileError as exc:
        raise ToolFailure(str(exc))

    try:
        frame = extract_to_dataframe(session.connection_string, sql)
    except DataSourceError as exc:
        raise ToolFailure(str(exc))

    if frame.empty:
        raise ToolFailure("The relationship profile query returned nothing.")

    try:
        profile = profile_from_row(frame.iloc[0].to_dict())
        recommendation = recommend(
            profile=profile,
            base_table=base_table,
            related_table=related_table,
            row_limit=MAX_ROWS,
        )
    except JoinProfileError as exc:
        raise ToolFailure(str(exc))

    session.register_names(base_table, base_column, related_table, related_column)

    key = f"{base_table}.{base_column}->{related_table}.{related_column}"
    # The four identifiers matter as much as the verdict: row_multiplication
    # on this edge means rows of BASE_TABLE are repeated, which is what decides
    # whether a measure from base_table may be summed without de-fanning first.
    session.analyzed[key] = {
        "base_table": base_table,
        "base_column": base_column,
        "related_table": related_table,
        "related_column": related_column,
        "recommended": recommendation.recommended,
        "cardinality": profile.cardinality,
        "row_multiplication": profile.row_multiplication,
    }

    return {
        "ok": True,
        "base_table": base_table,
        "base_column": base_column,
        "related_table": related_table,
        "related_column": related_column,
        "profile": {
            "base_rows": profile.base_rows,
            "related_rows": profile.related_rows,
            "base_matched_rows": profile.base_matched_rows,
            "base_unmatched_rows": profile.base_unmatched_rows,
            "related_unmatched_rows": profile.related_unmatched_rows,
            "base_null_keys": profile.base_null_keys,
            "related_null_keys": profile.related_null_keys,
            "base_distinct_keys": profile.base_distinct_keys,
            "related_distinct_keys": profile.related_distinct_keys,
            "max_rows_per_key": profile.max_rows_per_key,
            "base_key_unique": profile.base_key_unique,
            "related_key_unique": profile.related_key_unique,
            "match_percentage": profile.match_percentage,
            "cardinality": profile.cardinality,
            "inner_result_rows": profile.inner_result_rows,
            "left_result_rows": profile.left_result_rows,
            "row_multiplication": profile.row_multiplication,
        },
        "recommendation": {
            "recommended": recommendation.recommended,
            "severity": recommendation.severity,
            "headline": recommendation.headline,
            "reasons": list(recommendation.reasons),
        },
        "summary": recommendation.headline,
    }


# ---------------------------------------------------------------------------
# TOOLS - dataset build (delegates to the existing SQL Builder)
# ---------------------------------------------------------------------------


@etl_tool(
    name="build_dataset",
    description=(
        "Compile the approved join into one SELECT and load the result as the "
        "working dataset. The SQL is generated by the platform's deterministic "
        "SQL builder from the tables and relationships you name - you do not "
        "write SQL. Verify each relationship with analyze_relationship first. "
        f"At most {MAX_TABLES} tables. Calling this again replaces the working "
        "dataset and discards any transformations already applied."
    ),
    parameters={
        "type": "object",
        "properties": {
            "base_table": {
                "type": "string",
                "description": "The table whose rows become the dataset's rows.",
            },
            "tables": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Every table to include, base table included.",
            },
            "relationships": {
                "type": "array",
                "description": "One entry per join. Leave empty for a single table.",
                "items": {
                    "type": "object",
                    "properties": {
                        "left_table": {"type": "string"},
                        "left_column": {"type": "string"},
                        "right_table": {"type": "string"},
                        "right_column": {"type": "string"},
                        "join_type": {"type": "string", "enum": ["inner", "left"]},
                    },
                    "required": [
                        "left_table",
                        "left_column",
                        "right_table",
                        "right_column",
                        "join_type",
                    ],
                },
            },
            "selected_columns": {
                "type": "object",
                "description": (
                    "Optional {table: [column names]} subset. Omit to take every "
                    "column of every table. You do NOT need to list the join key "
                    "columns here - they are used for the join automatically and "
                    "only appear in the output if you ask for them."
                ),
            },
        },
        "required": ["base_table", "tables", "relationships"],
    },
)
def _build_dataset(
    session: ETLSession,
    base_table: str,
    tables: List[str],
    relationships: Optional[List[Dict[str, Any]]] = None,
    selected_columns: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    base_table = str(base_table)
    if not isinstance(tables, list) or not tables:
        raise ToolFailure("tables must be a non-empty list.")

    table_list = [str(t) for t in tables]
    if base_table not in table_list:
        table_list.insert(0, base_table)
    if len(table_list) > MAX_TABLES:
        raise ToolFailure(f"At most {MAX_TABLES} tables can be joined.")
    for table in table_list:
        session.check_table(table)

    columns_by_table, available_by_table = _resolve_columns(
        session, table_list, selected_columns
    )

    try:
        edges = edges_from_dicts(relationships or [])
    except SqlBuildError as exc:
        raise ToolFailure(str(exc))

    # A join key the caller did not select still has to reach sql_builder, or
    # _validate_graph reports it as missing from its own table. Add it here and
    # remember to drop it from the output afterwards. Must run BEFORE the row
    # estimate, which is built from the same projection.
    implicit_columns = _include_join_keys(columns_by_table, available_by_table, edges)

    # Refuse a fan-out that cannot fit, BEFORE fetching anything.
    estimated = _estimate_rows(session, base_table, table_list, edges, columns_by_table)
    if estimated is not None and estimated > MAX_ROWS:
        raise ToolFailure(
            f"This join produces about {estimated} rows, over the {MAX_ROWS} "
            f"limit. Drop a table, switch a join to INNER, or aggregate the "
            f"one-to-many side first."
        )

    try:
        built = build_select(
            db_type=session.db_type,
            base_table=base_table,
            tables=table_list,
            relationships=edges,
            columns_by_table=columns_by_table,
            schema=session.schema,
            limit=MAX_ROWS,
        )
    except SqlBuildError as exc:
        raise ToolFailure(str(exc))

    try:
        frame = extract_to_dataframe(session.connection_string, built.sql)
    except DataSourceError as exc:
        raise ToolFailure(str(exc))

    # Join keys that were added implicitly did their work in the SQL; they are
    # not output columns unless the caller asked for them. alias_map maps the
    # output alias to "Table.column", so the aliases to drop are exact.
    # They are KEPT in the frame, not dropped: a join key is often the natural
    # key a measure has to be de-fanned on before it can be aggregated, and
    # OrderID is exactly that case. They are recorded as internal instead, and
    # finish_etl removes them from the CSV unless they were explicitly chosen.
    internal_join_keys: List[str] = []
    if implicit_columns:
        internal_join_keys = [
            alias
            for alias, source in built.alias_map.items()
            if _source_pair(source) in implicit_columns and alias in frame.columns
        ]
        session.internal_columns.update(internal_join_keys)

    # sql_builder's output aliases are schema-derived, not value-derived.
    session.register_names(*built.column_names, *table_list)

    session.grain = _build_grain(session, base_table, table_list, built, frame)

    session.df = frame
    session.transform_log = []
    session.build_info = {
        "base_table": base_table,
        "tables": table_list,
        "relationships": [
            {
                "left_table": edge.left_table,
                "left_column": edge.left_column,
                "right_table": edge.right_table,
                "right_column": edge.right_column,
                "join_type": edge.join_type,
            }
            for edge in built.join_order
        ],
        "column_names": list(frame.columns),
        "alias_map": dict(built.alias_map),
        "join_keys_used_internally": list(internal_join_keys),
        "column_sources": dict(session.grain["column_sources"]),
        "fanned_out_tables": list(session.grain["fanned_out_tables"]),
        "multiplied_tables": list(session.grain["multiplied_tables"]),
    }

    summary = _frame_summary(frame)
    return {
        "ok": True,
        "base_table": base_table,
        "tables": table_list,
        "join_order": [edge.describe() for edge in built.join_order],
        "estimated_rows": estimated,
        "dataset": summary,
        "column_names": list(frame.columns),
        "join_keys_used_internally": list(internal_join_keys),
        "column_sources": dict(session.grain["column_sources"]),
        "fanned_out_tables": list(session.grain["fanned_out_tables"]),
        "multiplied_tables": list(session.grain["multiplied_tables"]),
        "natural_key_columns": dict(session.grain["natural_key_aliases"]),
        "summary": (
            f"Built {summary['rows']} rows x {summary['columns']} columns from "
            f"{', '.join(table_list)}. The output column names are listed in "
            f"column_names above - use them exactly as written, copying each "
            f"one character for character. Do not construct, guess or infer a "
            f"column name from the table and column it came from; the aliasing "
            f"rule is not something you can reproduce."
            + (
                f" Join key(s) {', '.join(internal_join_keys)} are kept in the "
                f"dataset because they may be needed to de-fan a measure before "
                f"aggregating it; they are removed from the saved file unless you "
                f"select them."
                if internal_join_keys
                else ""
            )
            + (
                f" WARNING: rows of {', '.join(session.grain['multiplied_tables'])} "
                f"are REPEATED by a one-to-many join. A sum or mean of any column "
                f"from those tables will over-count unless you pass dedupe_on to "
                f"aggregate_rows. natural_key_columns tells you which column to "
                f"use. Do NOT use drop_duplicates on the target grain to fix this."
                if session.grain["multiplied_tables"]
                else ""
            )
        ),
    }


def _build_grain(
    session: ETLSession,
    base_table: str,
    table_list: List[str],
    built,
    frame: pd.DataFrame,
) -> Dict[str, Any]:
    """Record what is needed to aggregate a measure at its own grain.

    column_sources       output alias -> "Table.column"  (from sql_builder)
    fanned_out_tables    tables whose ATTACHMENT multiplies rows (related side)
    multiplied_tables    tables whose OWN rows are repeated (base side) - this
                         is the one the guard uses: a measure from a multiplied
                         table must be de-fanned before it is summed
    natural_key_aliases  table -> the output aliases of its primary key
    """
    column_sources = {
        alias: source
        for alias, source in built.alias_map.items()
        if alias in frame.columns
    }

    # row_multiplication on edge (base -> related) means base's rows repeat.
    multiplied = sorted(
        {
            str(info.get("base_table"))
            for info in session.analyzed.values()
            if info.get("row_multiplication") and info.get("base_table")
        }
    )
    fanned_out = sorted(
        {
            str(info.get("related_table"))
            for info in session.analyzed.values()
            if info.get("row_multiplication") and info.get("related_table")
        }
    )

    # Which output alias carries each table's primary key?
    natural_key_aliases: Dict[str, List[str]] = {}
    for table, keys in (session.primary_keys_by_table or {}).items():
        aliases = []
        for key_column in keys:
            wanted = (table, str(key_column).lower())
            aliases.extend(
                alias
                for alias, source in column_sources.items()
                if _source_pair(source) == wanted
            )
        if aliases:
            natural_key_aliases[table] = aliases

    return {
        "base_table": base_table,
        "tables": list(table_list),
        "primary_keys_by_table": dict(session.primary_keys_by_table),
        "column_sources": column_sources,
        "fanned_out_tables": [t for t in fanned_out if t in table_list],
        "multiplied_tables": [t for t in multiplied if t in table_list],
        "natural_key_aliases": natural_key_aliases,
        "aggregated_to": None,
    }


def _source_table_of(session: ETLSession, column: str) -> Optional[str]:
    """Which source table an output column came from, or None if unknown."""
    source = (session.grain.get("column_sources") or {}).get(column)
    if not source:
        return None
    return _source_pair(source)[0]


# Functions whose result is distorted by repeated rows. min/max are unaffected
# (repeating a value cannot change them) and nunique is definitionally safe.
FANOUT_SENSITIVE_FUNCTIONS = {"sum", "mean", "median", "std", "count"}


def _grain_guard(
    session: ETLSession,
    keys: List[str],
    named: Dict[str, tuple],
    dedupe_on: Optional[List[str]],
) -> Optional[str]:
    """Refuse an aggregation that would over-count a repeated measure."""
    multiplied = set(session.grain.get("multiplied_tables") or [])
    if not multiplied:
        return None

    natural_keys: Dict[str, List[str]] = (
        session.grain.get("natural_key_aliases") or {}
    )
    at_risk: Dict[str, List[str]] = {}

    for new_name, (column, function) in named.items():
        if function not in FANOUT_SENSITIVE_FUNCTIONS:
            continue

        table = _source_table_of(session, column)
        if table and table in multiplied:
            at_risk.setdefault(table, []).append(
                f"{column} ({function})"
            )

    if not at_risk:
        return None

    # Measures from two different repeated tables cannot both be
    # safely de-fanned by one dedupe operation.
    if len(at_risk) > 1:
        return (
            "This aggregation mixes measures from more than one repeated "
            "table "
            f"({', '.join(sorted(at_risk))}), which cannot be de-fanned "
            "in a single step. Aggregate one table's measures at a time."
        )

    table = next(iter(at_risk))

    natural_key_columns = [
        str(k)
        for k in natural_keys.get(table, [])
        if k in session.df.columns
    ]

    keys_for_table = [
        k for k in natural_key_columns
        if k not in keys
    ]

    if not keys_for_table:
        declared = (
            (session.grain.get("primary_keys_by_table") or {}).get(table)
            or []
        )

        if len(declared) > 1:
            return (
                f"'{table}' has a composite primary key "
                f"({', '.join(declared)}), which this version cannot "
                "de-fan safely. Aggregate that table separately, or "
                "build the dataset without the table that multiplies "
                "its rows."
            )

        return (
            f"Rows of '{table}' are repeated by a one-to-many join, so "
            f"{', '.join(sorted(v for vs in at_risk.values() for v in vs))} "
            f"would be over-counted - but no usable natural key for "
            f"'{table}' is present in the dataset, so it cannot be "
            "de-fanned. Rebuild the dataset including that table's "
            "primary key, or leave out the table that multiplies it."
        )

    # No dedupe key was provided.
    if not dedupe_on:
        return (
            f"Refused: rows of '{table}' are repeated by a one-to-many "
            f"join, so "
            f"{', '.join(sorted(v for vs in at_risk.values() for v in vs))} "
            f"would be over-counted. Pass dedupe_on={keys_for_table} "
            "to collapse the source table back to one row per record "
            "BEFORE aggregating. Do not use drop_duplicates on "
            f"{', '.join(keys)} instead - that deletes real rows."
        )

    requested = {str(c) for c in dedupe_on}
    grouping_keys = {str(k) for k in keys}

    # Never dedupe on the final grouping grain.
    overlap = requested & grouping_keys
    if overlap:
        return (
            f"Refused: dedupe_on={sorted(requested)} overlaps the "
            f"grouping columns {sorted(grouping_keys)}. De-duplicating "
            "on the grain you are aggregating TO keeps one row per "
            "group and deletes the rest - it is data loss, not "
            "de-fanning."
        )

    base_table = session.grain.get("base_table")
    if table == base_table:
        return (
            f"Refused: '{table}' is the base table, so its key defines "
            "the output grain and cannot be used to de-fan. De-fan on "
            "the child table's key instead."
        )

    # The supplied key must be exactly the natural key of the
    # table whose measure is being aggregated.
    expected = set(natural_key_columns)

    if requested != expected:
        owner = next(
            (
                t
                for t, aliases in natural_keys.items()
                if requested == {str(x) for x in aliases}
            ),
            None,
        )

        hint = (
            f" {sorted(requested)} is the natural key of '{owner}', "
            f"not of '{table}'."
            if owner
            else ""
        )

        return (
            f"Refused: "
            f"{', '.join(sorted(v for vs in at_risk.values() for v in vs))} "
            f"comes from '{table}', whose rows are repeated by a "
            "one-to-many join. It must be de-fanned on that table's "
            f"own natural key {sorted(expected)}, not on "
            f"{sorted(requested)}.{hint}"
        )

    # Everything is valid.
    return None


    # Measures from two different repeated tables cannot both be de-fanned by
    # one dedupe. Refuse rather than return a number that is right for one.
    if len(at_risk) > 1:
        return (
            "This aggregation mixes measures from more than one repeated table "
            f"({', '.join(sorted(at_risk))}), which cannot be de-fanned in a "
            "single step. Aggregate one table's measures at a time."
        )

    table = next(iter(at_risk))
    keys_for_table = [k for k in natural_keys.get(table, []) if k not in keys]

    if not keys_for_table:
        declared = (session.grain.get("primary_keys_by_table") or {}).get(table) or []
        if len(declared) > 1:
            return (
                f"'{table}' has a composite primary key "
                f"({', '.join(declared)}), which this version cannot de-fan "
                f"safely. Aggregate that table separately, or build the dataset "
                f"without the table that multiplies its rows."
            )
        return (
            f"Rows of '{table}' are repeated by a one-to-many join, so "
            f"{', '.join(sorted(v for vs in at_risk.values() for v in vs))} "
            f"would be over-counted - but no usable natural key for '{table}' is "
            f"present in the dataset, so it cannot be de-fanned. Rebuild the "
            f"dataset including that table's primary key, or leave out the table "
            f"that multiplies it."
        )

    return (
        f"Refused: rows of '{table}' are repeated by a one-to-many join, so "
        f"{', '.join(sorted(v for vs in at_risk.values() for v in vs))} would be "
        f"over-counted. Pass dedupe_on={keys_for_table} to collapse '{table}' "
        f"back to one row per record BEFORE aggregating. Do not use "
        f"drop_duplicates on {', '.join(keys)} instead - that deletes real rows."
    )


def _resolve_columns(
    session: ETLSession,
    tables: List[str],
    selected_columns: Optional[Dict[str, List[str]]],
) -> tuple:
    """Resolve the projection, and hand back the real columns alongside it.

    Returns (columns_by_table, available_by_table):

      columns_by_table   what will be SELECTed - filtered to selected_columns
      available_by_table every column the table actually has

    The second dict is what makes _include_join_keys safe. Without it we would
    be adding a join key to the projection on the model's word alone; with it,
    a key that really is misspelled still fails here, with a message that names
    the columns that do exist, instead of failing later against the driver.
    """
    try:
        metadata = list_columns(session.connection_string, tables, schema=session.schema)
    except DataSourceError as exc:
        raise ToolFailure(str(exc))

    out: Dict[str, List[str]] = {}
    available_by_table: Dict[str, List[str]] = {}

    session.primary_keys_by_table = {}

    for table in tables:
        table_metadata = metadata.get(table, [])

        available = [str(column["name"]) for column in table_metadata]
        # The declared primary key is the table's natural key, and the only
        # safe thing to de-fan its measures on.
        session.primary_keys_by_table[table] = [
            str(column["name"])
            for column in table_metadata
            if column.get("primary_key")
        ]
        if not available:
            raise ToolFailure(f"Table '{table}' has no readable columns.")
        available_by_table[table] = available

        if not selected_columns or table not in selected_columns:
            out[table] = list(available)
            continue

        wanted = {str(name).lower() for name in selected_columns[table]}
        unknown = wanted - {name.lower() for name in available}
        if unknown:
            raise ToolFailure(
                f"Column(s) {', '.join(sorted(unknown))} do not exist in '{table}'."
            )
        out[table] = [name for name in available if name.lower() in wanted]

    # Every name here came from the database's own metadata.
    session.register_names(*tables)
    for names in available_by_table.values():
        session.register_names(*names)

    return out, available_by_table


def _source_pair(source: str) -> tuple:
    """Split an alias_map value ("Customers.CustomerID") into a match key."""
    table, _, column = str(source).rpartition(".")
    return (table, column.lower())


def _include_join_keys(
    columns_by_table: Dict[str, List[str]],
    available_by_table: Dict[str, List[str]],
    edges,
) -> set:
    """Put every join key into the projection handed to sql_builder.

    sql_builder._validate_graph checks each relationship column against
    columns_by_table - which is also the SELECT list. So a key the caller chose
    not to output is reported as "Column 'X' does not exist in 'T'", even
    though T has it. That message is about the projection, not the table, and
    it is unactionable: the caller can see the column in the metadata.

    Adding the key here fixes that, but only after checking it against the
    table's REAL columns. Appending blindly would satisfy _validate_graph and
    then fail at the database with an "Invalid column name" from the driver,
    which is a worse error than the one being fixed.

    Returns the (table, lowercased column) pairs that were added, so the caller
    can drop them from the DataFrame once the query has run.
    """
    implicit: set = set()

    for edge in edges:
        for table, column in (
            (edge.left_table, edge.left_column),
            (edge.right_table, edge.right_column),
        ):
            projected = columns_by_table.get(table)
            if projected is None:
                # An unknown table; sql_builder reports that itself, with the
                # message written for it.
                continue

            wanted = str(column).lower()
            if any(str(name).lower() == wanted for name in projected):
                continue  # explicitly selected - it stays in the output

            real = available_by_table.get(table, [])
            match = next((n for n in real if str(n).lower() == wanted), None)
            if match is None:
                raise ToolFailure(
                    f"Join column '{column}' does not exist in '{table}'. "
                    f"Its columns are: {', '.join(real)}."
                )

            projected.append(match)
            implicit.add((table, match.lower()))

    return implicit


def _estimate_rows(
    session: ETLSession,
    base_table: str,
    tables: List[str],
    edges,
    columns_by_table: Dict[str, List[str]],
) -> Optional[int]:
    try:
        count_sql = build_count_select(
            db_type=session.db_type,
            base_table=base_table,
            tables=tables,
            relationships=edges,
            columns_by_table=columns_by_table,
            schema=session.schema,
        )
    except SqlBuildError as exc:
        raise ToolFailure(str(exc))

    try:
        frame = extract_to_dataframe(session.connection_string, count_sql)
        return int(frame.iloc[0, 0]) if not frame.empty else None
    except (DataSourceError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# TOOLS - transforms that the existing registry does not cover
# ---------------------------------------------------------------------------


@etl_tool(
    name="select_columns",
    description=(
        "Keep ONLY the named columns and drop the rest. Use this to remove "
        "identifier columns and other non-predictive fields before training. "
        "(To drop a few columns instead, use drop_columns.)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["columns"],
    },
    requires_dataset=True,
)
def _select_columns(session: ETLSession, columns: List[str]) -> Dict[str, Any]:
    if not isinstance(columns, list) or not columns:
        raise ToolFailure("columns must be a non-empty list.")
    keep = [str(c) for c in columns]
    session.check_columns(keep)

    before = session.df.shape[1]
    session.df = session.df[keep].copy()
    session.transform_log.append(f"select_columns -> kept {len(keep)} of {before}")

    return {
        "ok": True,
        "dataset": _frame_summary(session.df),
        "summary": f"Kept {len(keep)} of {before} columns.",
    }


@etl_tool(
    name="add_calculated_column",
    description=(
        "Create a new numeric column from two operands with one arithmetic "
        "operation. Each operand is either an existing column name or a plain "
        "number. No expressions or code - if you need something more complex, "
        "chain several calls."
    ),
    parameters={
        "type": "object",
        "properties": {
            "new_column": {"type": "string", "description": "Name for the new column."},
            "left": {"type": "string", "description": "Column name or number."},
            "operation": {
                "type": "string",
                "enum": ["add", "subtract", "multiply", "divide"],
            },
            "right": {"type": "string", "description": "Column name or number."},
        },
        "required": ["new_column", "left", "operation", "right"],
    },
    requires_dataset=True,
)
def _add_calculated_column(
    session: ETLSession, new_column: str, left: str, operation: str, right: str
) -> Dict[str, Any]:
    new_column = str(new_column).strip()
    if not new_column:
        raise ToolFailure("new_column must be a name.")
    if new_column in session.df.columns:
        raise ToolFailure(f"Column '{new_column}' already exists.")

    def _operand(token: str):
        token = str(token)
        if token in session.df.columns:
            series = pd.to_numeric(session.df[token], errors="coerce")
            if series.isnull().all():
                raise ToolFailure(f"Column '{token}' holds no numeric values.")
            return series
        try:
            return float(token)
        except (TypeError, ValueError):
            raise ToolFailure(
                f"'{token}' is neither a column in the dataset nor a number."
            )

    left_value, right_value = _operand(left), _operand(right)

    if operation == "add":
        result = left_value + right_value
    elif operation == "subtract":
        result = left_value - right_value
    elif operation == "multiply":
        result = left_value * right_value
    elif operation == "divide":
        denominator = right_value
        if isinstance(denominator, float):
            if denominator == 0:
                raise ToolFailure("Division by zero.")
            result = left_value / denominator
        else:
            result = left_value / denominator.replace(0, pd.NA)
    else:
        raise ToolFailure(f"Unsupported operation '{operation}'.")

    session.df = session.df.copy()
    session.df[new_column] = result
    session.transform_log.append(
        f"add_calculated_column -> {new_column} = {left} {operation} {right}"
    )

    nulls = int(session.df[new_column].isnull().sum())
    return {
        "ok": True,
        "new_column": new_column,
        "null_count": nulls,
        "dataset": _frame_summary(session.df, include_columns=False),
        "summary": (
            f"Added '{new_column}' ({left} {operation} {right}); "
            f"{nulls} null value(s)."
        ),
    }


@etl_tool(
    name="aggregate_rows",
    description=(
        "Group the dataset by one or more columns and aggregate the rest. Use "
        "this to collapse a one-to-many join back to one row per entity - for "
        "example one row per customer after joining orders. Columns not named "
        "here are dropped."
    ),
    parameters={
        "type": "object",
        "properties": {
            "group_by": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Columns defining the output grain.",
            },
            "aggregations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string"},
                        "function": {
                            "type": "string",
                            "enum": [
                                "sum", "mean", "min", "max",
                                "count", "nunique", "median", "std",
                            ],
                        },
                        "new_name": {"type": "string"},
                    },
                    "required": ["column", "function"],
                },
            },
            "dedupe_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The natural key of the measure's SOURCE table, used to "
                    "collapse repeated rows BEFORE aggregating. Required when a "
                    "measure comes from a table whose rows are repeated by a "
                    "one-to-many join - build_dataset reports those in "
                    "multiplied_tables and gives the key in natural_key_columns. "
                    "Example: summing an order amount after joining order items "
                    "needs dedupe_on set to the order-id column."
                ),
            },
        },
        "required": ["group_by", "aggregations"],
    },
    requires_dataset=True,
)
def _aggregate_rows(
    session: ETLSession,
    group_by: List[str],
    aggregations: List[Dict[str, Any]],
    dedupe_on: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if not isinstance(group_by, list) or not group_by:
        raise ToolFailure("group_by must be a non-empty list of columns.")
    if not isinstance(aggregations, list) or not aggregations:
        raise ToolFailure("aggregations must be a non-empty list.")

    keys = [str(c) for c in group_by]
    session.check_columns(keys)

    if dedupe_on is not None:
        if not isinstance(dedupe_on, list) or not dedupe_on:
            raise ToolFailure(
                "dedupe_on must be a non-empty list of column names, or omitted."
            )
        dedupe_on = [str(c) for c in dedupe_on]
        session.check_columns(dedupe_on)  # a missing key fails here, loudly

    allowed = {"sum", "mean", "min", "max", "count", "nunique", "median", "std"}
    named: Dict[str, tuple] = {}
    for spec in aggregations:
        if not isinstance(spec, dict):
            raise ToolFailure("Each aggregation must be an object.")
        column = str(spec.get("column", ""))
        function = str(spec.get("function", "")).lower()
        if function not in allowed:
            raise ToolFailure(f"Unsupported aggregation function '{function}'.")
        session.check_columns([column])
        new_name = str(spec.get("new_name") or f"{column}_{function}")
        if new_name in keys:
            raise ToolFailure(f"'{new_name}' collides with a group_by column.")
        named[new_name] = (column, function)

    # Refuse an over-counting aggregation rather than return a wrong number.
    refusal = _grain_guard(session, keys, named, dedupe_on)
    if refusal:
        raise ToolFailure(refusal)

    before_rows = len(session.df)
    deduped_rows = before_rows

    try:
        working_frame = session.df

        if dedupe_on:
            # Split on the key's nullity first. An unmatched LEFT JOIN row has a
            # NULL key, and drop_duplicates treats NULL as a value - so a plain
            # subset dedupe would collapse every orderless customer into ONE
            # row and delete the rest. Keyed rows are de-fanned; unkeyed rows
            # are passed through untouched.
            null_key_mask = working_frame[dedupe_on].isnull().any(axis=1)

            keyed_rows = (
                working_frame.loc[~null_key_mask]
                .drop_duplicates(subset=dedupe_on, keep="first")
            )
            unkeyed_rows = working_frame.loc[null_key_mask]

            working_frame = pd.concat(
                [keyed_rows, unkeyed_rows],
                ignore_index=True,
            )
            deduped_rows = len(working_frame)

        aggregated = working_frame.groupby(keys, dropna=False).agg(**named).reset_index()
    except Exception as exc:  # noqa: BLE001 - pandas raises many types here
        # Type only: a pandas aggregation error can quote the offending value.
        raise ToolFailure(
            f"Aggregation failed ({type(exc).__name__}). Check that each "
            f"aggregated column suits its function."
        )

    # A LEFT-JOIN group with no related rows sums to 0 rather than NaN, which
    # is what "total spend of a customer with no orders" means. mean/median/std
    # stay NaN on purpose: an average of nothing is undefined, and filling it
    # with 0 would feed a false signal into training.
    for new_name, (_, function) in named.items():
        if function in {"sum", "count", "nunique"}:
            aggregated[new_name] = aggregated[new_name].fillna(0)
        if function in {"sum", "mean", "median", "std"}:
            try:
                aggregated[new_name] = pd.to_numeric(
                    aggregated[new_name], errors="raise"
                )
            except Exception as exc:  # noqa: BLE001
                raise ToolFailure(
                    f"Aggregation result '{new_name}' could not be converted to "
                    f"numeric for {function} ({type(exc).__name__})."
                )

    session.df = aggregated

    # The frame is now one row per group, so the earlier fan-out no longer
    # applies. Clearing it stops a second aggregate_rows being refused on
    # stale evidence - and stops a stale key being suggested.
    if session.grain:
        session.grain["multiplied_tables"] = []
        session.grain["fanned_out_tables"] = []
        session.grain["aggregated_to"] = list(keys)

    session.transform_log.append(
        f"aggregate_rows -> grouped by {', '.join(keys)}"
        + (f"; de-fanned on {', '.join(dedupe_on)} ({before_rows} -> {deduped_rows} rows)"
           if dedupe_on else "")
        + f"; {deduped_rows} -> {len(aggregated)} rows"
    )

    return {
        "ok": True,
        "dataset": _frame_summary(session.df),
        "deduped_rows": deduped_rows,
        "summary": (
            f"Grouped by {', '.join(keys)}: {before_rows} rows"
            + (
                f" de-fanned on {', '.join(dedupe_on)} to {deduped_rows}, then"
                if dedupe_on
                else ""
            )
            + f" collapsed to {len(aggregated)}, "
            f"{len(named)} aggregate column(s) created."
        ),
    }


# ---------------------------------------------------------------------------
# TOOLS - validation
# ---------------------------------------------------------------------------


@etl_tool(
    name="validate_dataset",
    description=(
        "Check the working dataset: row and column counts, per-column nulls "
        "and distinct counts, duplicate rows, data types, and whether it has "
        "enough rows to train on. Pass target_column to also check that the "
        "target is usable. Run this before finish_etl."
    ),
    parameters={
        "type": "object",
        "properties": {
            "target_column": {
                "type": "string",
                "description": "Optional. The column you intend to predict.",
            }
        },
        "required": [],
    },
    requires_dataset=True,
)
def _validate_dataset(
    session: ETLSession, target_column: Optional[str] = None
) -> Dict[str, Any]:
    frame = session.df
    summary = _frame_summary(frame)

    problems: List[str] = []
    if summary["duplicate_rows"]:
        problems.append(f"{summary['duplicate_rows']} duplicate row(s).")

    fully_null = [c["name"] for c in summary["column_details"] if c["null_percentage"] == 100.0]
    if fully_null:
        problems.append(f"Entirely null column(s): {', '.join(fully_null)}.")

    constant = [
        c["name"]
        for c in summary["column_details"]
        if c["distinct_count"] <= 1 and c["null_percentage"] < 100.0
    ]
    if constant:
        problems.append(f"Constant column(s), no predictive value: {', '.join(constant)}.")

    # The platform's own training gate - the same one the trainer applies.
    try:
        from main import assess_training_readiness  # noqa: PLC0415 (deliberate)

        readiness = assess_training_readiness(frame)
    except Exception as exc:  # noqa: BLE001
        logger.info("Readiness check unavailable: %s", exc)
        readiness = {"ready": None, "reason": "Readiness check unavailable."}

    # A row shortage is a real issue and must be reported: otherwise the model
    # is told "no issues found" while the platform considers the dataset
    # untrainable, and it keeps calling cleanup tools to fix a phantom.
    if readiness.get("ready") is False and readiness.get("reason"):
        problems.append(str(readiness["reason"]))

    target_report: Optional[Dict[str, Any]] = None
    if target_column:
        name = str(target_column)
        if name not in frame.columns:
            problems.append(f"Target column '{name}' is not in the dataset.")
        else:
            numeric = bool(pd.api.types.is_numeric_dtype(frame[name]))
            nulls = int(frame[name].isnull().sum())
            target_report = {
                "column": name,
                "dtype": str(frame[name].dtype),
                "numeric": numeric,
                "null_count": nulls,
                "distinct_count": int(frame[name].nunique(dropna=True)),
            }
            if not numeric:
                problems.append(
                    f"Target '{name}' is not numeric. The platform currently "
                    f"trains regression only, so finish_etl will reject it. "
                    f"Convert it with convert_dtype or choose another target."
                )
            if nulls:
                problems.append(f"Target '{name}' has {nulls} null value(s).")

    # A clean bill of health: nothing to fix, and a usable target. Record the
    # dataset's fingerprint so a later cleanup call can be recognised as
    # redundant. Anything less clears it - a validation that found problems is
    # not a licence to stop.
    # Only what the CLEANUP tools could actually fix may hold the guard open.
    # Everything else is reported to the model but must not keep the flag off,
    # or the agent loops forever trying to clean its way out of a problem that
    # cleanup cannot touch.
    #
    # This is exactly the live 5-row failure: the dataset was below
    # MIN_TRAINING_ROWS so readiness.ready was False, so the flag was never
    # set - and no amount of drop_rows_with_nulls can add a row.
    fixable = [
        issue
        for issue in problems
        if "duplicate row" in issue or issue.startswith(("Entirely null column", "Target"))
    ]

    if (
        not fixable
        and target_report is not None
        and target_report.get("numeric")
        and not target_report.get("null_count")
    ):
        session.clean_validation = session.dataset_fingerprint()
    else:
        session.clean_validation = None

    return {
        "ok": True,
        "dataset": summary,
        "target": target_report,
        "training_readiness": readiness,
        "problems": problems,
        "fixable_problems": fixable,
        "validated_clean": bool(session.clean_validation),
        "summary": (
            f"{summary['rows']} rows x {summary['columns']} columns; "
            + (f"{len(problems)} issue(s): " + " ".join(problems) if problems else "no issues found.")
        ),
    }


# ---------------------------------------------------------------------------
# TOOLS - terminal
# ---------------------------------------------------------------------------


@etl_tool(
    name="finish_etl",
    description=(
        "Save the working dataset and end the run. Writes the CSV, creates the "
        "project, and returns the file_id the AutoML side needs. Call this "
        "exactly once, as your final action, after validate_dataset comes back "
        "clean. The target column must already be numeric."
    ),
    parameters={
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
            "target_column": {
                "type": "string",
                "description": "A column of the current dataset, by its aliased name.",
            },
            "summary": {
                "type": "string",
                "description": "Two or three sentences on what you did and why.",
            },
        },
        "required": ["project_name", "target_column", "summary"],
    },
    requires_dataset=True,
    terminal=True,
)
def _finish_etl(
    session: ETLSession, project_name: str, target_column: str, summary: str
) -> Dict[str, Any]:
    if session.db is None:
        raise ToolFailure("No database session is available to save the dataset.")

    target = str(target_column)
    session.check_columns([target])

    # Internal grain keys were kept so a measure could be de-fanned. They are
    # not part of the deliverable: drop them before the CSV is written, unless
    # the caller explicitly asked for one (in which case it is not internal).
    output_frame = session.df
    internal_present = [
        column
        for column in session.internal_columns
        if column in output_frame.columns and column != target
    ]
    if internal_present:
        remaining = [c for c in output_frame.columns if c not in internal_present]
        if not remaining:
            raise ToolFailure(
                "After removing the internal join keys there would be no columns "
                "left. Select the columns you actually want in the dataset."
            )
        output_frame = output_frame.drop(columns=internal_present)

    if target in session.internal_columns:
        raise ToolFailure(
            f"'{target}' is an internal join key kept only for grain-aware "
            f"aggregation; it is not a measure and cannot be the target. Choose "
            f"an aggregated column, or rebuild selecting that column explicitly."
        )

    # Reuse the EXACT path /datamodel/extract uses: CSV write, dataset profile,
    # Project row, DatasetProfile row. Imported, not copied, not modified.
    from fastapi import HTTPException  # noqa: PLC0415
    from routers.datamodel import _create_project_from_frame  # noqa: PLC0415

    try:
        created = _create_project_from_frame(
            db=session.db,
            df=output_frame,
            project_name=str(project_name),
            target_column=target,
            source_label="etl_agent",
        )
    except HTTPException as exc:
        # Recoverable: bad target, too many rows, non-numeric target. Hand the
        # reason back so the agent can fix it while steps remain.
        raise ToolFailure(str(exc.detail))

    created["etl_summary"] = str(summary)[:2000]
    created["internal_columns_removed"] = list(internal_present)
    created["transformations_applied"] = list(session.transform_log)
    created["build"] = session.build_info
    session.result = created

    return {
        "ok": True,
        "terminal": True,
        "file_id": created["file_id"],
        "project_id": created["project_id"],
        "rows": created["rows"],
        "columns": created["columns"],
        "target_column": created["target_column"],
        "summary": (
            f"Saved '{project_name}': {created['rows']} rows x "
            f"{created['columns']} columns, target {target}, "
            f"file_id {created['file_id']}."
        ),
    }


# ---------------------------------------------------------------------------
# Schema + dispatch
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# THE PRIVACY BOUNDARY
#
# Everything a tool returns passes through sanitize_for_llm() before it can
# become a message. Privacy therefore does NOT depend on 24 tools each
# behaving correctly - it depends on this one function.
#
# The rule is an allowlist, not a blocklist: a key that is not named below is
# DROPPED, whatever it holds. A new tool that returns a new field will have
# that field withheld until it is registered here. That is the intended
# failure mode - silence rather than disclosure.
#
# Reproduced leak this closes: pandas' get_dummies() turns cell values into
# column names ("CustomerName_Rahul Vora"), and column names were allowed
# through. Now every name is checked against ETLSession.safe_names, which only
# ever holds names the database reported or the model itself chose.
# ---------------------------------------------------------------------------

# Numeric and boolean keys need no listing beyond being allowed: a count
# cannot carry a cell value. Strings are the risk, so each string key is
# classified.

_NAME_KEYS = {
    "table", "tables", "table_names", "name", "column", "columns",
    "column_names", "candidate_key_columns", "join_keys_used_internally",
    "join_order", "group_by", "left_table", "left_column", "right_table",
    "right_column", "base_table", "base_column", "related_table",
    "related_column", "target_column", "new_column", "schema",
    # grain metadata: all of these hold TABLE and COLUMN names, which are
    # schema-derived and therefore pass public_name() unchanged. None of them
    # can carry a cell value - a value-derived name still placeholders.
    "column_sources", "fanned_out_tables", "multiplied_tables",
    "natural_key_columns", "dedupe_on", "internal_columns_removed",
}

_TEXT_KEYS = {
    "summary", "error", "headline", "reasons", "problems", "reason",
    "fixable_problems",
    "warnings", "message", "note",
}

_ENUM_KEYS = {
    "dtype", "type", "cardinality", "severity", "recommended", "join_type",
    "tool", "status", "error_code", "operation",
}

_ID_KEYS = {"file_id", "project_id"}

# Dicts whose KEYS are names rather than schema fields (inspect_columns keys
# its result by table name).
# column_sources is {alias: "Table.column"} and natural_key_columns is
# {table: [aliases]} - in both, the KEY is a name, not a schema field. Without
# listing them here every key would be dropped as unrecognised.
_NAME_KEYED_DICTS = {"tables", "column_sources", "natural_key_columns"}

_CONTAINER_KEYS = {
    "dataset", "column_details", "columns", "tables", "relationships",
    "profile", "recommendation", "target", "training_readiness", "problems",
    "reasons", "warnings", "join_order", "column_names", "fixable_problems",
    "candidate_key_columns", "join_keys_used_internally", "group_by",
}

_NUMERIC_KEYS = {
    "rows", "columns", "row_count", "column_count", "table_count",
    "duplicate_rows", "relationship_count", "estimated_rows", "null_count",
    "deduped_rows",
    "null_percentage", "distinct_count", "distinct_percentage",
    "n_rows", "n_columns", "n_folds",
    "base_rows", "related_rows", "base_matched_rows", "related_matched_rows",
    "base_unmatched_rows", "related_unmatched_rows", "base_null_keys",
    "related_null_keys", "base_distinct_keys", "related_distinct_keys",
    "max_rows_per_key", "match_percentage", "inner_result_rows",
    "left_result_rows",
}

_BOOL_KEYS = {
    "ok", "terminal", "nullable", "primary_key", "looks_like_key", "numeric",
    "ready", "base_key_unique", "related_key_unique", "row_multiplication",
    "validated_clean",
}

ALLOWED_KEYS = (
    _NAME_KEYS | _TEXT_KEYS | _ENUM_KEYS | _ID_KEYS
    | _CONTAINER_KEYS | _NUMERIC_KEYS | _BOOL_KEYS
)

_ENUM_RE = re.compile(r"^[A-Za-z0-9_\-\[\]()., :]{1,64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,80}$")

WITHHELD = "withheld"


def sanitize_for_llm(session: ETLSession, result: Any) -> Dict[str, Any]:
    """The only gate between a tool result and the LLM conversation.

    Returns a new dict containing nothing but allowlisted, name-checked,
    length-capped metadata. Anything dropped is reported under "_withheld" so
    a missing field is visible in testing rather than silent.
    """
    withheld: List[str] = []
    clean = _sanitize_dict(session, result if isinstance(result, dict) else {},
                           withheld, "", name_keyed=False)
    if withheld:
        clean["_withheld"] = sorted(set(withheld))[:15]
    return clean


def _sanitize_dict(
    session: ETLSession,
    node: Dict[Any, Any],
    withheld: List[str],
    path: str,
    name_keyed: bool,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for raw_key, value in node.items():
        key = str(raw_key)
        here = f"{path}.{key}" if path else key

        if name_keyed:
            # The key is a table/column name, not a schema field.
            out[session.public_name(key)] = _sanitize_value(
                session, "name", value, withheld, here
            )
            continue

        if key not in ALLOWED_KEYS:
            withheld.append(here)
            continue

        out[key] = _sanitize_value(session, key, value, withheld, here)
    return out


def _sanitize_value(
    session: ETLSession, key: str, value: Any, withheld: List[str], path: str
) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value

    if isinstance(value, str):
        if key in _NAME_KEYS:
            return session.public_name(value)
        if key in _TEXT_KEYS:
            return session.scrub_text(value)
        if key in _ENUM_KEYS:
            return value if _ENUM_RE.match(value) else WITHHELD
        if key in _ID_KEYS:
            return value if _ID_RE.match(value) else WITHHELD
        # An allowlisted key holding an unexpected free string: withhold it
        # rather than guess. Numeric keys carrying text land here.
        withheld.append(path)
        return WITHHELD

    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                out.append(_sanitize_dict(session, item, withheld, path, False))
            elif isinstance(item, list):
                withheld.append(path)
            else:
                out.append(_sanitize_value(session, key, item, withheld, path))
        return out

    if isinstance(value, dict):
        return _sanitize_dict(
            session, value, withheld, path, key in _NAME_KEYED_DICTS
        )

    # A DataFrame, Series, ndarray, Timestamp, Decimal - anything else at all.
    withheld.append(path)
    return WITHHELD


def get_tools_schema(session: Optional[ETLSession] = None) -> List[Dict[str, Any]]:
    """OpenAI tool schema for the tools that are usable right now.

    Before a dataset exists, only the discovery and build tools are offered.
    Once build_dataset has run, the transform, validate and finish tools are
    added - including all 13 tools from transform_mcp_server, registered there
    and executed by its own execute_tool.
    """
    has_dataset = bool(session and session.has_dataset())

    # Once the dataset has cleanly validated and has not changed since, the
    # cleanup and validation tools are withheld ENTIRELY. Refusing them at
    # execution time still burned a step each; not offering them means the
    # model cannot spend a step on one at all. finish_etl and the reshaping
    # tools stay available, so a genuine further change is still possible -
    # and making one clears the flag, bringing these back.
    withheld: set = set()
    if has_dataset and _validation_still_clean(session):
        withheld = set(REDUNDANT_AFTER_VALIDATION) - {TERMINAL_TOOL}

    tools = [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["parameters"],
            },
        }
        for spec in ETL_TOOLS.values()
        if (has_dataset or not spec["requires_dataset"])
        and spec["name"] not in withheld
    ]

    if has_dataset:
        tools.extend(
            tool
            for tool in _restricted_transform_schema()
            if tool.get("function", {}).get("name") not in withheld
        )

    return tools


# Arguments the ETL Agent is not allowed to use, whatever the underlying
# transform supports. Enforced in execute_tool as well as hidden from the
# schema, because a schema is advice and a model can still send anything.
FORBIDDEN_TRANSFORM_ARGS = {
    # get_dummies() names each new column "<column>_<value>", which turns cell
    # values into column names and hence into LLM-visible metadata. Reproduced
    # as a live leak. "label" produces integer codes and is safe - and is the
    # right choice for AutoML anyway.
    ("encode_categorical", "method"): {"onehot"},
}


def _restricted_transform_schema() -> List[Dict[str, Any]]:
    """transform_mcp_server's schema with the unsafe options removed.

    Deep-copied, so TRANSFORM_TOOLS itself is untouched and the transform
    tools stay fully usable by the Transform Agent and anything else.
    """
    tools = copy.deepcopy(transform_mcp_server.get_tools_schema())

    for tool in tools:
        function = tool.get("function", {})
        name = function.get("name")
        properties = (function.get("parameters") or {}).get("properties") or {}

        for (tool_name, argument), banned in FORBIDDEN_TRANSFORM_ARGS.items():
            if name != tool_name or argument not in properties:
                continue
            spec = properties[argument]
            if isinstance(spec.get("enum"), list):
                spec["enum"] = [v for v in spec["enum"] if v not in banned]
            spec["description"] = (
                (spec.get("description") or "")
                + " Only 'label' is available in the ETL Agent: one-hot"
                " encoding would expose data values as column names."
            ).strip()

    return tools


def _forbidden_argument(tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
    """The refusal reason if this call uses a banned argument, else None."""
    for (banned_tool, argument), banned_values in FORBIDDEN_TRANSFORM_ARGS.items():
        if tool_name != banned_tool:
            continue
        value = arguments.get(argument)
        if isinstance(value, str) and value.strip().lower() in banned_values:
            return (
                f"'{argument}={value}' is not available in the ETL Agent, "
                f"because it would turn data values into column names. "
                f"Allowed value(s): "
                f"{', '.join(sorted(_allowed_values(banned_tool, argument)))}."
            )
    return None


def _unsafe_dedupe(session: ETLSession, tool_name: str, arguments: Dict[str, Any]):
    """Refuse drop_duplicates that would delete real rows, not duplicates.

    This is the reproduced bug: drop_duplicates(["customer_id"]) on a joined
    Customers/Orders/OrderItems frame keeps the FIRST order per customer and
    silently deletes the rest, so the total came out as one order's amount.

    Allowed:
      * no subset, or ["*"] - full-row dedupe only removes identical rows
      * a subset that is a source table's natural key - that is the de-fan
      * anything at all when the frame has no recorded fan-out

    Refused:
      * a subset on a fanned-out frame that is not a natural key
    """
    if tool_name != "drop_duplicates":
        return None

    columns = arguments.get("columns")
    if not columns or columns == ["*"] or not isinstance(columns, list):
        return None

    multiplied = session.grain.get("multiplied_tables") or []
    if not multiplied:
        return None

    subset = {str(c) for c in columns}
    natural_keys: Dict[str, List[str]] = session.grain.get("natural_key_aliases") or {}
    base_table = session.grain.get("base_table")

    for table, aliases in natural_keys.items():
        # A CHILD table's key is the de-fan - one row per order - so allow it.
        # The BASE table's key is not: the base grain is what the fan-out hangs
        # off, so keeping one row per customer throws that customer's other
        # orders away. That is the reported bug, and CustomerID being a genuine
        # primary key is exactly why it looks legitimate.
        if aliases and subset == set(aliases) and table != base_table:
            return None

    suggestions = [
        f"{table}: dedupe_on={aliases}"
        for table, aliases in natural_keys.items()
        if table in multiplied and aliases
    ]

    return (
        f"Refused: rows of {', '.join(multiplied)} are repeated by a one-to-many "
        f"join, and de-duplicating on {', '.join(sorted(subset))} would delete "
        f"real rows rather than duplicates - it keeps only the first record per "
        f"group. To collapse a one-to-many join correctly, call aggregate_rows "
        f"with dedupe_on set to the source table's natural key"
        + (f" ({'; '.join(suggestions)})" if suggestions else "")
        + ". Full-row de-duplication (omit columns, or use ['*']) is allowed."
    )


# Tools whose only purpose is to remove problems that validation reports. Once
# validation says there are none, calling one of these again cannot change
# anything - which is exactly the loop observed against the live model:
# validate -> drop_rows_with_nulls (no change) -> validate -> ... to MAX_STEPS.
CLEANUP_TOOLS = {
    "drop_rows_with_nulls",
    "fill_nulls",
    "drop_duplicates",
    "remove_outliers",
    "strip_whitespace",
    "lowercase_columns",
}

# Guarded after a clean validation. Deliberately NOT the reshaping tools
# (select_columns, drop_columns, rename_columns, convert_dtype, aggregate_rows,
# add_calculated_column, filter_rows, ...): the model must stay free to make a
# genuine change, and doing so clears the flag and re-opens validation.
# finish_etl is never guarded.
REDUNDANT_AFTER_VALIDATION = CLEANUP_TOOLS | {"validate_dataset"}

ALREADY_VALIDATED_MESSAGE = (
    "The dataset has already passed validation with no issues. Call finish_etl "
    "now unless you have made a new transformation."
)


def _protect_grain_keys(session, tool_name, arguments) -> List[str]:
    if tool_name not in {"select_columns", "drop_columns"}:
        return []

    columns = arguments.get("columns")
    if not isinstance(columns, list) or columns == ["*"]:
        return []

    multiplied = set(session.grain.get("multiplied_tables") or [])
    if not multiplied or session.df is None:
        return []

    natural_keys = session.grain.get("natural_key_aliases") or {}

    protected = {
        alias
        for table, aliases in natural_keys.items()
        if table in multiplied
        for alias in aliases
        if alias in session.internal_columns and alias in session.df.columns
    }

    if not protected:
        return []

    named = {str(c) for c in columns}

    if tool_name == "select_columns":
        missing = sorted(protected - named)
        arguments["columns"] = [str(c) for c in columns] + missing
        return missing

    removed = sorted(protected & named)
    arguments["columns"] = [str(c) for c in columns if str(c) not in protected]
    return removed


def _validation_still_clean(session: ETLSession) -> bool:
    """True when validation passed AND the dataset has not changed since.

    A single source of truth for both the schema filter and the execution
    guard, so the two can never disagree about whether the run is finished.
    """
    if not session.clean_validation:
        return False
    if session.dataset_fingerprint() != session.clean_validation:
        # Changed without the flag being cleared: treat it as changed.
        session.clean_validation = None
        return False
    return True


def _redundant_after_clean_validation(
    session: ETLSession, tool_name: str
) -> Optional[str]:
    """Block a cleanup or validation call that cannot change anything.

    Fires only when validate_dataset reported no problems AND the dataset has
    not changed since. A real transformation moves the fingerprint, which
    clears the flag in execute_tool, so normal processing resumes by itself.

    finish_etl is never blocked, and nothing is ever called on the model's
    behalf - this only declines to run a no-op and says what to do instead.
    """
    if tool_name == TERMINAL_TOOL or tool_name not in REDUNDANT_AFTER_VALIDATION:
        return None
    if not _validation_still_clean(session):
        return None

    return (
        f"{ALREADY_VALIDATED_MESSAGE} '{tool_name}' was not executed, because "
        f"the dataset is unchanged since that validation and this call cannot "
        f"alter it. If you genuinely need a further change, make that "
        f"transformation first."
    )


def _allowed_values(tool_name: str, argument: str) -> set:
    spec = transform_mcp_server.TRANSFORM_TOOLS.get(tool_name, {})
    properties = (spec.get("parameters") or {}).get("properties") or {}
    values = set((properties.get(argument) or {}).get("enum") or [])
    return values - FORBIDDEN_TRANSFORM_ARGS.get((tool_name, argument), set())


def get_tool_names() -> List[str]:
    return list(ETL_TOOLS.keys()) + transform_mcp_server.get_tool_names()


def execute_tool(
    session: ETLSession, tool_name: str, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    """Run one tool. Never raises - failures come back as {"ok": False, ...}."""
    if not isinstance(arguments, dict):
        return {
            "ok": False,
            "error": "Arguments must be a JSON object.",
            "tool": tool_name,
        }

    refusal = _forbidden_argument(tool_name, arguments)
    if refusal:
        return {"ok": False, "error": refusal, "tool": tool_name}

    refusal = _unsafe_dedupe(session, tool_name, arguments)
    if refusal:
        return {"ok": False, "error": refusal, "tool": tool_name}

    _protected = _protect_grain_keys(session, tool_name, arguments)

    # Fingerprint before the call, so a tool that really changes the dataset
    # clears the clean-validation flag no matter which branch executes it.
    fingerprint_before = session.dataset_fingerprint()
        

    # Names the MODEL chose are safe to show back to it. Registering them here
    # keeps the boundary from placeholdering a rename or an aggregate the model
    # asked for by name.
    session.register_names(arguments.get("new_column"), arguments.get("target_column"))
    mapping = arguments.get("mapping")
    if isinstance(mapping, dict):
        session.register_names(*mapping.values())
    for aggregation in arguments.get("aggregations") or []:
        if isinstance(aggregation, dict):
            session.register_names(
                aggregation.get("new_name"),
                f"{aggregation.get('column')}_{aggregation.get('function')}",
            )

    def _note_dataset_change() -> None:
        """Clear the clean-validation flag if the dataset actually moved.

        validate_dataset is exempt: it is what SETS the flag, and it never
        mutates the dataset.
        """
        if tool_name == "validate_dataset":
            return
        if session.dataset_fingerprint() != fingerprint_before:
            session.clean_validation = None

    spec = ETL_TOOLS.get(tool_name)

    if spec is not None:
        if spec["requires_dataset"] and not session.has_dataset():
            return {
                "ok": False,
                "error": (
                    f"'{tool_name}' needs a dataset. Call build_dataset first."
                ),
                "tool": tool_name,
            }
        try:
            outcome = spec["function"](session, **arguments)
            _note_dataset_change()
            return outcome
        except ToolFailure as exc:
            return {"ok": False, "error": str(exc), "tool": tool_name}
        except TypeError as exc:
            # The exception text is not forwarded: name the accepted
            # parameters instead, which is both safe and more useful.
            accepted = sorted((spec["parameters"].get("properties") or {}).keys())
            return {
                "ok": False,
                "error": (
                    f"Wrong arguments for '{tool_name}' ({type(exc).__name__}). "
                    f"Accepted parameters: {', '.join(accepted) or 'none'}."
                ),
                "tool": tool_name,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("ETL tool %s failed", tool_name)
            return {
                "ok": False,
                "error": f"'{tool_name}' failed unexpectedly: {type(exc).__name__}.",
                "tool": tool_name,
            }

    # --- delegate to the existing transform registry ----------------------
    if tool_name in transform_mcp_server.TRANSFORM_TOOLS:
        if not session.has_dataset():
            return {
                "ok": False,
                "error": f"'{tool_name}' needs a dataset. Call build_dataset first.",
                "tool": tool_name,
            }
        before = session.df.shape
        try:
            session.df = transform_mcp_server.execute_tool(
                tool_name, session.df, arguments
            )
        except Exception as exc:  # noqa: BLE001
            # The exception type only. pandas puts offending CELL VALUES into
            # its messages, so the text itself must never be forwarded.
            return {
                "ok": False,
                "error": (
                    f"'{tool_name}' failed ({type(exc).__name__}). Check the "
                    f"column names and arguments against the dataset."
                ),
                "tool": tool_name,
            }

        _note_dataset_change()

        after = session.df.shape
        session.transform_log.append(
            f"{tool_name} -> {before[0]}x{before[1]} to {after[0]}x{after[1]}"
        )
        return {
            "ok": True,
            "tool": tool_name,
            "dataset": _frame_summary(session.df),
            "summary": (
                f"{tool_name}: {before[0]} rows x {before[1]} cols -> "
                f"{after[0]} rows x {after[1]} cols."
            ),
        }

    return {
        "ok": False,
        "error": (
            f"'{tool_name}' is not a registered tool. Choose one of the tools "
            f"you were given."
        ),
        "tool": tool_name,
    }


# ---------------------------------------------------------------------------
# LLM provider
# ---------------------------------------------------------------------------


def resolve_llm_client():
    """Get a tool-calling-capable client.

    Reuses main._resolve_llm_client_standalone(), which returns
    (client, model, provider) with priority Groq -> OpenAI -> Gemini.

    OpenAI is preferred here and asked for first. Gemini is refused outright:
    _call_gemini_text_standalone is text-only, so there is no function-calling
    path, and a silent text fallback would let the model invent a plan it never
    measured. Better a clear 503.
    """
    from main import _resolve_llm_client_standalone  # noqa: PLC0415

    client, model, provider = _resolve_llm_client_standalone(force_provider="openai")

    if client is None:
        client, model, provider = _resolve_llm_client_standalone()

    if client is None or provider not in {"openai", "groq"}:
        raise ETLAgentError(
            "The ETL Agent needs a provider that supports tool calling. "
            "Set OPENAI_API_KEY (preferred) or GROQ_API_KEY. "
            f"Currently resolved provider: {provider or 'none'}."
        )

    return client, model, provider


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the ETL agent of an AutoML platform. You turn a set
of database tables into one flat, clean training dataset, then save it.

You cannot see any data. You will never be shown a database row, a cell value,
a customer name, an email or an ID. You receive structure and counts only. Do
not ask for data and do not guess what values look like. You never write SQL or
Python; your only actions are calls to the tools you have been given.

Work in this order:

1. UNDERSTAND. inspect_tables, then inspect_table or inspect_columns on the
   tables that matter. Find the key columns.
2. RELATE. find_declared_relationships first. Whether or not it finds anything,
   verify every join you intend to use with analyze_relationship. Its
   recommendation is computed from the real data by a fixed rule table - prefer
   it, and if you override it say why in your final summary.
3. BUILD. Choose the base table: the grain of the dataset must match what is
   being predicted - one output row per row of the base table. Call
   build_dataset. It returns column_names: those are the dataset's real column
   names. Copy them exactly, character for character, in every later call.
   Never construct, guess or infer a column name from the table and column it
   came from - the aliasing rule is not something you can reproduce, and a
   name you invented will not match.
4. CLEAN. Use the transformation tools for what the request asks. Typically:
   drop identifier and free-text columns with select_columns or drop_columns,
   fill or drop nulls, remove duplicates, convert types, and aggregate a
   one-to-many side with aggregate_rows so the grain stays correct.
5. VALIDATE. Call validate_dataset with the target column. Fix what it reports.
   If validate_dataset returns "no issues found", the target is valid, and the
   dataset is ready for saving, do NOT perform another cleanup or validation
   call. Proceed directly to finish_etl.
6. FINISH. When validation reports no issues and the target is valid, call
   finish_etl immediately. Do not call drop_rows_with_nulls, validate_dataset,
   or another transformation again unless you have made a new transformation
   after the previous validation.

Rules:
- At most {max_steps} rounds. You may issue up to {max_calls} tool calls in one
  round when they are independent; batch them.
- Never propose a join you have not verified with analyze_relationship.

GRAIN - the rule that decides whether your numbers are right:

A measure may only be aggregated at the grain of the table it came from.

When you join a parent to a child (one customer to many orders, one order to
many order items), every PARENT row is repeated once per child row. Summing a
parent's measure over that flat dataset counts it once per child, which
silently multiplies the answer. build_dataset tells you when this applies:

  column_sources        which source table each output column came from
  multiplied_tables     tables whose rows ARE repeated - their measures are
                        at risk
  natural_key_columns   the column that identifies one row of each table

So to total an order amount per customer after order items have been joined:
pass dedupe_on set to the order-id column from natural_key_columns. That
collapses the repeated order rows back to one row per order first, and only
then sums them per customer. aggregate_rows refuses a sum, mean, median, std
or count of a measure from a multiplied table when dedupe_on is missing, and
the error names the column to use.

NEVER use drop_duplicates on the grain you are aggregating TO in order to get
"one row per entity". drop_duplicates on a customer id keeps the first order
and deletes that customer's other orders - it destroys data rather than
de-duplicating it, and the total comes out as one order's amount. The tool
refuses this on a joined dataset. Full-row de-duplication (no columns, or
['*']) is always fine.

Some columns are kept in the dataset only so this de-fanning is possible;
build_dataset names them in join_keys_used_internally. They are removed from
the saved file automatically, so leave them alone - do not drop them, and do
not choose one as the target.
- The platform currently trains regression only, so the target column must be
  numeric when you call finish_etl. A 0/1 flag is numeric and fine.
- A tool that returns "ok": false has failed. Read the error and adapt; do not
  repeat the same call unchanged.
- Drop columns that cannot predict anything: primary keys, names, free text,
  and anything derived from the target.
"""


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def run_etl_agent(
    session: ETLSession,
    user_request: str,
    max_steps: int = MAX_STEPS,
) -> Dict[str, Any]:
    """Observe -> think -> act, until finish_etl or the step ceiling.

    Returns a dict with:
        status   "complete" | "incomplete" | "partial" | "error"
        result   the finish_etl payload (file_id, project_id, ...) or None
        trace    one entry per tool call: tool, arguments, ok, summary
        dataset  a summary of the working dataset, if one was built
    """
    client, model, provider = resolve_llm_client()
    max_steps = max(1, min(int(max_steps or MAX_STEPS), MAX_STEPS))

    table_hint = (
        ", ".join(session.allowed_tables)
        if session.allowed_tables
        else "discover them with inspect_tables"
    )

    messages: List[Dict[str, Any]] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                max_steps=max_steps, max_calls=MAX_TOOL_CALLS_PER_STEP
            ),
        },
        {
            "role": "user",
            "content": (
                f"Request: {user_request.strip()}\n"
                f"Database type: {session.db_type}\n"
                f"Schema: {session.schema}\n"
                f"Tables available: {table_hint}\n"
                "Begin."
            ),
        },
    ]

    trace: List[Dict[str, Any]] = []
    notes: List[str] = []

    # Retry guard. Keyed on the exact call, and populated ONLY by a failed one:
    # repeating a successful call is wasteful but it is not a loop, and
    # validate_dataset legitimately returns something different after a
    # transform. See _call_key.
    seen_failures: Dict[str, Dict[str, Any]] = {}

    for step in range(1, max_steps + 1):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=get_tools_schema(session),
                tool_choice="auto",
                temperature=LLM_TEMPERATURE,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ETL agent LLM call failed at step %s: %s", step, exc)
            return _envelope(
                session,
                status="error",
                message=f"{LLM_TIMEOUT_NOTE} ({type(exc).__name__})",
                trace=trace,
                steps_used=step - 1,
                provider=provider,
                model=model,
                notes=notes,
            )

        choice = completion.choices[0].message
        tool_calls = list(getattr(choice, "tool_calls", None) or [])

        if not tool_calls:
            return _envelope(
                session,
                status="incomplete",
                message=(
                    "The agent replied without calling a tool, so the run "
                    "stopped early. Restate the request, naming the tables and "
                    "the column to predict."
                ),
                trace=trace,
                steps_used=step,
                provider=provider,
                model=model,
                notes=notes,
                assistant_text=(choice.content or "").strip()[:1500],
            )

        if len(tool_calls) > MAX_TOOL_CALLS_PER_STEP:
            notes.append(
                f"Step {step}: {len(tool_calls)} tool calls requested; only the "
                f"first {MAX_TOOL_CALLS_PER_STEP} were executed."
            )
            tool_calls = tool_calls[:MAX_TOOL_CALLS_PER_STEP]

        messages.append(
            {
                "role": "assistant",
                "content": choice.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in tool_calls
                ],
            }
        )

        for call in tool_calls:
            name = call.function.name
            try:
                arguments = json.loads(call.function.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("not an object")
            except (TypeError, ValueError):
                arguments = {}
                result = {
                    "ok": False,
                    "tool": name,
                    "error": (
                        "The arguments were not a valid JSON object. Send them "
                        "again, matching the tool's schema exactly."
                    ),
                }
            else:
                key = _call_key(name, arguments)
                prior = seen_failures.get(key)

                if prior and prior["count"] >= 2:
                    # Third identical failing call. Stop rather than spend the
                    # remaining steps on it.
                    attempts = prior["count"] + 1
                    notes.append(
                        f"Stopped at step {step}: '{name}' was called with "
                        f"identical arguments {attempts} times and failed each time."
                    )
                    trace.append(
                        {
                            "step": step,
                            "tool": name,
                            "arguments": arguments,
                            "ok": False,
                            "summary": (
                                f"Blocked - identical failing call repeated "
                                f"{attempts} times. {prior['error']}"
                            ),
                        }
                    )
                    return _envelope(
                        session,
                        status="partial",
                        message=(
                            f"The agent repeated the same failing call to "
                            f"'{name}' {attempts} times and was stopped. The "
                            f"underlying error was: {prior['error']}"
                        ),
                        trace=trace,
                        steps_used=step,
                        provider=provider,
                        model=model,
                        notes=notes,
                    )

                if prior:
                    # Second identical failing call. Do not run it again - say
                    # so in different words, because repeating the original
                    # message invites the same response.
                    prior["count"] += 1
                    result = {
                        "ok": False,
                        "tool": name,
                        "error": (
                            f"You already made this exact call and it failed: "
                            f"{prior['error']} Repeating it will not work. Change "
                            f"the arguments or choose a different tool. If you are "
                            f"sure a table or column named in the error does exist, "
                            f"then the problem lies in the other arguments, not in "
                            f"that name."
                        ),
                    }
                else:
                    result = execute_tool(session, name, arguments)
                    if not result.get("ok"):
                        seen_failures[key] = {
                            "count": 1,
                            "error": str(result.get("error", "")).strip(),
                        }

            # ---- THE PRIVACY BOUNDARY --------------------------------------
            # Nothing a tool produced reaches the model except through here.
            # The trace is built from the sanitized copy too, so what the
            # operator sees is exactly what OpenAI saw.
            safe_result = sanitize_for_llm(session, result)

            trace.append(
                {
                    "step": step,
                    "tool": name,
                    "arguments": arguments,
                    "ok": bool(result.get("ok")),
                    "summary": safe_result.get("summary")
                    or safe_result.get("error", ""),
                }
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": name,
                    "content": json.dumps(safe_result, default=str)[:24000],
                }
            )

            if name == TERMINAL_TOOL and result.get("ok"):
                return _envelope(
                    session,
                    status="complete",
                    message="ETL complete. The dataset is saved and ready for AutoML.",
                    trace=trace,
                    steps_used=step,
                    provider=provider,
                    model=model,
                    notes=notes,
                )

    return _envelope(
        session,
        status="partial",
        message=(
            f"The agent used all {max_steps} steps without calling finish_etl. "
            "The work below is real; nothing was saved. Narrow the request or "
            "re-run from the dataset described here."
        ),
        trace=trace,
        steps_used=max_steps,
        provider=provider,
        model=model,
        notes=notes,
    )


def _call_key(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Identity of one tool call, stable across argument ordering."""
    try:
        payload = json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr(sorted(arguments.items(), key=lambda item: str(item[0])))
    return f"{tool_name}:{payload}"


def _envelope(
    session: ETLSession,
    status: str,
    message: str,
    trace: List[Dict[str, Any]],
    steps_used: int,
    provider: str,
    model: str,
    notes: List[str],
    assistant_text: str = "",
) -> Dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "result": session.result,
        "file_id": (session.result or {}).get("file_id"),
        "project_id": (session.result or {}).get("project_id"),
        "dataset": _frame_summary(session.df) if session.has_dataset() else None,
        "build": session.build_info,
        "transformations_applied": list(session.transform_log),
        "steps_used": steps_used,
        "max_steps": MAX_STEPS,
        "trace": trace,
        "notes": notes,
        "assistant_text": assistant_text,
        "provider": provider,
        "model": model,
    }
