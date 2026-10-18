"""Visual ETL Pipeline Builder - graph validation and SQL compilation.

A pipeline is a small graph of nodes:

    source -> (join) -> (transform ...) -> output

Everything here is deterministic. No LLM is involved, and no database is
contacted: the compiler works from catalog metadata (table and column names and
types that the metadata scan recorded) and produces ONE read-only SELECT made of
chained CTEs, one per node. The caller runs that statement through
services.db_connector.run_preview_query(), which is SELECT-only, read-only on
PostgreSQL, time-limited and row-bounded.

Safety rules this module enforces:
  * Table and column identifiers come from the catalog, never from the request.
    Names a user types (renamed columns) are validated and always quoted.
  * Every user VALUE (filter values, fixed fill values) is a bind parameter. No
    value is ever concatenated into the SQL text.
  * A node that has any problem produces no SQL; nodes downstream of it are
    blocked, so a broken pipeline can never run partially.

Execution path (see the report): every operation in this first slice is pushed
down into the source database. Operations that need pandas (median, mode, type
parsing, split/combine) are not offered yet.

Adding a transformation later = one entry in TRANSFORMS plus one function with
the signature `(ctx, node, input_columns) -> Step`.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from services.db_connector import MSSQL, POSTGRESQL, _quote_for_text

MAX_NODES = 60
MAX_JOIN_CONDITIONS = 5
MAX_FILTER_CONDITIONS = 10
MAX_NAME_LENGTH = 128
MAX_TEXT_VALUE = 1000
PREVIEW_MAX_ROWS = 100

_NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# Types the database cannot compare for equality. TEXT is only a problem on SQL Server,
# where it is the legacy large-object type; PostgreSQL's TEXT compares normally.
_UNCOMPARABLE_COMMON = {"image", "xml", "geography", "geometry", "json"}
_UNCOMPARABLE_MSSQL = _UNCOMPARABLE_COMMON | {"text", "ntext"}


def _uncomparable(db_type: str, data_type: str) -> bool:
    kinds = _UNCOMPARABLE_MSSQL if db_type == MSSQL else _UNCOMPARABLE_COMMON
    return (data_type or "").strip().lower() in kinds


JOIN_TYPES = ("INNER", "LEFT", "RIGHT", "FULL")
_JOIN_SQL = {
    "INNER": "INNER JOIN",
    "LEFT": "LEFT OUTER JOIN",
    "RIGHT": "RIGHT OUTER JOIN",
    "FULL": "FULL OUTER JOIN",
}


CROSS_SOURCE_MESSAGE = (
    "This join combines tables from different data sources ({names}), so it cannot run. "
    "A pipeline runs as one read-only query on one database connection, and nothing is copied or moved between sources. "
    "This also applies to sources on the same server or database. "
    "Supported options: use tables from one source; or ask your administrator to make both tables available in one database "
    "(for example a view or a copy), register that database as a source, and join there."
)


class PipelineError(Exception):
    """A request problem that is not tied to one node (bad database type...)."""


# --------------------------------------------------------------------- types


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str
    category: str  # numeric | text | datetime | boolean | other


@dataclass
class SourceTable:
    table_id: str
    schema: Optional[str]
    table: str
    columns: List[Column]
    row_count: Optional[int] = None
    row_count_is_estimate: bool = True
    # The registered data source this table belongs to. Set by the router from
    # the Catalog, never from the browser.
    source_id: Optional[str] = None


@dataclass
class Issue:
    node_id: str
    message: str
    # incomplete = not configured yet; invalid = configured wrongly;
    # blocked = an upstream node has a problem; warning = allowed but risky.
    kind: str
    field: Optional[str] = None


@dataclass
class NodeResult:
    node_id: str
    node_type: str
    columns: Optional[List[Column]] = None
    issues: List[Issue] = field(default_factory=list)
    sql: Optional[str] = None  # the CTE body, only when the node is valid
    inputs: Dict[str, str] = field(default_factory=dict)  # port -> node id
    notes: List[str] = field(default_factory=list)
    # Every registered data source this node reads from (sorted, no repeats).
    source_ids: List[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        kinds = {i.kind for i in self.issues}
        if self.columns is not None and "invalid" not in kinds and "incomplete" not in kinds and "blocked" not in kinds:
            return "ready"
        if "incomplete" in kinds and "invalid" not in kinds:
            return "not_configured"
        if "blocked" in kinds and not ({"invalid", "incomplete"} & kinds):
            return "blocked"
        return "invalid"


@dataclass
class Compiled:
    db_type: str
    order: List[str]
    results: Dict[str, NodeResult]
    cte_names: Dict[str, str]
    params: Dict[str, Any]
    graph_issues: List[Issue] = field(default_factory=list)
    # The one data source this pipeline is connected to (the only one whose
    # database a statement may be sent to). None = not enforced (old callers).
    connected_source_id: Optional[str] = None
    source_labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class Step:
    columns: List[Column]
    sql: str
    notes: List[str] = field(default_factory=list)


# --------------------------------------------------------------- type helpers


def type_category(data_type: str) -> str:
    t = (data_type or "").strip().lower()
    if any(k in t for k in ("datetime", "timestamp", "date", "time")) and "interval" not in t:
        return "datetime"
    if t in ("bit", "bool", "boolean"):
        return "boolean"
    if any(k in t for k in ("int", "decimal", "numeric", "float", "real", "double", "money", "serial")):
        return "numeric"
    if any(k in t for k in ("char", "text", "string", "clob")):
        return "text"
    return "other"


def make_column(name: str, data_type: str) -> Column:
    return Column(name=name, data_type=data_type, category=type_category(data_type))


_INTEGER_TYPES = ("bigint", "smallint", "tinyint", "int", "integer", "serial", "bigserial", "smallserial")


def _is_integer_type(data_type: str) -> bool:
    return (data_type or "").strip().lower() in _INTEGER_TYPES


# -------------------------------------------------------------- name helpers


def validate_new_name(name: Any) -> Optional[str]:
    """Why a user-typed column name is not allowed, or None if it is fine."""
    if not isinstance(name, str) or not name.strip():
        return "A column name cannot be empty."
    if name != name.strip():
        return "A column name cannot start or end with a space."
    if len(name) > MAX_NAME_LENGTH:
        return f"A column name cannot be longer than {MAX_NAME_LENGTH} characters."
    if _CONTROL_CHARS.search(name):
        return "A column name cannot contain control characters."
    return None


def _unique_name(base: str, taken: Set[str]) -> str:
    candidate = base
    n = 2
    while candidate.casefold() in taken:
        candidate = f"{base}_{n}"
        n += 1
    return candidate


# ------------------------------------------------------------------ context


class _Ctx:
    """Per-compile state: dialect, bind parameters, and issue collection."""

    def __init__(self, db_type: str):
        self.db_type = db_type
        self.params: Dict[str, Any] = {}

    def q(self, name: str) -> str:
        return _quote_for_text(self.db_type, name)

    def param(self, value: Any) -> str:
        key = f"p{len(self.params)}"
        self.params[key] = value
        return f":{key}"


class _StepError(Exception):
    """Raised inside a step builder; carries the user-facing message."""

    def __init__(self, message: str, kind: str = "invalid", field_name: Optional[str] = None):
        super().__init__(message)
        self.kind = kind
        self.field_name = field_name


def _find(columns: Sequence[Column], name: Any) -> Optional[Column]:
    if not isinstance(name, str):
        return None
    for c in columns:
        if c.name == name:
            return c
    return None


def _need_column(columns: Sequence[Column], name: Any, field_name: str) -> Column:
    if name in (None, ""):
        raise _StepError("Choose a column.", "incomplete", field_name)
    col = _find(columns, name)
    if col is None:
        raise _StepError(f"Column '{name}' is not available at this step.", "invalid", field_name)
    return col


def _select_list(ctx: _Ctx, columns: Sequence[Column], prefix: str = "") -> str:
    return ", ".join(f"{prefix}{ctx.q(c.name)}" for c in columns)


# -------------------------------------------------------------------- source


def _step_source(ctx: _Ctx, node: dict, sources: Dict[str, SourceTable],
                 connected_source_id: Optional[str] = None) -> Step:
    cfg = node.get("config") or {}
    table_id = cfg.get("table_id")
    if not table_id:
        raise _StepError("Choose a table from the catalog.", "incomplete", "table_id")
    src = sources.get(str(table_id))
    if src is None:
        raise _StepError("This table is not in the catalog for the selected source.", "invalid", "table_id")
    # The step may say which source it expects (config.source_id). A step without
    # one means the connected source, exactly as before. Either way the table must
    # really belong to that source, so a table can never be read under another
    # source's name.
    declared = cfg.get("source_id")
    expected = str(declared) if declared else connected_source_id
    if expected is not None and src.source_id is not None and src.source_id != expected:
        raise _StepError(
            "This step names one data source but the table belongs to another, so it was not used. Remove the step and add the table again.",
            "invalid",
            "source_id",
        )
    table_ref = ctx.q(src.table)
    if src.schema:
        table_ref = f"{ctx.q(src.schema)}.{table_ref}"
    cols = _select_list(ctx, src.columns)
    notes = []
    if src.row_count is not None:
        label = "estimated" if src.row_count_is_estimate else "exact at the last scan"
        notes.append(f"{src.row_count:,} rows ({label})")
    return Step(columns=list(src.columns), sql=f"SELECT {cols} FROM {table_ref}", notes=notes)


# ---------------------------------------------------------------------- join


def _compatible(a: Column, b: Column) -> bool:
    if a.category == b.category and a.category != "other":
        return True
    return a.data_type.strip().lower() == b.data_type.strip().lower()


def _step_join(ctx: _Ctx, node: dict, left: Sequence[Column], right: Sequence[Column],
               left_cte: str, right_cte: str) -> Step:
    cfg = node.get("config") or {}
    join_type = str(cfg.get("join_type") or "").upper()
    if not join_type:
        raise _StepError("Choose a join type.", "incomplete", "join_type")
    if join_type not in JOIN_TYPES:
        raise _StepError(f"Join type must be one of {', '.join(JOIN_TYPES)}.", "invalid", "join_type")
    conditions = cfg.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise _StepError("Add at least one matching column pair.", "incomplete", "conditions")
    if len(conditions) > MAX_JOIN_CONDITIONS:
        raise _StepError(f"A join can have at most {MAX_JOIN_CONDITIONS} conditions.", "invalid", "conditions")

    on_parts: List[str] = []
    notes: List[str] = []
    seen: Set[Tuple[str, str]] = set()
    for i, cond in enumerate(conditions, start=1):
        if not isinstance(cond, dict):
            raise _StepError(f"Condition {i} is malformed.", "invalid", "conditions")
        lc = cond.get("left_column")
        rc = cond.get("right_column")
        if not lc or not rc:
            raise _StepError(f"Condition {i} needs a column on both sides.", "incomplete", "conditions")
        lcol = _find(left, lc)
        rcol = _find(right, rc)
        if lcol is None:
            raise _StepError(f"Left column '{lc}' is not available.", "invalid", "conditions")
        if rcol is None:
            raise _StepError(f"Right column '{rc}' is not available.", "invalid", "conditions")
        if (lc, rc) in seen:
            raise _StepError(f"Condition {lc} = {rc} is listed twice.", "invalid", "conditions")
        seen.add((lc, rc))
        for c in (lcol, rcol):
            if _uncomparable(ctx.db_type, c.data_type):
                raise _StepError(f"Column '{c.name}' has type {c.data_type}, which cannot be used as a join key.", "invalid", "conditions")
        if not _compatible(lcol, rcol):
            notes.append(
                f"warning: {lcol.name} ({lcol.data_type}) and {rcol.name} ({rcol.data_type}) have "
                "different types; the database may convert them or fail."
            )
        on_parts.append(f"l.{ctx.q(lcol.name)} = r.{ctx.q(rcol.name)}")

    # Output names: all left columns, then right columns. A right column whose
    # name is already taken gets a "_right" suffix (made unique), and says so.
    taken = {c.name.casefold() for c in left}
    out_cols: List[Column] = list(left)
    select_parts = [f"l.{ctx.q(c.name)} AS {ctx.q(c.name)}" for c in left]
    renamed = 0
    for c in right:
        name = c.name
        if name.casefold() in taken:
            name = _unique_name(f"{c.name}_right", taken)
            renamed += 1
        taken.add(name.casefold())
        out_cols.append(Column(name, c.data_type, c.category))
        select_parts.append(f"r.{ctx.q(c.name)} AS {ctx.q(name)}")
    if renamed:
        notes.append(f"info: {renamed} right-hand column(s) had the same name as a left column and were renamed with a '_right' suffix.")

    sql = (
        f"SELECT {', '.join(select_parts)} FROM {left_cte} AS l "
        f"{_JOIN_SQL[join_type]} {right_cte} AS r ON {' AND '.join(on_parts)}"
    )
    return Step(columns=out_cols, sql=sql, notes=notes)


# --------------------------------------------------------------- transforms

_NUMERIC_OPS = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_TEXT_OPS = {"eq": "=", "ne": "<>", "contains": None, "starts_with": None, "ends_with": None}
_NULL_OPS = {"is_null": "IS NULL", "is_not_null": "IS NOT NULL"}


def allowed_operators(category: str) -> List[str]:
    if category in ("numeric", "datetime"):
        return [*_NUMERIC_OPS, *_NULL_OPS]
    if category == "text":
        return [*_TEXT_OPS, *_NULL_OPS]
    if category == "boolean":
        return ["eq", "ne", *_NULL_OPS]
    return list(_NULL_OPS)


def _coerce_value(col: Column, value: Any, field_name: str) -> Any:
    if value is None or (isinstance(value, str) and value == "" and col.category != "text"):
        raise _StepError(f"Enter a value for '{col.name}'.", "incomplete", field_name)
    if col.category == "numeric":
        if isinstance(value, bool):
            raise _StepError(f"'{value}' is not a number.", "invalid", field_name)
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise _StepError(f"'{value}' is not a number.", "invalid", field_name)
        if math.isnan(number) or math.isinf(number):
            raise _StepError("Numbers must be finite.", "invalid", field_name)
        if _is_integer_type(col.data_type):
            if number != int(number):
                raise _StepError(f"'{col.name}' holds whole numbers; '{value}' is not one.", "invalid", field_name)
            return int(number)
        return int(number) if number == int(number) and abs(number) < 1e15 else number
    if col.category == "datetime":
        text = str(value).strip()
        try:
            if len(text) == 10:
                return _dt.date.fromisoformat(text)
            return _dt.datetime.fromisoformat(text)
        except ValueError:
            raise _StepError(f"'{value}' is not a date (use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS).", "invalid", field_name)
    if col.category == "boolean":
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        raise _StepError(f"'{value}' is not true or false.", "invalid", field_name)
    if col.category == "text":
        text = str(value)
        if len(text) > MAX_TEXT_VALUE:
            raise _StepError(f"Text values are limited to {MAX_TEXT_VALUE} characters.", "invalid", field_name)
        return text
    raise _StepError(f"Column '{col.name}' (type {col.data_type}) cannot be compared to a value.", "invalid", field_name)


def _like_pattern(db_type: str, text: str, mode: str) -> str:
    escaped = text.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    if db_type == MSSQL:
        escaped = escaped.replace("[", "![")
    if mode == "contains":
        return f"%{escaped}%"
    if mode == "starts_with":
        return f"{escaped}%"
    return f"%{escaped}"


def _step_filter(ctx: _Ctx, node: dict, cols: Sequence[Column], prev: str) -> Step:
    cfg = node.get("config") or {}
    combine = str(cfg.get("combine") or "AND").upper()
    if combine not in ("AND", "OR"):
        raise _StepError("Combine conditions with AND or OR.", "invalid", "combine")
    conditions = cfg.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise _StepError("Add at least one filter condition.", "incomplete", "conditions")
    if len(conditions) > MAX_FILTER_CONDITIONS:
        raise _StepError(f"At most {MAX_FILTER_CONDITIONS} conditions.", "invalid", "conditions")

    parts: List[str] = []
    for cond in conditions:
        if not isinstance(cond, dict):
            raise _StepError("A filter condition is malformed.", "invalid", "conditions")
        col = _need_column(cols, cond.get("column"), "conditions")
        op = cond.get("operator")
        if not op:
            raise _StepError(f"Choose an operator for '{col.name}'.", "incomplete", "conditions")
        if op not in allowed_operators(col.category):
            raise _StepError(f"Operator '{op}' cannot be used on '{col.name}' (type {col.data_type}).", "invalid", "conditions")
        ref = ctx.q(col.name)
        if op in _NULL_OPS:
            parts.append(f"{ref} {_NULL_OPS[op]}")
        elif op in ("contains", "starts_with", "ends_with"):
            value = _coerce_value(col, cond.get("value"), "conditions")
            pattern = _like_pattern(ctx.db_type, str(value), op)
            parts.append(f"{ref} LIKE {ctx.param(pattern)} ESCAPE '!'")
        else:
            sql_op = _NUMERIC_OPS.get(op) or _TEXT_OPS[op]
            value = _coerce_value(col, cond.get("value"), "conditions")
            parts.append(f"{ref} {sql_op} {ctx.param(value)}")

    where = f" {combine} ".join(f"({p})" for p in parts)
    return Step(columns=list(cols), sql=f"SELECT {_select_list(ctx, cols)} FROM {prev} WHERE {where}")


def _step_drop_columns(ctx: _Ctx, node: dict, cols: Sequence[Column], prev: str) -> Step:
    names = (node.get("config") or {}).get("columns")
    if not isinstance(names, list) or not names:
        raise _StepError("Choose the columns to remove.", "incomplete", "columns")
    if len(set(names)) != len(names):
        raise _StepError("A column is listed twice.", "invalid", "columns")
    for n in names:
        _need_column(cols, n, "columns")
    keep = [c for c in cols if c.name not in set(names)]
    if not keep:
        raise _StepError("You cannot remove every column.", "invalid", "columns")
    return Step(columns=keep, sql=f"SELECT {_select_list(ctx, keep)} FROM {prev}")


def _step_rename(ctx: _Ctx, node: dict, cols: Sequence[Column], prev: str) -> Step:
    mappings = (node.get("config") or {}).get("mappings")
    if not isinstance(mappings, list) or not mappings:
        raise _StepError("Add at least one rename.", "incomplete", "mappings")
    rename: Dict[str, str] = {}
    for m in mappings:
        if not isinstance(m, dict):
            raise _StepError("A rename is malformed.", "invalid", "mappings")
        src = m.get("from")
        dst = m.get("to")
        _need_column(cols, src, "mappings")
        problem = validate_new_name(dst)
        if problem:
            raise _StepError(f"{problem} (renaming '{src}')", "invalid" if dst else "incomplete", "mappings")
        if src in rename:
            raise _StepError(f"'{src}' is renamed twice.", "invalid", "mappings")
        rename[src] = dst
    out: List[Column] = []
    seen: Set[str] = set()
    for c in cols:
        new = rename.get(c.name, c.name)
        if new.casefold() in seen:
            raise _StepError(f"Two columns would be called '{new}'. Column names must be unique.", "invalid", "mappings")
        seen.add(new.casefold())
        out.append(Column(new, c.data_type, c.category))
    select = ", ".join(f"{ctx.q(c.name)} AS {ctx.q(rename.get(c.name, c.name))}" for c in cols)
    return Step(columns=out, sql=f"SELECT {select} FROM {prev}")


def _step_dedupe(ctx: _Ctx, node: dict, cols: Sequence[Column], prev: str) -> Step:
    subset = (node.get("config") or {}).get("columns")
    if subset in (None, []):
        for c in cols:
            if _uncomparable(ctx.db_type, c.data_type):
                raise _StepError(f"Column '{c.name}' has type {c.data_type}, which cannot be compared for duplicates. Remove it first or choose key columns.", "invalid", "columns")
        return Step(columns=list(cols), sql=f"SELECT DISTINCT {_select_list(ctx, cols)} FROM {prev}",
                    notes=["info: rows that are identical in every column are collapsed to one (NULLs count as equal)."])
    if not isinstance(subset, list):
        raise _StepError("Columns must be a list.", "invalid", "columns")
    if len(set(subset)) != len(subset):
        raise _StepError("A column is listed twice.", "invalid", "columns")
    keys = [_need_column(cols, n, "columns") for n in subset]
    for k in keys:
        if _uncomparable(ctx.db_type, k.data_type):
            raise _StepError(f"Column '{k.name}' has type {k.data_type}, which cannot be compared for duplicates.", "invalid", "columns")
    taken = {c.name.casefold() for c in cols}
    rn = _unique_name("__dedupe_rn", taken)
    partition = ", ".join(ctx.q(k.name) for k in keys)
    inner = f"SELECT {_select_list(ctx, cols)}, ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY (SELECT NULL)) AS {ctx.q(rn)} FROM {prev}"
    sql = f"SELECT {_select_list(ctx, cols)} FROM ({inner}) AS d WHERE d.{ctx.q(rn)} = 1"
    return Step(columns=list(cols), sql=sql,
                notes=["warning: when several rows share the same key, which one is kept is not defined (no sort order is applied)."])


def _step_missing(ctx: _Ctx, node: dict, cols: Sequence[Column], prev: str) -> Step:
    cfg = node.get("config") or {}
    strategy = cfg.get("strategy")
    if not strategy:
        raise _StepError("Choose how to handle missing values.", "incomplete", "strategy")
    if strategy not in ("drop_rows", "fixed", "mean"):
        raise _StepError("Strategy must be drop rows, fixed value or mean.", "invalid", "strategy")
    names = cfg.get("columns")
    if not isinstance(names, list) or not names:
        raise _StepError("Choose at least one column.", "incomplete", "columns")
    if len(set(names)) != len(names):
        raise _StepError("A column is listed twice.", "invalid", "columns")
    chosen = {n: _need_column(cols, n, "columns") for n in names}

    if strategy == "drop_rows":
        where = " AND ".join(f"{ctx.q(n)} IS NOT NULL" for n in names)
        return Step(columns=list(cols), sql=f"SELECT {_select_list(ctx, cols)} FROM {prev} WHERE {where}",
                    notes=["info: rows with a missing value in any chosen column are removed."])

    out: List[Column] = []
    exprs: List[str] = []
    notes: List[str] = []
    float_type = "FLOAT" if ctx.db_type == MSSQL else "DOUBLE PRECISION"
    fixed_value = cfg.get("value")
    for c in cols:
        if c.name not in chosen:
            out.append(c)
            exprs.append(ctx.q(c.name))
            continue
        if strategy == "fixed":
            value = _coerce_value(c, fixed_value, "value")
            exprs.append(f"COALESCE({ctx.q(c.name)}, {ctx.param(value)}) AS {ctx.q(c.name)}")
            out.append(c)
        else:  # mean
            if c.category != "numeric":
                raise _StepError(f"The mean needs a numeric column; '{c.name}' is {c.data_type}.", "invalid", "columns")
            exprs.append(
                f"COALESCE({ctx.q(c.name)}, AVG(CAST({ctx.q(c.name)} AS {float_type})) OVER ()) AS {ctx.q(c.name)}"
            )
            out.append(Column(c.name, "float" if ctx.db_type == MSSQL else "double precision", "numeric"))
    if strategy == "mean":
        notes.append("info: the mean is calculated by the database over every row reaching this step; the column becomes a decimal (float) type.")
    return Step(columns=out, sql=f"SELECT {', '.join(exprs)} FROM {prev}", notes=notes)


def _step_output(ctx: _Ctx, node: dict, cols: Sequence[Column], prev: str) -> Step:
    name = (node.get("config") or {}).get("name")
    if name not in (None, ""):
        problem = validate_new_name(name)
        if problem:
            raise _StepError(problem.replace("column name", "dataset name"), "invalid", "name")
    seen: Set[str] = set()
    for c in cols:
        if c.name.casefold() in seen:
            raise _StepError(f"The output would contain two columns called '{c.name}'.", "invalid", None)
        seen.add(c.name.casefold())
    return Step(columns=list(cols), sql=f"SELECT {_select_list(ctx, cols)} FROM {prev}")


TRANSFORMS: Dict[str, Callable[[_Ctx, dict, Sequence[Column], str], Step]] = {
    "filter": _step_filter,
    "drop_columns": _step_drop_columns,
    "rename_columns": _step_rename,
    "remove_duplicates": _step_dedupe,
    "handle_missing": _step_missing,
}

NODE_TYPES = ("source", "join", "output", *TRANSFORMS)


# --------------------------------------------------------------- graph rules


def _ports_for(node_type: str) -> Tuple[str, ...]:
    if node_type == "source":
        return ()
    if node_type == "join":
        return ("left", "right")
    return ("in",)


def _topological(ids: List[str], incoming: Dict[str, Dict[str, str]]) -> Optional[List[str]]:
    indegree = {i: 0 for i in ids}
    children: Dict[str, List[str]] = {i: [] for i in ids}
    for target, ports in incoming.items():
        for src in ports.values():
            indegree[target] += 1
            children[src].append(target)
    ready = sorted(i for i, d in indegree.items() if d == 0)
    order: List[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for child in sorted(children[node]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    return order if len(order) == len(ids) else None


def can_connect(
    nodes: Sequence[dict],
    edges: Sequence[dict],
    source: str,
    target: str,
    port: str,
) -> Optional[str]:
    """Why a proposed connection is not allowed, or None. Mirrors compile()."""
    by_id = {n.get("id"): n for n in nodes}
    if source not in by_id or target not in by_id:
        return "Both nodes must exist."
    if source == target:
        return "A node cannot connect to itself."
    if by_id[source].get("type") == "output":
        return "An output node has nothing after it."
    ports = _ports_for(str(by_id[target].get("type")))
    if not ports:
        return "A source node takes no input."
    if port not in ports:
        return f"This node's inputs are: {', '.join(ports)}."
    for e in edges:
        if e.get("target") == target and e.get("port") == port:
            return "That input is already connected. Remove the existing connection first."
    # a cycle exists if `source` is reachable from `target`
    forward: Dict[str, List[str]] = {}
    for e in edges:
        forward.setdefault(e.get("source"), []).append(e.get("target"))
    stack, seen = [target], set()
    while stack:
        cur = stack.pop()
        if cur == source:
            return "That connection would create a loop."
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(forward.get(cur, []))
    return None


def compile_pipeline(
    db_type: str,
    nodes: Sequence[dict],
    edges: Sequence[dict],
    sources: Dict[str, SourceTable],
    connected_source_id: Optional[str] = None,
    source_labels: Optional[Dict[str, str]] = None,
) -> Compiled:
    """Validate every node and build its CTE. Never raises for node problems;
    those are reported per node. Raises PipelineError only for request-level
    problems (unsupported database, too many nodes, duplicate ids, a loop)."""
    db_type = (db_type or "").strip().lower()
    if db_type not in (MSSQL, POSTGRESQL):
        raise PipelineError(f"Unsupported database type '{db_type}'.")
    if len(nodes) > MAX_NODES:
        raise PipelineError(f"A pipeline can have at most {MAX_NODES} nodes.")

    ids: List[str] = []
    by_id: Dict[str, dict] = {}
    for n in nodes:
        nid = n.get("id")
        if not isinstance(nid, str) or not _NODE_ID.match(nid):
            raise PipelineError("Every node needs an id of letters, digits, '_' or '-' (max 40).")
        if nid in by_id:
            raise PipelineError(f"Node id '{nid}' is used twice.")
        by_id[nid] = n
        ids.append(nid)

    results: Dict[str, NodeResult] = {
        nid: NodeResult(node_id=nid, node_type=str(by_id[nid].get("type"))) for nid in ids
    }
    graph_issues: List[Issue] = []

    for nid in ids:
        if results[nid].node_type not in NODE_TYPES:
            results[nid].issues.append(Issue(nid, f"Unknown node type '{results[nid].node_type}'.", "invalid", "type"))

    incoming: Dict[str, Dict[str, str]] = {nid: {} for nid in ids}
    for e in edges:
        s, t, p = e.get("source"), e.get("target"), e.get("port") or "in"
        if s not in by_id or t not in by_id:
            raise PipelineError("A connection refers to a node that does not exist.")
        if s == t:
            raise PipelineError("A node cannot connect to itself.")
        t_type = results[t].node_type
        if t_type in NODE_TYPES and p not in _ports_for(t_type):
            results[t].issues.append(Issue(t, f"'{p}' is not an input of a {t_type} node.", "invalid", "inputs"))
            continue
        if results[s].node_type == "output":
            results[s].issues.append(Issue(s, "An output node cannot feed another node.", "invalid", "outputs"))
            continue
        if p in incoming[t]:
            results[t].issues.append(Issue(t, f"Input '{p}' has more than one connection.", "invalid", "inputs"))
            continue
        incoming[t][p] = s

    order = _topological(ids, incoming)
    if order is None:
        raise PipelineError("The pipeline contains a loop. Remove a connection to break it.")

    ctx = _Ctx(db_type)
    cte_names: Dict[str, str] = {nid: f"n{i}" for i, nid in enumerate(order)}

    for nid in order:
        node = by_id[nid]
        res = results[nid]
        res.inputs = dict(incoming[nid])
        t = res.node_type
        if t not in NODE_TYPES or any(i.kind == "invalid" for i in res.issues):
            continue
        ports = _ports_for(t)
        missing = [p for p in ports if p not in incoming[nid]]
        if missing:
            res.issues.append(Issue(nid, f"Connect an input: {', '.join(missing)}.", "incomplete", "inputs"))
            continue
        upstream = [results[incoming[nid][p]] for p in ports]
        bad = [u for u in upstream if u.columns is None]
        if bad:
            res.issues.append(Issue(nid, "An upstream node has a problem, so this step cannot be checked yet.", "blocked"))
            continue
        labels = source_labels or {}
        if t == "join":
            joined = sorted({sid for u in upstream for sid in u.source_ids})
            if len(joined) > 1:
                names = " and ".join(labels.get(sid, "another data source") for sid in joined)
                res.issues.append(Issue(nid, CROSS_SOURCE_MESSAGE.format(names=names), "invalid", "cross_source"))
                continue
        try:
            if t == "source":
                step = _step_source(ctx, node, sources, connected_source_id)
            elif t == "join":
                step = _step_join(ctx, node, upstream[0].columns or [], upstream[1].columns or [],
                                  cte_names[upstream[0].node_id], cte_names[upstream[1].node_id])
            elif t == "output":
                step = _step_output(ctx, node, upstream[0].columns or [], cte_names[upstream[0].node_id])
            else:
                step = TRANSFORMS[t](ctx, node, upstream[0].columns or [], cte_names[upstream[0].node_id])
        except _StepError as exc:
            res.issues.append(Issue(nid, str(exc), exc.kind, exc.field_name))
            continue
        res.columns = step.columns
        res.sql = step.sql
        if t == "source":
            src_tbl = sources.get(str((node.get("config") or {}).get("table_id")))
            res.source_ids = [src_tbl.source_id] if src_tbl is not None and src_tbl.source_id else []
        else:
            res.source_ids = sorted({sid for u in upstream for sid in u.source_ids})
        for note in step.notes:
            if note.startswith("warning: "):
                res.issues.append(Issue(nid, note[len("warning: "):], "warning"))
            elif note.startswith("info: "):
                res.notes.append(note[len("info: "):])
            else:
                res.notes.append(note)

    return Compiled(db_type=db_type, order=order, results=results, cte_names=cte_names,
                    params=ctx.params, graph_issues=graph_issues,
                    connected_source_id=connected_source_id, source_labels=dict(source_labels or {}))


# ------------------------------------------------------------------ building


def _ancestors(compiled: Compiled, node_id: str) -> List[str]:
    needed: Set[str] = set()
    stack = [node_id]
    while stack:
        cur = stack.pop()
        if cur in needed:
            continue
        needed.add(cur)
        stack.extend(compiled.results[cur].inputs.values())
    return [n for n in compiled.order if n in needed]


def _cte_block(compiled: Compiled, node_ids: Sequence[str]) -> str:
    return ",\n".join(f"{compiled.cte_names[n]} AS (\n  {compiled.results[n].sql}\n)" for n in node_ids)


def _used_params(sql: str, params: Dict[str, Any]) -> Dict[str, Any]:
    names = set(re.findall(r"(?<![\w\\]):(p\d+)\b", sql))
    return {k: v for k, v in params.items() if k in names}


def execution_problem(compiled: Compiled, node_id: str) -> Optional[str]:
    """Why this node cannot be run on the connected database, or None.

    A statement is sent to ONE database. It may only read tables of that
    source, so a node whose lineage touches any other source is refused here,
    before any SQL is built. This is the single check every execution path
    (SQL view, preview, join measurement) goes through."""
    res = compiled.results.get(node_id)
    if res is None or compiled.connected_source_id is None:
        return None
    others = [sid for sid in res.source_ids if sid != compiled.connected_source_id]
    if not others:
        return None
    labels = compiled.source_labels
    connected = labels.get(compiled.connected_source_id, "the connected source")
    where = " and ".join(labels.get(sid, "another data source") for sid in others)
    return (
        f"This step reads from {where}, but this pipeline is connected to {connected}. "
        f"To preview or measure it, open {where} as the main data source. Tables of other sources can be checked here but are not run."
    )


def _require_runnable(compiled: Compiled, node_id: str) -> None:
    res = compiled.results.get(node_id)
    if res is None:
        raise PipelineError("That node is not in the pipeline.")
    problem = execution_problem(compiled, node_id)
    if problem:
        raise PipelineError(problem)
    for n in _ancestors(compiled, node_id):
        if compiled.results[n].sql is None:
            raise PipelineError(
                "The pipeline has a problem upstream of this node; fix it before previewing."
            )


def build_preview_sql(compiled: Compiled, node_id: str, limit: int) -> Tuple[str, Dict[str, Any]]:
    """SELECT of one node's output, capped at `limit` rows IN THE DATABASE."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= PREVIEW_MAX_ROWS:
        raise PipelineError(f"The preview limit must be a whole number from 1 to {PREVIEW_MAX_ROWS}.")
    _require_runnable(compiled, node_id)
    res = compiled.results[node_id]
    ctx_q = lambda name: _quote_for_text(compiled.db_type, name)  # noqa: E731
    cols = ", ".join(ctx_q(c.name) for c in (res.columns or []))
    fetch = limit + 1  # one extra row tells the caller there is more
    cte = _cte_block(compiled, _ancestors(compiled, node_id))
    last = compiled.cte_names[node_id]
    if compiled.db_type == MSSQL:
        sql = f"WITH {cte}\nSELECT TOP {fetch} {cols} FROM {last}"
    else:
        sql = f"WITH {cte}\nSELECT {cols} FROM {last} LIMIT {fetch}"
    return sql, _used_params(sql, compiled.params)


def display_sql(sql: str) -> str:
    """The statement as a person would read it (undo the ':' escape)."""
    return sql.replace("\\:", ":")


def build_join_diagnostics_sql(compiled: Compiled, node_id: str, node: dict) -> Tuple[str, Dict[str, Any], List[Tuple[str, str]]]:
    """One statement that MEASURES a join: row counts on each side, how many
    rows match, and how many rows the join would multiply."""
    res = compiled.results.get(node_id)
    if res is None or res.node_type != "join":
        raise PipelineError("Diagnostics are only available for a join node.")
    _require_runnable(compiled, node_id)
    left_cte = compiled.cte_names[res.inputs["left"]]
    right_cte = compiled.cte_names[res.inputs["right"]]
    q = lambda name: _quote_for_text(compiled.db_type, name)  # noqa: E731
    pairs = [(c["left_column"], c["right_column"]) for c in (node.get("config") or {})["conditions"]]
    big = "BIGINT"

    lk_sel = ", ".join(f"{q(lc)} AS k{i}" for i, (lc, _) in enumerate(pairs))
    rk_sel = ", ".join(f"{q(rc)} AS k{i}" for i, (_, rc) in enumerate(pairs))
    l_group = ", ".join(q(lc) for lc, _ in pairs)
    r_group = ", ".join(q(rc) for _, rc in pairs)
    lnn = " AND ".join(f"{q(lc)} IS NOT NULL" for lc, _ in pairs)
    rnn = " AND ".join(f"{q(rc)} IS NOT NULL" for _, rc in pairs)
    on = " AND ".join(f"lk.k{i} = rk.k{i}" for i in range(len(pairs)))
    anynull_l = " OR ".join(f"{q(lc)} IS NULL" for lc, _ in pairs)
    anynull_r = " OR ".join(f"{q(rc)} IS NULL" for _, rc in pairs)

    ancestors = _ancestors(compiled, res.inputs["left"])
    for n in _ancestors(compiled, res.inputs["right"]):
        if n not in ancestors:
            ancestors.append(n)
    ancestors = [n for n in compiled.order if n in set(ancestors)]
    cte = _cte_block(compiled, ancestors)

    sql = (
        f"WITH {cte},\n"
        f"lk AS (SELECT {lk_sel}, COUNT(*) AS c FROM {left_cte} WHERE {lnn} GROUP BY {l_group}),\n"
        f"rk AS (SELECT {rk_sel}, COUNT(*) AS c FROM {right_cte} WHERE {rnn} GROUP BY {r_group}),\n"
        f"m AS (SELECT SUM(CAST(lk.c AS {big})) AS matched_left_rows, SUM(CAST(rk.c AS {big})) AS matched_right_rows, "
        f"SUM(CAST(lk.c AS {big}) * CAST(rk.c AS {big})) AS matched_pairs FROM lk INNER JOIN rk ON {on})\n"
        f"SELECT (SELECT CAST(COUNT(*) AS {big}) FROM {left_cte}) AS left_rows, "
        f"(SELECT CAST(COUNT(*) AS {big}) FROM {right_cte}) AS right_rows, "
        f"m.matched_left_rows, m.matched_right_rows, m.matched_pairs, "
        f"(SELECT MAX(c) FROM lk) AS max_left_group, (SELECT MAX(c) FROM rk) AS max_right_group, "
        f"(SELECT CAST(COUNT(*) AS {big}) FROM {left_cte} WHERE {anynull_l}) AS left_null_keys, "
        f"(SELECT CAST(COUNT(*) AS {big}) FROM {right_cte} WHERE {anynull_r}) AS right_null_keys "
        f"FROM m"
    )
    return sql, _used_params(sql, compiled.params), pairs


def summarise_join(row: Dict[str, Any], join_type: str) -> Dict[str, Any]:
    """Turn the raw counts into the numbers the UI shows. Pure arithmetic."""
    def n(key: str) -> int:
        value = row.get(key)
        return int(value) if value is not None else 0

    left_rows, right_rows = n("left_rows"), n("right_rows")
    ml, mr, mp = n("matched_left_rows"), n("matched_right_rows"), n("matched_pairs")
    unmatched_left = left_rows - ml
    unmatched_right = right_rows - mr
    jt = join_type.upper()
    output_rows = {
        "INNER": mp,
        "LEFT": mp + unmatched_left,
        "RIGHT": mp + unmatched_right,
        "FULL": mp + unmatched_left + unmatched_right,
    }[jt]
    return {
        "join_type": jt,
        "left_rows": left_rows,
        "right_rows": right_rows,
        "left_rows_with_match": ml,
        "right_rows_with_match": mr,
        "unmatched_left_rows": unmatched_left,
        "unmatched_right_rows": unmatched_right,
        "matched_pairs": mp,
        "output_rows": output_rows,
        "largest_left_key_group": n("max_left_group"),
        "largest_right_key_group": n("max_right_group"),
        "left_null_keys": n("left_null_keys"),
        "right_null_keys": n("right_null_keys"),
        # A left row that matches several right rows (or the reverse) is repeated
        # in the result: the join multiplies rows. The raw facts are kept for each side...
        "multiplies_left_rows": mp > ml,
        "multiplies_right_rows": mp > mr,
        # ...and the warning is about the MAIN side of the join: the left table, or the
        # right table for a RIGHT join. A normal many-to-one lookup (orders -> customers)
        # repeats the parent rows but does not inflate the main table, so it is not flagged.
        "main_side": "right" if jt == "RIGHT" else "left",
        "extra_rows_from_multiplication": max(mp - (mr if jt == "RIGHT" else ml), 0),
        "multiplies_main_side": mp > (mr if jt == "RIGHT" else ml),
    }
