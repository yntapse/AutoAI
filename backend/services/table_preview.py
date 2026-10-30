"""Paged preview of a catalogued table.

Lets a user browse a whole table one page at a time (default 20 rows, at most
100). The page size limits ONE response only; it never limits the source
table or any dataset built from it.

How paging works
----------------
* The table has a primary key  ->  KEYSET paging. The next page is "rows whose
  key is greater than the last key shown". Cost stays flat on page 1 or page
  1,000,000, and rows inserted or deleted elsewhere in the table do not shift
  the pages already shown.
* No primary key  ->  OFFSET paging. Rows are ordered by every sortable column
  (so identical rows are the only ties) and the next page skips the rows
  already shown. Deep pages get slower, and a change to the table between two
  requests can shift the rows.

The "cursor" handed to the caller is opaque. It carries only the position
(never SQL, never a credential), and is checked against the table it was
issued for. The SQL is built from the CATALOG's schema, table and column names,
and every value is a bound parameter.
"""

import base64
import hashlib
import json
import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from services.db_connector import (
    MAX_PAGE_SIZE,
    MSSQL,
    POSTGRESQL,
    PREVIEW_ROWS,
    DataSourceError,
    _check_db_type,
    build_page_query,
    run_preview_query,
)

CURSOR_VERSION = 1
MAX_CELL_CHARS = 2000  # a preview cell is cut here; the database is untouched
_JS_SAFE_INT = 2**53 - 1  # larger integers are sent as text so browsers keep them exact

# Types the database cannot ORDER BY. Matched on the type name before any "(".
_UNSORTABLE = {
    MSSQL: {"TEXT", "NTEXT", "IMAGE", "XML", "GEOGRAPHY", "GEOMETRY", "HIERARCHYID", "NULL"},
    POSTGRESQL: {"JSON", "XML", "POINT", "LINE", "LSEG", "BOX", "PATH", "POLYGON", "CIRCLE", "NULL"},
}


class PreviewCursorError(ValueError):
    """The page token is malformed, tampered with, or for a different table."""


class _UnencodableKey(Exception):
    pass


# ---------------------------------------------------------------------------
# Choosing the order
# ---------------------------------------------------------------------------


def _type_name(data_type: str) -> str:
    return (data_type or "").split("(")[0].strip().upper()


def choose_ordering(
    db_type: str, columns: List[Dict[str, Any]]
) -> Tuple[List[str], str, bool]:
    """Return (order_columns, basis, stable).

    basis   "primary_key" | "all_columns" | "none"
    stable  True when the order is fully determined (a primary key, or every
            column is sortable so only identical rows can tie).

    `columns` items need column_name, data_type and is_primary_key, already in
    table order.
    """
    db_type = _check_db_type(db_type)
    primary_key = [c["column_name"] for c in columns if c.get("is_primary_key")]
    if primary_key:
        return primary_key, "primary_key", True

    blocked = _UNSORTABLE[db_type]
    sortable = [
        c["column_name"] for c in columns if _type_name(c.get("data_type", "")) not in blocked
    ]
    if not sortable:
        return [], "none", False
    return sortable, "all_columns", len(sortable) == len(columns)


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------


def _fingerprint(db_type: str, schema: str, table: str, order: List[str], basis: str) -> str:
    raw = json.dumps([db_type, schema, table, order, basis], separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _encode_value(value: Any) -> List[Any]:
    if isinstance(value, bool):
        return ["b", value]
    if isinstance(value, int):
        return ["i", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _UnencodableKey()
        return ["f", repr(value)]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise _UnencodableKey()
        return ["d", str(value)]
    if isinstance(value, str):
        return ["s", value]
    if isinstance(value, datetime):  # before date: datetime is a date subclass
        return ["dt", value.isoformat()]
    if isinstance(value, date):
        return ["da", value.isoformat()]
    if isinstance(value, time):
        return ["t", value.isoformat()]
    if isinstance(value, UUID):
        return ["u", str(value)]
    if isinstance(value, (bytes, bytearray)):
        return ["by", bytes(value).hex()]
    raise _UnencodableKey()


def _decode_value(item: Any) -> Any:
    try:
        tag, raw = item
        if tag == "b":
            if not isinstance(raw, bool):
                raise ValueError
            return raw
        if tag == "i":
            return int(raw)
        if tag == "f":
            return float(raw)
        if tag == "d":
            return Decimal(raw)
        if tag == "s":
            if not isinstance(raw, str):
                raise ValueError
            return raw
        if tag == "dt":
            return datetime.fromisoformat(raw)
        if tag == "da":
            return date.fromisoformat(raw)
        if tag == "t":
            return time.fromisoformat(raw)
        if tag == "u":
            return UUID(raw)
        if tag == "by":
            return bytes.fromhex(raw)
    except Exception as exc:
        raise PreviewCursorError("The page token is not valid.") from exc
    raise PreviewCursorError("The page token is not valid.")


def encode_cursor(fingerprint: str, mode: str, consumed: int, key: Optional[List[Any]] = None) -> str:
    payload: Dict[str, Any] = {"v": CURSOR_VERSION, "f": fingerprint, "m": mode, "n": consumed}
    if key is not None:
        payload["k"] = [_encode_value(v) for v in key]
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str, fingerprint: str) -> Dict[str, Any]:
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except Exception as exc:
        raise PreviewCursorError("The page token is not valid.") from exc

    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise PreviewCursorError("The page token is not valid.")
    if payload.get("f") != fingerprint:
        raise PreviewCursorError(
            "The page token belongs to a different table or an older scan. "
            "Reload the preview from the first page."
        )
    mode, consumed = payload.get("m"), payload.get("n")
    if mode not in ("keyset", "offset") or isinstance(consumed, bool) or not isinstance(consumed, int) or consumed < 0:
        raise PreviewCursorError("The page token is not valid.")

    key = None
    if mode == "keyset":
        raw_key = payload.get("k")
        if not isinstance(raw_key, list) or not raw_key:
            raise PreviewCursorError("The page token is not valid.")
        key = [_decode_value(item) for item in raw_key]
    return {"mode": mode, "consumed": consumed, "key": key}


# ---------------------------------------------------------------------------
# Turning database values into JSON
# ---------------------------------------------------------------------------


def json_cell(value: Any) -> Any:
    """A JSON-safe value. NULL stays None (JSON null).

    Integers beyond 2**53 and every Decimal go out as text so no digit is lost
    in a browser; NaN and infinity (not valid JSON) go out as text; binary is
    summarised; very long text is cut at MAX_CELL_CHARS.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= _JS_SAFE_INT else str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        shown = data[:32].hex()
        return f"0x{shown}" + (f"… ({len(data)} bytes)" if len(data) > 32 else "")
    text_ = value if isinstance(value, str) else str(value)
    if isinstance(value, timedelta):
        text_ = str(value)
    return text_ if len(text_) <= MAX_CELL_CHARS else text_[:MAX_CELL_CHARS] + "…"


# ---------------------------------------------------------------------------
# One page
# ---------------------------------------------------------------------------


def get_table_page(
    connection_string: str,
    db_type: str,
    schema: str,
    table: str,
    columns: List[Dict[str, Any]],
    limit: int = PREVIEW_ROWS,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch one page of `schema.table`.

    `schema`, `table` and `columns` must come from the catalog, never from the
    request. Returns the response body (no credentials in it).
    Raises PreviewCursorError for a bad token, and DataSourceError (or a
    subclass) for connection, missing-table and query failures.
    """
    db_type = _check_db_type(db_type)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise DataSourceError(f"limit must be a whole number from 1 to {MAX_PAGE_SIZE}.")

    order, basis, stable = choose_ordering(db_type, columns)
    fingerprint = _fingerprint(db_type, schema, table, order, basis)

    after: Optional[List[Any]] = None
    offset = 0
    consumed = 0
    mode = "keyset" if basis == "primary_key" else "offset"
    if cursor:
        state = decode_cursor(cursor, fingerprint)
        mode, consumed = state["mode"], state["consumed"]
        if mode == "keyset":
            if basis != "primary_key" or len(state["key"]) != len(order):
                raise PreviewCursorError("The page token does not match this table.")
            after = state["key"]
        else:
            offset = consumed

    sql, params = build_page_query(
        db_type, table, schema=schema, page_size=limit, order_by=order, offset=offset, after=after
    )
    column_names, rows = run_preview_query(connection_string, sql, params, max_rows=limit + 1)

    has_more = len(rows) > limit
    page = rows[:limit]
    total_shown = consumed + len(page)

    next_cursor = None
    if has_more:
        next_cursor = _next_cursor(fingerprint, basis, order, column_names, page[-1], total_shown)

    message = None
    if not page:
        message = "No more rows." if cursor else "This table is empty."

    return {
        "table_name": table,
        "schema_name": schema,
        "column_names": [str(c) for c in column_names],
        "rows": [
            {str(name): json_cell(value) for name, value in zip(column_names, row)}
            for row in page
        ],
        "row_count": len(page),
        "page_size": limit,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "pagination_mode": mode,
        "stable_order": stable,
        "start_row": consumed + 1 if page else None,
        "message": message,
    }


def _next_cursor(
    fingerprint: str,
    basis: str,
    order: List[str],
    column_names: List[str],
    last_row: tuple,
    total_shown: int,
) -> str:
    if basis != "primary_key":
        return encode_cursor(fingerprint, "offset", total_shown)

    by_name = dict(zip(column_names, last_row))
    missing = [c for c in order if c not in by_name]
    if missing:
        raise DataSourceError(
            "The table's columns no longer match the catalog. Rescan the source and try again."
        )
    try:
        return encode_cursor(fingerprint, "keyset", total_shown, [by_name[c] for c in order])
    except _UnencodableKey:
        # A key value we cannot carry in a token. Same ORDER BY, so carry on
        # by position instead of failing the browse.
        return encode_cursor(fingerprint, "offset", total_shown)
