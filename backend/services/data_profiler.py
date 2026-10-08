"""Read-only source-table profiling used before ETL dataset creation."""

import os
import math
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    cast,
    func,
    inspect,
    null,
    or_,
    select,
)
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.types import JSON, ARRAY

from services.db_connector import (
    DataSourceError,
    MSSQL,
    POSTGRESQL,
    _make_engine,
)
from services.sql_builder import MAX_TABLES

TOP_VALUES_LIMIT = 10
VALUE_INVENTORY_LIMIT = max(1, int(os.getenv("PROFILE_VALUE_INVENTORY_LIMIT", "1000")))


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (UUID,)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _is_numeric(column_type: Any) -> bool:
    return isinstance(column_type, (Integer, Numeric, Float)) and not isinstance(
        column_type, Boolean
    )


def _supports_scalar_aggregates(column_type: Any, dialect_name: str) -> bool:
    if isinstance(
        column_type,
        (JSON, ARRAY, LargeBinary, PostgreSQLUUID, UNIQUEIDENTIFIER),
    ):
        return False
    if dialect_name == "mssql" and (
        isinstance(column_type, Text)
        or (isinstance(column_type, String) and column_type.length is None)
    ):
        return False
    return True


def _is_groupable(column_type: Any, dialect_name: str) -> bool:
    return _supports_scalar_aggregates(column_type, dialect_name)


def _quantile(connection, table: Table, column, count: int, probability: float) -> Optional[float]:
    if count == 0:
        return None
    position = (count - 1) * probability
    lower_index = int(math.floor(position))
    upper_index = int(math.ceil(position))
    values = connection.execute(
        select(column)
        .select_from(table)
        .where(column.is_not(None))
        .order_by(column)
        .offset(lower_index)
        .limit(upper_index - lower_index + 1)
    ).scalars().all()
    if not values:
        return None
    lower = float(values[0])
    upper = float(values[-1])
    return lower + (upper - lower) * (position - lower_index)


def _classify_value(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "numeric"
    if not isinstance(value, str):
        return "other"
    cleaned = value.strip()
    if not cleaned:
        return "string"
    try:
        int(cleaned)
        return "integer"
    except ValueError:
        try:
            float(cleaned)
            return "numeric"
        except ValueError:
            try:
                date.fromisoformat(cleaned)
                return "date"
            except ValueError:
                try:
                    datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
                    return "datetime"
                except ValueError:
                    lowered = cleaned.lower()
                    if lowered in {"true", "false", "yes", "no"}:
                        return "boolean"
                    return "string"


def _type_analysis(
    column_type: Any,
    value_inventory: List[Dict[str, Any]],
    complete: bool,
) -> Dict[str, Any]:
    declared_type = str(column_type)
    declared_name = type(column_type).__name__.lower()
    if isinstance(column_type, Boolean):
        expected_type = "boolean"
    elif _is_numeric(column_type):
        expected_type = "numeric"
    elif isinstance(column_type, DateTime):
        expected_type = "datetime"
    elif isinstance(column_type, Date):
        expected_type = "date"
    elif isinstance(column_type, String):
        expected_type = "string"
    else:
        expected_type = declared_name

    frequencies: Dict[str, int] = {}
    for item in value_inventory:
        value_type = _classify_value(item.get("value"))
        frequencies[value_type] = frequencies.get(value_type, 0) + int(item.get("count", 0))
    observed_types = sorted(frequencies)
    mixed = len(observed_types) > 1 if complete else (True if len(observed_types) > 1 else None)
    analysis_complete = complete or not isinstance(column_type, String)
    mismatch_count = None
    if complete and isinstance(column_type, String):
        mismatch_count = sum(
            count for value_type, count in frequencies.items()
            if value_type != "string"
        )
    elif analysis_complete:
        mismatch_count = 0
    elif mixed:
        mismatch_count = None

    return {
        "declared_type": declared_type,
        "detected_type": observed_types[0] if len(observed_types) == 1 else (
            "mixed" if observed_types else None
        ),
        "expected_type": expected_type,
        "observed_types": observed_types,
        "type_mismatch_count": mismatch_count if complete or mixed else None,
        "mixed_type": mixed,
        "type_analysis_complete": analysis_complete,
    }


def _profile_column(connection, table: Table, column, row_count: int) -> Dict[str, Any]:
    column_type = column.type
    numeric = _is_numeric(column_type)
    scalar_aggregates = _supports_scalar_aggregates(
        column_type, connection.dialect.name
    )

    expressions = [
        func.count(column).label("non_null_count"),
        (
            func.count(func.distinct(column)).label("distinct_count")
            if scalar_aggregates
            else null().label("distinct_count")
        ),
        (
            func.min(column).label("minimum")
            if scalar_aggregates and not isinstance(column_type, Boolean)
            else null().label("minimum")
        ),
        (
            func.max(column).label("maximum")
            if scalar_aggregates and not isinstance(column_type, Boolean)
            else null().label("maximum")
        ),
        (
            func.avg(cast(column, Float)).label("mean")
            if numeric
            else null().label("mean")
        ),
    ]
    stats = connection.execute(select(*expressions).select_from(table)).one()
    non_null_count = int(stats.non_null_count or 0)
    null_count = row_count - non_null_count
    distinct_count = (
        int(stats.distinct_count)
        if stats.distinct_count is not None
        else None
    )

    median = None
    standard_deviation = None
    quartiles = None
    outlier_report = {
        "method": "iqr",
        "count": None,
        "percentage": None,
        "lower_bound": None,
        "upper_bound": None,
        "complete": False,
        "reason": "IQR analysis is available for numeric columns with at least four non-null values.",
    }
    if numeric and non_null_count:
        median = _quantile(connection, table, column, non_null_count, 0.5)
        if non_null_count >= 4:
            q1 = _quantile(connection, table, column, non_null_count, 0.25)
            q3 = _quantile(connection, table, column, non_null_count, 0.75)
            if q1 is not None and q3 is not None:
                iqr = q3 - q1
                lower_bound = q1 - 1.5 * iqr
                upper_bound = q3 + 1.5 * iqr
                outlier_count = int(
                    connection.execute(
                        select(func.count())
                        .select_from(table)
                        .where(
                            column.is_not(None),
                            or_(column < lower_bound, column > upper_bound),
                        )
                    ).scalar_one()
                )
                if connection.dialect.name == "postgresql":
                    standard_deviation = connection.execute(
                        select(func.stddev_pop(column)).select_from(table)
                    ).scalar_one()
                elif connection.dialect.name == "mssql":
                    standard_deviation = connection.execute(
                        select(func.stdevp(column)).select_from(table)
                    ).scalar_one()
                else:
                    mean_square = connection.execute(
                        select(func.avg(cast(column, Float) * cast(column, Float)))
                        .select_from(table)
                        .where(column.is_not(None))
                    ).scalar_one()
                    mean = float(stats.mean)
                    standard_deviation = (
                        math.sqrt(max(0.0, float(mean_square) - mean * mean))
                        if mean_square is not None
                        else None
                    )
                quartiles = {"q1": q1, "q2": median, "q3": q3}
                outlier_report = {
                    "method": "iqr",
                    "count": outlier_count,
                    "percentage": outlier_count * 100.0 / non_null_count,
                    "lower_bound": lower_bound,
                    "upper_bound": upper_bound,
                    "complete": True,
                    "multiplier": 1.5,
                }

    top_values = []
    value_inventory = []
    inventory_complete = scalar_aggregates or non_null_count == 0
    if scalar_aggregates and non_null_count:
        inventory_rows = connection.execute(
            select(column, func.count().label("value_count"))
            .select_from(table)
            .where(column.is_not(None))
            .group_by(column)
            .order_by(func.count().desc(), column.asc())
            .limit(VALUE_INVENTORY_LIMIT + 1)
        ).all()
        inventory_complete = len(inventory_rows) <= VALUE_INVENTORY_LIMIT
        value_inventory = [
            {
                "value": _json_value(value),
                "count": int(value_count),
                "percentage": value_count * 100.0 / non_null_count,
            }
            for value, value_count in inventory_rows[:VALUE_INVENTORY_LIMIT]
        ]

    if scalar_aggregates and isinstance(column_type, (String, Boolean)) and non_null_count:
        value_rows = connection.execute(
            select(column, func.count().label("value_count"))
            .select_from(table)
            .where(column.is_not(None))
            .group_by(column)
            .order_by(func.count().desc())
            .limit(TOP_VALUES_LIMIT)
        ).all()
        top_values = [
            {
                "value": _json_value(value),
                "count": int(value_count),
                "percentage": value_count * 100.0 / non_null_count,
            }
            for value, value_count in value_rows
        ]

    type_analysis = _type_analysis(column_type, value_inventory, inventory_complete)
    return {
        "column_name": column.name,
        "data_type": str(column_type),
        "null_count": null_count,
        "null_percentage": (
            null_count * 100.0 / row_count if row_count else None
        ),
        "distinct_count": distinct_count,
        "distinct_percentage": (
            distinct_count * 100.0 / non_null_count
            if distinct_count is not None and non_null_count
            else None
        ),
        "min": _json_value(stats.minimum),
        "max": _json_value(stats.maximum),
        "mean": _json_value(float(stats.mean)) if stats.mean is not None else None,
        "median": _json_value(median),
        "std_dev": _json_value(float(standard_deviation)) if standard_deviation is not None else None,
        "quartiles": quartiles,
        "outliers": outlier_report,
        "type_analysis": type_analysis,
        "top_values": top_values,
        "value_inventory": value_inventory,
        "inventory_complete": inventory_complete,
    }


def profile_source_tables(
    connection_string: str,
    schema: str,
    selected_tables: List[str],
    null_threshold: float = 5.0,
) -> Dict[str, Any]:
    """Profile selected source tables using read-only SQL aggregate queries."""
    if not selected_tables:
        raise DataSourceError("Select at least one table to profile.")

    if len(set(selected_tables)) != len(selected_tables):
        raise DataSourceError("Selected tables must be unique.")

    if len(selected_tables) > MAX_TABLES:
        raise DataSourceError(
            "Select no more than %s tables per profiling request." % MAX_TABLES
        )
    if not math.isfinite(null_threshold) or not 0 <= null_threshold <= 100:
        raise DataSourceError("null_threshold must be between 0 and 100.")

    db_type = MSSQL if connection_string.startswith("mssql") else POSTGRESQL
    engine = None
    try:
        engine = _make_engine(connection_string)
        with engine.connect() as raw_connection:
            connection = raw_connection
            if db_type == POSTGRESQL:
                connection = connection.execution_options(postgresql_readonly=True)

            inspector = inspect(connection)
            available_tables = set(inspector.get_table_names(schema=schema))
            available_tables.update(inspector.get_view_names(schema=schema))
            unknown = [name for name in selected_tables if name not in available_tables]
            if unknown:
                raise DataSourceError(
                    "Selected table(s) were not found in schema '%s': %s"
                    % (schema, ", ".join(unknown))
                )

            metadata = MetaData()
            profiles = []
            for table_name in selected_tables:
                table = Table(
                    table_name,
                    metadata,
                    schema=schema,
                    autoload_with=connection,
                )
                row_count = int(
                    connection.execute(
                        select(func.count()).select_from(table)
                    ).scalar_one()
                )
                groupable_columns = [
                    column for column in table.columns
                    if _is_groupable(column.type, connection.dialect.name)
                ]
                duplicate_complete = len(groupable_columns) == len(table.columns)
                duplicate_count = None
                if groupable_columns and row_count:
                    duplicate_groups = (
                        select(func.count().label("group_count"))
                        .select_from(table)
                        .group_by(*groupable_columns)
                        .having(func.count() > 1)
                        .subquery()
                    )
                    duplicate_count = int(
                        connection.execute(
                            select(
                                func.coalesce(
                                    func.sum(duplicate_groups.c.group_count - 1), 0
                                )
                            )
                        ).scalar_one()
                    )

                primary_key_columns = [
                    table.c[name] for name in table.primary_key.columns.keys()
                ]
                duplicate_key_count = None
                if primary_key_columns and row_count:
                    duplicate_key_groups = (
                        select(func.count().label("group_count"))
                        .select_from(table)
                        .group_by(*primary_key_columns)
                        .having(func.count() > 1)
                        .subquery()
                    )
                    duplicate_key_count = int(
                        connection.execute(
                            select(
                                func.coalesce(
                                    func.sum(
                                        duplicate_key_groups.c.group_count - 1
                                    ),
                                    0,
                                )
                            )
                        ).scalar_one()
                    )

                columns = [
                    _profile_column(connection, table, column, row_count)
                    for column in table.columns
                ]
                total_cells = row_count * len(columns)
                null_count = sum(int(column["null_count"]) for column in columns)
                numeric_columns = [
                    column for column in columns
                    if column["type_analysis"]["expected_type"] == "numeric"
                ]
                complete_numeric_columns = [
                    column for column in numeric_columns
                    if column["outliers"]["complete"]
                ]
                numeric_values = sum(
                    row_count - int(column["null_count"]) for column in numeric_columns
                )
                outlier_count = sum(
                    int(column["outliers"]["count"])
                    for column in complete_numeric_columns
                )
                type_checked_values = [
                    column for column in columns
                    if column["type_analysis"]["type_mismatch_count"] is not None
                ]
                type_values = sum(
                    row_count - int(column["null_count"]) for column in type_checked_values
                )
                type_mismatches = sum(
                    int(column["type_analysis"]["type_mismatch_count"])
                    for column in type_checked_values
                )
                missing_percentage = null_count * 100.0 / total_cells if total_cells else 0.0
                duplicate_percentage = (
                    duplicate_count * 100.0 / row_count
                    if duplicate_count is not None and row_count
                    else (None if duplicate_count is None else 0.0)
                )
                outlier_complete = (
                    not numeric_columns
                    or len(complete_numeric_columns) == len(numeric_columns)
                )
                outlier_percentage = (
                    outlier_count * 100.0 / numeric_values
                    if numeric_values and outlier_complete
                    else (0.0 if not numeric_columns else None)
                )
                type_complete = len(type_checked_values) == len(columns)
                type_mismatch_percentage = (
                    type_mismatches * 100.0 / type_values
                    if type_values and type_complete
                    else (0.0 if type_complete else None)
                )
                score_dimensions = {
                    "missing_values": total_cells > 0,
                    "duplicate_rows": (
                        duplicate_complete and duplicate_count is not None
                    ),
                    "outliers": outlier_complete,
                    "data_types": type_complete,
                }
                score_coverage = round(
                    sum(score_dimensions.values()) * 100.0
                    / len(score_dimensions),
                    1,
                )
                score_penalty = min(30.0, missing_percentage * 1.5)
                if duplicate_percentage is not None:
                    score_penalty += min(30.0, duplicate_percentage * 3.0)
                if outlier_percentage is not None:
                    score_penalty += min(20.0, outlier_percentage * 2.0)
                if type_mismatch_percentage is not None:
                    score_penalty += min(20.0, type_mismatch_percentage * 0.2)
                score = round(
                    max(0.0, 100.0 - score_penalty),
                    1,
                )
                issues = []
                recommendations = []
                for column in columns:
                    if column["null_percentage"] is not None and column["null_percentage"] > null_threshold:
                        issues.append({
                            "category": "missing_values",
                            "severity": "warning",
                            "table_name": table_name,
                            "column_name": column["column_name"],
                            "message": "Null percentage exceeds the configured threshold.",
                            "actual": column["null_percentage"],
                            "expected": "<= %s%%" % null_threshold,
                        })
                        recommendations.append(
                            "Review missingness in %s.%s and determine an approved handling policy."
                            % (table_name, column["column_name"])
                        )
                    if (
                        column["type_analysis"]["mixed_type"]
                        or (
                            column["type_analysis"]["type_mismatch_count"] is not None
                            and column["type_analysis"]["type_mismatch_count"] > 0
                        )
                    ):
                        issues.append({
                            "category": "data_type",
                            "severity": "warning",
                            "table_name": table_name,
                            "column_name": column["column_name"],
                            "message": "Observed values are mixed or do not match the expected type.",
                            "actual": column["type_analysis"]["observed_types"],
                            "expected": column["type_analysis"]["expected_type"],
                        })
                        recommendations.append(
                            "Review observed values in %s.%s against the expected type before using the column."
                            % (table_name, column["column_name"])
                        )
                    outliers = column["outliers"]
                    if outliers["complete"] and outliers["count"]:
                        issues.append({
                            "category": "outliers",
                            "severity": "info",
                            "table_name": table_name,
                            "column_name": column["column_name"],
                            "message": "IQR outliers were detected; this is a review signal, not an automatic removal instruction.",
                            "actual": outliers["count"],
                            "expected": 0,
                        })
                        recommendations.append(
                            "Inspect IQR outliers in %s.%s; do not remove them without domain review."
                            % (table_name, column["column_name"])
                        )
                if duplicate_count:
                    issues.append({
                        "category": "duplicates",
                        "severity": "warning",
                        "table_name": table_name,
                        "column_name": None,
                        "message": "Duplicate rows were detected using comparable columns.",
                        "actual": duplicate_count,
                        "expected": 0,
                    })
                    recommendations.append(
                        "Review duplicate rows in %s and confirm the intended record grain."
                        % table_name
                    )
                profiles.append(
                    {
                        "table_name": table_name,
                        "row_count": row_count,
                        "column_count": len(table.columns),
                        "duplicate_rows": {
                            "count": duplicate_count,
                            "percentage": (
                                duplicate_percentage if duplicate_count is not None else None
                            ),
                            "method": "exact_row_match",
                            "complete": duplicate_complete,
                            "columns_checked": [column.name for column in groupable_columns],
                        },
                        "duplicate_keys": {
                            "columns": [column.name for column in primary_key_columns],
                            "duplicate_count": duplicate_key_count,
                            "complete": bool(primary_key_columns),
                        },
                        "columns": columns,
                        "issues": issues,
                        "recommendations": recommendations,
                        "quality_score": score,
                        "score_coverage": score_coverage,
                        "score_formula": {
                            "version": 1,
                            "formula": "100 - min(30, missing_cell_percentage*1.5) - min(30, duplicate_row_percentage*3) - min(20, numeric_outlier_percentage*2) - min(20, expected_type_mismatch_percentage*0.2); unavailable dimensions are not penalized and lower score coverage",
                            "available_dimensions": score_dimensions,
                        },
                    }
                )

        all_issues = [issue for table_profile in profiles for issue in table_profile["issues"]]
        total_rows = sum(table_profile["row_count"] for table_profile in profiles)
        overall_score = round(
            sum(
                table_profile["quality_score"] * max(table_profile["row_count"], 1)
                for table_profile in profiles
            )
            / sum(max(table_profile["row_count"], 1) for table_profile in profiles),
            1,
        ) if profiles else 100.0
        overall_score_coverage = round(
            sum(
                table_profile["score_coverage"] * max(table_profile["row_count"], 1)
                for table_profile in profiles
            )
            / sum(max(table_profile["row_count"], 1) for table_profile in profiles),
            1,
        ) if profiles else 100.0
        return {
            "schema_name": schema,
            "table_count": len(profiles),
            "tables": profiles,
            "summary": {
                "overall_quality_score": overall_score,
                "score_coverage": overall_score_coverage,
                "tables_profiled": len(profiles),
                "total_rows": total_rows,
                "columns_above_null_threshold": sum(
                    1 for issue in all_issues if issue["category"] == "missing_values"
                ),
                "issue_counts": {
                    severity: sum(1 for issue in all_issues if issue["severity"] == severity)
                    for severity in ("critical", "high", "warning", "info")
                },
                "null_threshold_percentage": null_threshold,
            },
            "issues": all_issues,
            "recommendations": list(dict.fromkeys(
                recommendation
                for table_profile in profiles
                for recommendation in table_profile["recommendations"]
            )),
        }
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(
            "Could not profile the selected source tables: %s" % exc
        ) from exc
    finally:
        if engine is not None:
            engine.dispose()
