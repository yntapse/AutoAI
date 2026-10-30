"""Metadata scanning for the Data Intelligence OS catalog.

This module is deliberately thin. Reading a schema is already solved in
services/db_connector.py (list_tables, list_columns, list_foreign_keys) and the
call sequence is already proven in etl_copilot/schema_reader.py. The scanner
adds persistence, not a second schema reader.

Privacy boundary
----------------
Row counts are metadata and are always safe. Sample values ARE customer row
data: they are opt-in per scan and catalog_for_llm() strips them, so no code
path can hand them to a model by accident.
"""

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.db_connector import (
    DataSourceError,
    default_schema,
    extract_to_dataframe,
    list_columns,
    list_foreign_keys,
    list_tables,
    preview_query,
    quote_identifier,
)

logger = logging.getLogger(__name__)

MAX_SAMPLE_VALUES = 5
MAX_SAMPLE_COLUMNS_PER_TABLE = 50
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_column_name(name: str) -> str:
    """Fold a column name to a comparison key.

    CustomerID, Customer_ID and cust_id are three spellings an enterprise uses
    for the same thing. Sprint 2 matches on this key; Sprint 1 only stores it.
    """
    lowered = str(name or "").strip().lower()
    collapsed = _NON_ALNUM.sub("", lowered)
    for prefix in ("tbl", "dim", "fact"):
        if collapsed.startswith(prefix) and len(collapsed) > len(prefix) + 2:
            collapsed = collapsed[len(prefix):]
            break
    if collapsed.startswith("cust") and not collapsed.startswith("customer"):
        collapsed = "customer" + collapsed[len("cust"):]
    return collapsed


def _row_count(connection_string: str, db_type: str, schema: str, table: str) -> Optional[int]:
    """Exact COUNT(*). Returns None rather than raising - a scan must not fail
    because one table is unreadable or very large."""
    try:
        quoted_schema = quote_identifier(db_type, schema)
        quoted_table = quote_identifier(db_type, table)
        frame = extract_to_dataframe(
            connection_string,
            f"SELECT COUNT(*) AS row_count FROM {quoted_schema}.{quoted_table}",
            max_rows=10,
        )
        if frame.empty:
            return None
        return int(frame.iloc[0]["row_count"])
    except Exception as exc:
        logger.warning("Row count failed for %s.%s: %s", schema, table, exc)
        return None


def _sample_values(
    connection_string: str,
    db_type: str,
    table: str,
    columns: List[Dict[str, Any]],
    schema: Optional[str] = None,
) -> Dict[str, List[Any]]:
    """Up to MAX_SAMPLE_VALUES distinct-ish values per column.

    CUSTOMER ROW DATA. Only called when the caller opts in.
    """
    try:
        frame = extract_to_dataframe(
            connection_string,
            preview_query(db_type, table, limit=MAX_SAMPLE_VALUES, schema=schema),
            max_rows=MAX_SAMPLE_VALUES + 1,
        )
    except Exception as exc:
        logger.warning("Sample values failed for %s: %s", table, exc)
        return {}

    samples: Dict[str, List[Any]] = {}
    for column in columns[:MAX_SAMPLE_COLUMNS_PER_TABLE]:
        name = column["name"]
        if name not in frame.columns:
            continue
        values = []
        for value in frame[name].head(MAX_SAMPLE_VALUES).tolist():
            try:
                values.append(None if value is None else str(value)[:120])
            except Exception:
                values.append(None)
        samples[name] = values
    return samples


def scan_source(
    connection_string: str,
    db_type: str,
    schema: Optional[str] = None,
    include_row_counts: bool = True,
    include_sample_values: bool = False,
    max_tables: Optional[int] = None,
) -> Dict[str, Any]:
    """Read a schema and return a catalog payload ready for persistence.

    Raises DataSourceError if the source cannot be reached at all. A failure on
    one individual table degrades that table, it does not fail the scan.
    """
    started = time.perf_counter()
    resolved_schema = schema or default_schema(db_type)

    table_names = list_tables(connection_string, schema=resolved_schema)
    if max_tables:
        table_names = table_names[: int(max_tables)]
    if not table_names:
        raise DataSourceError(
            f"No readable tables found in schema '{resolved_schema}'."
        )

    columns_by_table = list_columns(
        connection_string, table_names, schema=resolved_schema
    )
    foreign_keys = list_foreign_keys(
        connection_string, table_names, schema=resolved_schema
    )

    fk_columns = {
        (edge["left_table"], edge["left_column"]) for edge in foreign_keys
    }

    tables: List[Dict[str, Any]] = []
    total_columns = 0

    for table_name in table_names:
        raw_columns = columns_by_table.get(table_name, [])
        total_columns += len(raw_columns)

        samples: Dict[str, List[Any]] = {}
        if include_sample_values and raw_columns:
            samples = _sample_values(
                connection_string,
                db_type,
                table_name,
                raw_columns,
                schema=resolved_schema,
            )

        columns = []
        for position, column in enumerate(raw_columns):
            name = str(column["name"])
            columns.append(
                {
                    "column_name": name,
                    "data_type": str(column.get("type", "")),
                    "ordinal_position": position,
                    "is_nullable": bool(column.get("nullable", True)),
                    "is_primary_key": bool(column.get("primary_key", False)),
                    "is_foreign_key": (table_name, name) in fk_columns,
                    "normalized_name": normalize_column_name(name),
                    "sample_values": samples.get(name),
                }
            )

        row_count = None
        if include_row_counts:
            row_count = _row_count(
                connection_string, db_type, resolved_schema, table_name
            )

        tables.append(
            {
                "table_name": table_name,
                "schema_name": resolved_schema,
                "column_count": len(columns),
                "row_count": row_count,
                "row_count_is_estimate": False,
                "has_primary_key": any(c["is_primary_key"] for c in columns),
                "columns": columns,
            }
        )

    relationships = [
        {
            "left_table": edge["left_table"],
            "left_column": edge["left_column"],
            "right_table": edge["right_table"],
            "right_column": edge["right_column"],
            "constraint_name": edge.get("constraint_name"),
            "origin": "declared",
            "confidence": 1.0,
        }
        for edge in foreign_keys
    ]

    return {
        "schema_name": resolved_schema,
        "db_type": db_type,
        "scanned_at": datetime.utcnow(),
        "table_count": len(tables),
        "column_count": total_columns,
        "relationship_count": len(relationships),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "tables": tables,
        "relationships": relationships,
    }


def catalog_for_llm(catalog: Dict[str, Any]) -> Dict[str, Any]:
    """Strip every customer row value before a catalog reaches a model.

    Mirrors etl_copilot.copilot_service.catalog_for_llm. Any new field holding
    row data must be removed here as well.
    """
    return {
        "schema_name": catalog.get("schema_name"),
        "db_type": catalog.get("db_type"),
        "table_count": catalog.get("table_count"),
        "tables": [
            {
                "table_name": table.get("table_name"),
                "column_count": table.get("column_count"),
                "row_count": table.get("row_count"),
                "columns": [
                    {
                        "column_name": column.get("column_name"),
                        "data_type": column.get("data_type"),
                        "is_nullable": column.get("is_nullable"),
                        "is_primary_key": column.get("is_primary_key"),
                        "is_foreign_key": column.get("is_foreign_key"),
                    }
                    for column in table.get("columns", [])
                ],
            }
            for table in catalog.get("tables", [])
        ],
        "relationships": catalog.get("relationships", []),
    }
