"""ETL Copilot over HTTP.

Natural language -> verified dataset -> an ordinary AutoAI project.

WHY THIS ROUTER EXISTS
----------------------
etl_copilot/ is a pure library: it reads a schema, resolves join paths from
declared foreign keys, compiles SQL, executes it, reconciles the result against
source-grain totals and hands back a DataFrame. None of that was reachable over
HTTP, so nothing but a shell could use it.

HOW IT REGISTERS A DATASET
--------------------------
It does NOT invent a dataset system. It calls
routers.datamodel._create_project_from_frame() - the same helper
/datamodel/extract uses - so a Copilot dataset is byte-for-byte the same kind
of object as one built by the multi-table builder or uploaded as a CSV:
a file in uploads/, a Project row, a DatasetProfile row. Every downstream
feature (training, agent, transform, results, downloads) then works with no
further change.

The Copilot's own CSV under generated_datasets/ is deliberately NOT written on
this path (persist=False). One dataset, one file_id, one owner.
"""

import datetime
import json
import logging
import math
import os
import pathlib
import threading
import time
import traceback
import uuid
from decimal import Decimal
from json import JSONDecodeError
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from openai import OpenAIError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
import pandas as pd

from database import SessionLocal
from models.pipeline_definition import PipelineDefinition
from models.pipeline_run import PipelineRun
from models.pipeline_version import PipelineVersion
from services.data_cleaner import (
    CleaningValidationError,
    apply_cleaning_steps,
    validate_cleaning_steps,
)
from services.db_connector import (
    DataSourceError,
    build_connection_string,
    default_schema,
    extract_to_dataframe,
    list_columns,
)
from services.quality_rules import (
    QualityRuleValidationError,
    evaluate_rules,
    validate_rule_definition,
)
from services.pipeline_execution import (
    PipelineExecutionError,
    build_execution_graph,
    compile_saved_pipeline_sql,
)
from services.sql_builder import SqlBuildError, build_select, edges_from_dicts

from etl_copilot.build_service import BuildError
from etl_copilot.copilot_service import CopilotError, ETLCopilot, catalog_for_llm
from etl_copilot.dataset_compiler import SpecCompileError
from etl_copilot.reconciler import ReconciliationError
from etl_copilot.quality import compute_quality_score

# routers.datamodel imports `main` only inside function bodies, so importing it
# here is safe and introduces no cycle.
from routers.datamodel import _columns_for_build, _create_project_from_frame

router = APIRouter(
    prefix="/etl-copilot",
    tags=["ETL Copilot"],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Crash-proof tracing
#
# print() is NOT safe here. When the console a uvicorn worker writes to is
# unavailable (a detached reloader child, a closed pipe, a console that cannot
# encode a character), print() itself raises. The first print on the preview
# path then throws, the error handler's own traceback.print_exc() throws again,
# and the request dies as a bare text/plain 500 with NOTHING logged anywhere.
#
# _trace() writes to backend/logs/etl_copilot_trace.log first and only then
# attempts the console. Neither can raise.
# ---------------------------------------------------------------------------
_TRACE_PATH = pathlib.Path(__file__).resolve().parent.parent / "logs" / "etl_copilot_trace.log"
_trace_lock = threading.Lock()


def _trace(message: str) -> None:
    line = "%s pid=%s %s" % (
        datetime.datetime.now().isoformat(timespec="milliseconds"),
        os.getpid(),
        message,
    )
    try:
        _TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _trace_lock, open(_TRACE_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001
        pass


_trace(
    "MODULE LOADED routers/etl_copilot.py (source saved %s)"
    % datetime.datetime.fromtimestamp(os.path.getmtime(__file__)).isoformat(
        timespec="seconds"
    )
)


# ---------------------------------------------------------------------------
# Shared copilot instance
#
# ETLCopilot holds a JoinVerifier whose cache is keyed on (connection, schema,
# hop). Building a new ETLCopilot per request threw that cache away every time,
# so ten heavy profiling queries per join hop were paid again on every call.
# One module-level instance keeps it warm across requests.
#
# It is built lazily because OpenAISpecGenerator reads OPENAI_API_KEY at
# construction: doing that at import time would stop the whole application
# starting when the key is absent.
# ---------------------------------------------------------------------------

_copilot: Optional[ETLCopilot] = None


def get_copilot() -> ETLCopilot:
    global _copilot

    if _copilot is None:
        try:
            _copilot = ETLCopilot()
        except RuntimeError as exc:
            # resolve_api_key() raises this when OPENAI_API_KEY is missing or
            # empty. That is a deployment problem, not a bad request.
            raise HTTPException(status_code=503, detail=str(exc))

    return _copilot


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Request models - field for field the same as routers.datamodel's, so the
# frontend can send one connection object to either router.
# ---------------------------------------------------------------------------


class EtlCopilotConnection(BaseModel):
    """Connection details. Sent with every call and never stored on disk."""

    db_type: str = Field(default="mssql", description="'mssql' or 'postgresql'")
    host: str = Field(..., description="Server hostname or IP, e.g. localhost")
    port: Optional[int] = Field(default=None, description="Defaults to 1433 / 5432")
    database: str = Field(..., description="Database name")
    username: Optional[str] = Field(default=None)
    password: Optional[str] = Field(default=None)
    trusted_connection: bool = Field(
        default=False, description="SQL Server Windows Authentication"
    )
    odbc_driver: Optional[str] = Field(
        default=None, description="Override the ODBC driver name if needed"
    )
    schema_name: Optional[str] = Field(
        default=None,
        description="Schema to read. Defaults to dbo / public.",
    )


class EtlCopilotSchemaRequest(EtlCopilotConnection):
    pass


class EtlCopilotPreviewRequest(EtlCopilotConnection):
    user_request: str

class EtlCopilotBuildRequest(EtlCopilotPreviewRequest):
    project_name: str = Field(..., min_length=1, description="Name for the project")
    target_column: Optional[str] = Field(
        default=None,
        description=(
            "Column to predict. Defaults to the spec's own target_column, and "
            "then to the last column of the generated dataset."
        ),
    )
    background: bool = Field(
        default=False,
        description=(
            "Run the build as a background job and return a job_id immediately. "
            "Poll GET /etl-copilot/status/{job_id} for the result."
        ),
    )
    pipeline_version_id: Optional[UUID] = None
    cleaning_steps: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Ordered in-memory cleaning steps applied before project registration.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connection_string(request: EtlCopilotConnection) -> str:
    """Build a connection URL, or raise a 400. Same call datamodel makes."""
    try:
        return build_connection_string(
            db_type=request.db_type,
            host=request.host,
            port=request.port,
            database=request.database,
            username=request.username,
            password=request.password,
            trusted_connection=request.trusted_connection,
            odbc_driver=request.odbc_driver,
        )
    except DataSourceError as exc:
        detail = str(exc)
        _trace("[ETL] RAISING HTTPException 400 detail=%r" % detail)
        raise HTTPException(status_code=400, detail=detail)


def _resolved_schema(request: EtlCopilotConnection) -> str:
    if request.schema_name:
        return request.schema_name
    try:
        return default_schema(request.db_type)
    except DataSourceError as exc:
        detail = str(exc)
        _trace("[ETL] RAISING HTTPException 400 detail=%r" % detail)
        raise HTTPException(status_code=400, detail=detail)


def _jsonable(value):
    """Coerce engine values into something json.dumps accepts.

    The reconciliation checks carry numpy scalars - pandas .sum() returns
    int64/float64, .nunique() returns int64 - and a SUM over a DECIMAL column
    comes back as decimal.Decimal. None of the three is JSON serialisable, and
    JSONResponse raises TypeError on them at render time, AFTER the dataset has
    been built and registered. The engine is right to keep exact types; this is
    the transport boundary, so the conversion belongs here.
    """
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]

    if isinstance(value, Decimal):
        return float(value)

    # numpy scalars expose .item(); this avoids importing numpy just to isinstance.
    if hasattr(value, "item") and hasattr(value, "dtype"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            return str(value)

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    return str(value)


def _issue_detail(message: str, issues) -> Dict[str, Any]:
    """A 400 body that names every problem, not just the first.

    The validator returns a list of ValidationIssue objects with a code, a
    message and often a suggestion. Flattening them to one string would throw
    away exactly the information a user needs to fix the request.
    """
    return {
        "message": message,
        "issues": [
            issue.dict() if hasattr(issue, "dict") else issue for issue in (issues or [])
        ],
    }


def _raise_for(exc: Exception):
    """Map an ETL Copilot failure onto an HTTP status code.

    400 - the request cannot be satisfied (bad spec, bad schema, bad SQL).
    422 - the dataset was BUILT but could not be VERIFIED. Semantically
          different from a malformed request and worth its own code: nothing
          was written, and the user's data is what changed.
    """
    if isinstance(exc, CopilotError):
        detail = _issue_detail(str(exc), getattr(exc, "issues", None))
        _trace("[ETL] RAISING HTTPException 400 detail=%r" % detail)
        raise HTTPException(
            status_code=400,
            detail=detail,
        )

    if isinstance(exc, ReconciliationError):
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "hint": (
                    "The dataset was built but its totals did not match the "
                    "source. Nothing was saved."
                ),
            },
        )

    if isinstance(exc, (BuildError, SpecCompileError, DataSourceError)):
        detail = str(exc)
        _trace("[ETL] RAISING HTTPException 400 detail=%r" % detail)
        raise HTTPException(status_code=400, detail=detail)

    # Everything the OpenAI SDK raises is an UPSTREAM fault, not a bad
    # request: a network blip, a rate limit, a revoked key, a completion
    # that is not JSON. Without this branch they fall through to `raise
    # exc` and leave the process as a bare 500 - which is what the browser
    # reported as a CORS failure, because a 500 raised out of the app does
    # not travel back through CORSMiddleware.
    if isinstance(exc, (OpenAIError, JSONDecodeError)):
        raise HTTPException(
            status_code=502,
            detail=(
                "The AI service could not be reached or returned an "
                "unusable response: %s: %s" % (type(exc).__name__, exc)
            ),
        )

    raise exc


def _resolve_target(requested: Optional[str], result: Dict[str, Any]) -> str:
    """Pick the target column, preferring what the user asked for."""
    columns = list(result.get("columns") or [])

    for candidate in (requested, result.get("target_column")):
        if candidate and candidate in columns:
            return candidate

    if requested and requested not in columns:
        detail = (
            "Target column '%s' is not in the generated dataset. "
            "Available columns: %s" % (requested, ", ".join(map(str, columns)))
        )
        _trace("[ETL] RAISING HTTPException 400 detail=%r" % detail)
        raise HTTPException(
            status_code=400,
            detail=detail,
        )

    if not columns:
        detail = "The dataset has no columns."
        _trace("[ETL] RAISING HTTPException 400 detail=%r" % detail)
        raise HTTPException(status_code=400, detail=detail)

    return str(columns[-1])


def _build_compiled_pipeline_frame(
    request: EtlCopilotBuildRequest,
    pipeline_json: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute a compiled Pipeline Builder definition without invoking an LLM."""
    tables = pipeline_json.get("tables")
    joins = pipeline_json.get("joins", [])
    if (
        not isinstance(tables, list)
        or not tables
        or any(not isinstance(table, str) or not table.strip() for table in tables)
        or not isinstance(joins, list)
    ):
        raise HTTPException(
            status_code=422,
            detail="Saved pipeline must contain a non-empty tables list and joins list.",
        )
    selected_columns = pipeline_json.get("selected_columns")
    if selected_columns is not None and not isinstance(selected_columns, dict):
        raise HTTPException(
            status_code=422, detail="Saved selected_columns must be an object."
        )
    if selected_columns is not None:
        if any(
            table not in tables
            or not isinstance(columns, list)
            or any(not isinstance(column, str) for column in columns)
            for table, columns in selected_columns.items()
        ):
            raise HTTPException(
                status_code=422,
                detail="Saved selected_columns must map selected tables to column lists.",
            )

    source = pipeline_json.get("source")
    source = source if isinstance(source, dict) else {}
    schema = source.get("schema_name") or pipeline_json.get("schema_name")
    schema = schema or _resolved_schema(request)
    connection_string = _connection_string(request)
    base_table = pipeline_json.get("base_table") or tables[0]
    if not isinstance(base_table, str):
        raise HTTPException(status_code=422, detail="Saved base_table must be a string.")

    expected_graph = build_execution_graph(pipeline_json)
    saved_graph = pipeline_json.get("execution_graph")
    if saved_graph is not None and saved_graph != expected_graph:
        raise HTTPException(
            status_code=422,
            detail="Saved execution_graph does not match the pipeline definition.",
        )

    try:
        columns_by_table = _columns_for_build(
            connection_string,
            tables,
            schema,
            selected_columns,
        )
        if selected_columns is not None:
            for table_name, saved_columns in selected_columns.items():
                columns_by_table[table_name] = list(saved_columns)
        edges = edges_from_dicts(joins)
        built = build_select(
            db_type=request.db_type,
            base_table=base_table,
            tables=tables,
            relationships=edges,
            columns_by_table=columns_by_table,
            schema=schema,
        )
        execution_sql, query_params = compile_saved_pipeline_sql(
            request.db_type,
            built.sql,
            built.alias_map,
            pipeline_json,
        )
        frame = extract_to_dataframe(
            connection_string,
            execution_sql,
            params=query_params,
        )
    except SqlBuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PipelineExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "frame": frame,
        "sql": execution_sql,
        "alias_map": built.alias_map,
        "tables": tables,
        "columns_by_table": columns_by_table,
        "schema": schema,
        "execution_graph": saved_graph or expected_graph,
    }


def _source_column_alias(
    alias_map: Dict[str, str],
    table_name: Optional[str],
    column_name: str,
) -> str:
    """Resolve one saved table/column reference to its deterministic output alias."""
    if not isinstance(column_name, str) or not column_name.strip():
        raise HTTPException(
            status_code=422, detail="Saved column references must be non-empty strings."
        )
    matches = [
        output_name
        for output_name, source in alias_map.items()
        if source.rsplit(".", 1)[-1].casefold() == column_name.casefold()
        and (
            table_name is None
            or source.rsplit(".", 1)[0].casefold() == table_name.casefold()
        )
    ]
    if len(matches) != 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "Saved column reference '%s%s' resolves to %d output columns."
                % (
                    "%s." % table_name if table_name else "",
                    column_name,
                    len(matches),
                )
            ),
        )
    return matches[0]


def _compiled_cleaning_steps(
    transformations: Any,
    alias_map: Dict[str, str],
) -> List[Dict[str, Any]]:
    if not isinstance(transformations, list):
        return []

    steps = []
    for index, transformation in enumerate(transformations, 1):
        if not isinstance(transformation, dict):
            raise HTTPException(
                status_code=422,
                detail="Saved transformation #%d must be an object." % index,
            )
        step_type = transformation.get("step_type")
        config = transformation.get("configuration")
        table_name = transformation.get("table_name")
        if not isinstance(config, dict):
            raise HTTPException(
                status_code=422,
                detail="Saved transformation #%d has no configuration object." % index,
            )

        normalized = dict(config)
        if isinstance(normalized.get("column_name"), str):
            normalized["column_name"] = _source_column_alias(
                alias_map,
                table_name if isinstance(table_name, str) else None,
                normalized["column_name"],
            )
        if isinstance(normalized.get("columns"), list):
            if any(not isinstance(column, str) for column in normalized["columns"]):
                raise HTTPException(
                    status_code=422,
                    detail="Saved transformation columns must be strings.",
                )
            normalized["columns"] = [
                _source_column_alias(
                    alias_map,
                    table_name if isinstance(table_name, str) else None,
                    column,
                )
                for column in normalized["columns"]
            ]

        if step_type == "remove_duplicates" and normalized.get("scope") == "columns":
            normalized["scope"] = "selected_columns"
        if (
            step_type == "replace_null_values"
            and normalized.get("strategy") != "custom"
        ):
            normalized.pop("custom_value", None)

        steps.append(
            {
                "step_name": transformation.get("step_name")
                or transformation.get("name")
                or str(step_type or "transformation"),
                "step_type": step_type,
                "configuration": normalized,
                "enabled": transformation.get("enabled", True),
            }
        )
    return steps


def _apply_saved_filters(
    frame: pd.DataFrame,
    filters: Any,
    alias_map: Dict[str, str],
) -> pd.DataFrame:
    if filters is None:
        return frame
    if not isinstance(filters, list):
        raise HTTPException(status_code=422, detail="Saved filters must be a list.")

    operators = {
        "=": "eq",
        "==": "eq",
        "eq": "eq",
        "equals": "eq",
        "!=": "ne",
        "<>": "ne",
        "ne": "ne",
        "not_equals": "ne",
        ">": "gt",
        "gt": "gt",
        "greater_than": "gt",
        ">=": "gte",
        "gte": "gte",
        "greater_than_or_equal": "gte",
        "<": "lt",
        "lt": "lt",
        "less_than": "lt",
        "<=": "lte",
        "lte": "lte",
        "less_than_or_equal": "lte",
        "is null": "is_null",
        "is_null": "is_null",
        "isnull": "is_null",
        "is not null": "is_not_null",
        "is_not_null": "is_not_null",
        "isnotnull": "is_not_null",
    }
    result = frame
    for index, item in enumerate(filters, 1):
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=422, detail="Saved filter #%d must be an object." % index
            )
        column = item.get("column") or item.get("column_name")
        if not isinstance(column, str) or not column.strip():
            raise HTTPException(
                status_code=422,
                detail="Saved filter #%d has no column." % index,
            )
        source_table = item.get("source_table") or item.get("table_name")
        if source_table is not None and not isinstance(source_table, str):
            raise HTTPException(
                status_code=422,
                detail="Saved filter table_name must be a string.",
            )
        output_column = _source_column_alias(
            alias_map,
            source_table,
            column,
        )
        operator = operators.get(str(item.get("operator", "")).strip().lower())
        if operator is None:
            raise HTTPException(
                status_code=422,
                detail="Saved filter #%d uses an unsupported operator." % index,
            )

        values = result[output_column]
        if operator == "is_null":
            mask = values.isna()
        elif operator == "is_not_null":
            mask = values.notna()
        else:
            if "value" not in item:
                raise HTTPException(
                    status_code=422,
                    detail="Saved filter #%d requires a value." % index,
                )
            value = item["value"]
            if (
                value is None
                or not isinstance(value, (str, bool, int, float))
                or isinstance(value, float) and not math.isfinite(value)
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Saved filter #%d value must be a finite JSON scalar." % index,
                )
            try:
                if operator == "eq":
                    mask = values == value
                elif operator == "ne":
                    mask = values != value
                elif operator == "gt":
                    mask = values > value
                elif operator == "gte":
                    mask = values >= value
                elif operator == "lt":
                    mask = values < value
                else:
                    mask = values <= value
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail="Saved filter #%d cannot compare its column and value." % index,
                ) from exc
            mask = mask.fillna(False)
        result = result.loc[mask].copy()
    return result


def _profile_source_frames(
    source_frames: Dict[str, pd.DataFrame],
    column_metadata: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    tables = []
    for table_name, frame in source_frames.items():
        source_types = {
            str(column["name"]).casefold(): column.get("type")
            for column in column_metadata.get(table_name, [])
        }
        columns = []
        for column_name in frame.columns:
            series = frame[column_name]
            non_null = series.dropna()
            distinct = non_null.drop_duplicates()
            inventory = [_jsonable(value) for value in distinct.tolist()]
            try:
                minimum = _jsonable(non_null.min()) if not non_null.empty else None
                maximum = _jsonable(non_null.max()) if not non_null.empty else None
            except (TypeError, ValueError):
                minimum = maximum = None
            row_count = len(frame)
            null_count = int(series.isna().sum())
            non_null_count = row_count - null_count
            distinct_count = len(distinct)
            columns.append(
                {
                    "column_name": str(column_name),
                    "data_type": source_types.get(
                        str(column_name).casefold(), str(series.dtype)
                    ),
                    "null_count": null_count,
                    "null_percentage": (
                        null_count * 100.0 / row_count if row_count else 0.0
                    ),
                    "distinct_count": distinct_count,
                    "distinct_percentage": (
                        distinct_count * 100.0 / non_null_count
                        if non_null_count
                        else 0.0
                    ),
                    "min": minimum,
                    "max": maximum,
                    "value_inventory": [{"value": value} for value in inventory],
                    "inventory_complete": True,
                }
            )
        tables.append(
            {
                "table_name": table_name,
                "row_count": int(len(frame)),
                "columns": columns,
            }
        )
    return {"tables": tables}


def _evaluate_saved_quality_rules(
    request: EtlCopilotBuildRequest,
    pipeline_json: Dict[str, Any],
    tables: List[str],
    schema: str,
    connection_string: str,
) -> Optional[Dict[str, Any]]:
    raw_rules = pipeline_json.get("quality_rules", [])
    if not isinstance(raw_rules, list):
        raise HTTPException(status_code=422, detail="Saved quality_rules must be a list.")
    if not raw_rules:
        return None

    rules = []
    for index, item in enumerate(raw_rules, 1):
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=422, detail="Saved quality rule #%d must be an object." % index
            )
        if not isinstance(item.get("enabled", True), bool):
            raise HTTPException(
                status_code=422,
                detail="Saved quality rule #%d enabled must be boolean." % index,
            )
        rule_type = item.get("rule_type")
        severity = item.get("severity", "error")
        config = item.get("rule_config")
        try:
            validate_rule_definition(rule_type, severity, config)
        except QualityRuleValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        rules.append(
            SimpleNamespace(
                id=item.get("id") or "saved-quality-rule-%d" % index,
                rule_name=item.get("rule_name") or item.get("name") or (
                    "%s #%d" % (rule_type, index)
                ),
                rule_type=rule_type,
                severity=severity,
                enabled=item.get("enabled", True),
                rule_config=config,
            )
        )

    source_frames = {}
    try:
        column_metadata = list_columns(connection_string, tables, schema=schema)
        for table_name in tables:
            columns = _columns_for_build(
                connection_string, [table_name], schema, None
            )
            statement = build_select(
                db_type=request.db_type,
                base_table=table_name,
                tables=[table_name],
                relationships=[],
                columns_by_table=columns,
                schema=schema,
            )
            source_frames[table_name] = extract_to_dataframe(
                connection_string, statement.sql
            )
    except SqlBuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        report = evaluate_rules(
            rules,
            _profile_source_frames(source_frames, column_metadata),
        )
    except (QualityRuleValidationError, LookupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if report["overall_status"] == "fail":
        raise HTTPException(
            status_code=422,
            detail={"message": "Saved pipeline quality rules failed.", "report": report},
        )
    return report


def _build_dataset_and_register(
    db: Session,
    request: EtlCopilotBuildRequest,
    pipeline_json: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The whole build path, shared by the sync and background entry points."""
    stage_durations: Dict[str, float] = {}
    execution_started = time.perf_counter()

    compiled_pipeline = (
        pipeline_json is not None
        and isinstance(pipeline_json.get("tables"), list)
        and (
            pipeline_json.get("schema_version") is not None
            or not isinstance(pipeline_json.get("generated_plan"), dict)
        )
    )
    cleaning_steps = request.cleaning_steps
    if compiled_pipeline and isinstance(pipeline_json.get("transformations"), list):
        cleaning_steps = []
    if not compiled_pipeline or not isinstance(pipeline_json.get("transformations"), list):
        try:
            validate_cleaning_steps(cleaning_steps)
        except CleaningValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if pipeline_json is not None and not compiled_pipeline and not isinstance(
        pipeline_json.get("generated_plan"), dict
    ):
        raise HTTPException(
            status_code=422,
            detail="Saved pipeline version has no executable definition.",
        )

    quality_report = None
    compiled_result = None
    if compiled_pipeline:
        query_started = time.perf_counter()
        compiled_result = _build_compiled_pipeline_frame(request, pipeline_json)
        stage_durations["extract_and_join"] = max(
            0.0, time.perf_counter() - query_started
        )
        result = {
            "frame": compiled_result["frame"],
            "columns": list(compiled_result["frame"].columns),
            "target_column": pipeline_json.get("target_column"),
            "sql": compiled_result["sql"],
            "execution_graph": compiled_result["execution_graph"],
            "spec": None,
            "explanation": [
                "Executed the saved tables and approved joins without replanning."
            ],
        }
        alias_map = compiled_result["alias_map"]
        if isinstance(pipeline_json.get("transformations"), list):
            cleaning_steps = _compiled_cleaning_steps(
                pipeline_json["transformations"], alias_map
            )
        try:
            validate_cleaning_steps(cleaning_steps)
        except CleaningValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        copilot = get_copilot()
        connection_string = _connection_string(request)
        schema = _resolved_schema(request)
        try:
            build_options = {
                "connection_string": connection_string,
                "schema": schema,
                "dataset_name": request.project_name,
                # AutoAI owns the file and the file_id. Letting BuildService also
                # write one would give a single dataset two ids and two copies on
                # disk that can drift apart.
                "persist": False,
            }
            if pipeline_json is not None:
                result = copilot.create_dataset_from_spec(
                    saved_spec=pipeline_json["generated_plan"],
                    **build_options,
                )
            else:
                result = copilot.create_dataset(
                    user_request=request.user_request,
                    **build_options,
                )
        except Exception as exc:
            _raise_for(exc)

    frame = result.pop("frame", None)

    if frame is None:
        raise HTTPException(
            status_code=500,
            detail="The dataset was built but its rows were not returned.",
        )

    query_row_count = int(len(frame))
    cleaning_started = time.perf_counter()
    try:
        frame, cleaning_summary = apply_cleaning_steps(
            frame,
            cleaning_steps,
        )
    except CleaningValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stage_durations["cleaning"] = max(
        0.0, time.perf_counter() - cleaning_started
    )
    cleaned_row_count = int(len(frame))

    if compiled_pipeline:
        connection_string = _connection_string(request)
        quality_started = time.perf_counter()
        quality_report = _evaluate_saved_quality_rules(
            request=request,
            pipeline_json=pipeline_json,
            tables=compiled_result["tables"],
            schema=compiled_result["schema"],
            connection_string=connection_string,
        )
        stage_durations["quality_validation"] = max(
            0.0, time.perf_counter() - quality_started
        )
        result["lineage"] = {
            "schema_version": pipeline_json.get("schema_version"),
            "plan_id": pipeline_json.get("plan_id"),
            "source": pipeline_json.get("source"),
            "base_table": pipeline_json.get("base_table")
            or compiled_result["tables"][0],
            "tables": list(compiled_result["tables"]),
            "joins": pipeline_json.get("joins", []),
            "transformations": pipeline_json.get("transformations", []),
            "filters": pipeline_json.get("filters", []),
            "group_by": pipeline_json.get("group_by", []),
            "aggregations": pipeline_json.get("aggregations", []),
            "quality_rules": pipeline_json.get("quality_rules", []),
            "quality_report": quality_report,
            "sql": compiled_result["sql"],
            "output_columns": list(frame.columns),
            "column_sources": compiled_result["alias_map"],
            "execution_graph": compiled_result["execution_graph"],
        }

    total_rows = int(len(frame))
    total_columns = int(len(frame.columns))
    total_cells = total_rows * total_columns
    missing_value_count = int(frame.isnull().sum().sum())
    data_quality = {
        "total_rows": total_rows,
        "total_columns": total_columns,
        "missing_value_percentage": (
            missing_value_count * 100.0 / total_cells if total_cells else None
        ),
        "duplicate_rows": int(frame.duplicated().sum()),
        "quality_score": int(compute_quality_score(frame)),
    }
    column_profiles = [
        {
            "column_name": str(column),
            "data_type": str(frame[column].dtype),
            "null_count": int(frame[column].isnull().sum()),
            "distinct_count": int(frame[column].nunique(dropna=True)),
            "sample_values": _jsonable(frame[column].dropna().head(3).tolist()),
        }
        for column in frame.columns
    ]

    target_column = _resolve_target(request.target_column, result)

    source_label = "%s://%s/%s (ETL Copilot)" % (
        request.db_type,
        request.host,
        request.database,
    )

    # _create_project_from_frame enforces the row/column caps, validates the
    # target, writes uploads/<file_id>.csv, and commits Project +
    # DatasetProfile. It raises HTTPException itself, which passes straight
    # through.
    registration_started = time.perf_counter()
    payload = _create_project_from_frame(
        db=db,
        df=frame,
        project_name=request.project_name,
        target_column=target_column,
        source_label=source_label,
    )
    stage_durations["register_dataset"] = max(
        0.0, time.perf_counter() - registration_started
    )

    payload["user_request"] = request.user_request
    payload["sql"] = result.get("sql")
    payload["explanation"] = result.get("explanation")
    payload["reconciliation"] = result.get("reconciliation")
    payload["warnings"] = result.get("warnings")
    payload["lineage"] = result.get("lineage")
    payload["spec"] = result.get("spec")
    payload["data_quality"] = data_quality
    payload["column_profiles"] = column_profiles
    payload["cleaning_summary"] = cleaning_summary
    if compiled_pipeline:
        payload["execution_metrics"] = {
            "total_duration_seconds": max(
                0.0, time.perf_counter() - execution_started
            ),
            "stage_durations_seconds": stage_durations,
            "row_counts": {
                "query_output": query_row_count,
                "after_cleaning": cleaned_row_count,
                "registered": int(payload.get("rows", len(frame))),
            },
            "quality_metrics": quality_report or data_quality,
            "execution_graph": compiled_result["execution_graph"],
        }
    if quality_report is not None:
        payload["quality_report"] = quality_report

    # Coerced once, here, so the background job stores a JSON-safe result too.
    return _jsonable(payload)


def _build_and_register(
    db: Session,
    request: EtlCopilotBuildRequest,
) -> Dict[str, Any]:
    """Build through the existing path and persist the corresponding run."""
    started_at = datetime.datetime.utcnow()
    started_clock = time.perf_counter()
    run = None
    run_project_id = None
    pipeline_json = None

    if request.pipeline_version_id is not None:
        version = (
            db.query(PipelineVersion)
            .filter(PipelineVersion.id == request.pipeline_version_id)
            .first()
        )
        if version is None:
            raise HTTPException(status_code=404, detail="Pipeline version not found.")
        pipeline_json = version.pipeline_json
        pipeline = (
            db.query(PipelineDefinition)
            .filter(PipelineDefinition.id == version.pipeline_definition_id)
            .first()
        )
        if pipeline is None or pipeline.status == "deleted":
            raise HTTPException(status_code=404, detail="Pipeline not found.")
        run_project_id = pipeline.project_id
        run = PipelineRun(
            project_id=run_project_id,
            pipeline_version_id=version.id,
            status="running",
            started_at=started_at,
        )
        db.add(run)
        try:
            db.commit()
            db.refresh(run)
        except SQLAlchemyError as exc:
            db.rollback()
            logger.exception("Could not record pipeline run start")
            raise HTTPException(
                status_code=500,
                detail="Could not record pipeline run.",
            ) from exc

    try:
        payload = _build_dataset_and_register(
            db,
            request,
            pipeline_json=pipeline_json,
        )
    except Exception as exc:
        if run is not None:
            run.status = "failed"
            run.completed_at = datetime.datetime.utcnow()
            run.execution_time = max(
                0.0,
                time.perf_counter() - started_clock,
            )
            failure_message = "Build failed (%s)." % type(exc).__name__
            if isinstance(exc, HTTPException):
                detail = _jsonable(exc.detail)
                run.execution_log = json.dumps(
                    {
                        "message": failure_message,
                        "detail": detail,
                        "quality_metrics": (
                            detail.get("report")
                            if isinstance(detail, dict)
                            else None
                        ),
                    },
                    sort_keys=True,
                )
            else:
                run.execution_log = failure_message
            try:
                db.commit()
            except SQLAlchemyError:
                db.rollback()
                logger.exception("Could not persist failed pipeline run")
        raise

    completed_at = datetime.datetime.utcnow()
    if run is None:
        try:
            run = PipelineRun(
                project_id=UUID(payload["project_id"]),
                pipeline_version_id=None,
                status="completed",
                started_at=started_at,
            )
            db.add(run)
        except (KeyError, ValueError) as exc:
            logger.exception("Successful build did not return a project identifier")
            raise HTTPException(
                status_code=500,
                detail="Could not associate the completed build with a project.",
            ) from exc
    run.status = "completed"
    run.completed_at = completed_at
    run.dataset_file_id = payload.get("file_id")
    run.row_count = payload.get("rows")
    run.execution_time = max(
        0.0,
        time.perf_counter() - started_clock,
    )
    if request.pipeline_version_id is not None:
        run.execution_log = json.dumps(
            _jsonable(
                {
                    "message": "Dataset build completed successfully.",
                    "lineage": payload.get("lineage"),
                    "quality_report": payload.get("quality_report"),
                    "execution_metrics": payload.get("execution_metrics"),
                    "quality_metrics": payload.get("quality_report")
                    or payload.get("data_quality"),
                    "row_counts": (
                        payload.get("execution_metrics", {}).get("row_counts")
                        if isinstance(payload.get("execution_metrics"), dict)
                        else {"registered": payload.get("rows")}
                    ),
                }
            ),
            sort_keys=True,
        )
    else:
        run.execution_log = "Dataset build completed successfully."
    try:
        db.commit()
        db.refresh(run)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Dataset built but pipeline run could not be persisted")
        raise HTTPException(
            status_code=500,
            detail="Dataset built, but its run history could not be saved.",
        ) from exc
    payload["pipeline_run_id"] = str(run.id)
    payload["pipeline_version_id"] = (
        str(request.pipeline_version_id) if request.pipeline_version_id else None
    )
    return payload


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health")
async def health():
    """Liveness check. Reports whether the LLM key is configured."""
    configured = True
    detail = None

    try:
        get_copilot()
    except HTTPException as exc:
        configured = False
        detail = exc.detail

    return {
        "status": "ok",
        "service": "etl_copilot",
        "llm_configured": configured,
        "detail": detail,
    }


@router.post("/schema")
def etl_copilot_schema(request: EtlCopilotSchemaRequest):
    """Read the catalog the Copilot will work from.

    Returns exactly what the model is allowed to see - table names, column
    names, primary keys and declared foreign keys. No data value ever leaves
    the database through this endpoint.

    `relationship_count == 0` is the single most useful thing here: without
    declared foreign keys every request will fail with no_join_path, and the
    caller should say so before the user types anything.
    """
    connection_string = _connection_string(request)
    schema = _resolved_schema(request)

    copilot = get_copilot()

    try:
        catalog = copilot.discover_schema(connection_string, schema=schema)
    except DataSourceError as exc:
        detail = str(exc)
        _trace("[ETL] RAISING HTTPException 400 detail=%r" % detail)
        raise HTTPException(status_code=400, detail=detail)

    payload = catalog_for_llm(catalog)

    return {
        "db_type": catalog.db_type,
        "schema_name": catalog.schema_name,
        "table_count": len(payload["tables"]),
        "relationship_count": len(payload["relationships"]),
        "tables": payload["tables"],
        "primary_keys": payload["primary_keys"],
        "relationships": payload["relationships"],
        "usable": len(payload["relationships"]) > 0,
        "notice": (
            None
            if payload["relationships"]
            else (
                "No declared foreign keys were found in this schema. The ETL "
                "Copilot resolves joins from declared relationships only and "
                "will not guess them from column names, so multi-table "
                "requests cannot be built here yet."
            )
        ),
    }


@router.post("/preview")
def etl_copilot_preview(request: EtlCopilotPreviewRequest):
    """Plan the dataset without building it.

    Runs schema discovery, spec generation (with one repair round), join
    resolution, verification, validation and compilation - then stops.

    TEMPORARY DIAGNOSTIC MODE
    -------------------------
    The whole body is wrapped so that ANY unhandled exception is printed to
    the backend console with its full traceback AND returned to the browser
    as JSON. Nothing can reach the client as an opaque 500 any more. Remove
    the outer handler once the failure is identified.
    """
    _trace("[ETL preview] 1/7 route entered")

    try:
        _trace(
            "[ETL preview] 2/7 request parsed"
            " | db_type=%r host=%r port=%r database=%r schema_name=%r"
            " trusted=%r user=%r user_request=%r"
            % (
                request.db_type,
                request.host,
                request.port,
                request.database,
                request.schema_name,
                request.trusted_connection,
                request.username,
                (request.user_request or "")[:120],
            )
        )

        connection_string = _connection_string(request)
        _trace("[ETL preview] 3/7 connection string built")

        schema = _resolved_schema(request)
        _trace("[ETL preview] 4/7 schema resolved -> %r" % (schema,))

        copilot = get_copilot()
        _trace("[ETL preview] 5/7 copilot instance ready")

        try:
            _trace("[ETL preview] 6/7 preview_plan STARTING")
            result = copilot.preview_plan(
                user_request=request.user_request,
                connection_string=connection_string,
                schema=schema,
            )
            _trace("[ETL preview] 6/7 preview_plan COMPLETED")
        except Exception as exc:
            # Maps the known failure classes onto 400/422/502. Anything it does
            # not recognise is re-raised and caught by the outer handler below.
            _trace(
                "[ETL preview] preview_plan raised %s: %s"
                % (type(exc).__name__, exc)
            )
            _raise_for(exc)

        issues = result.get("issues") or []

        response = _jsonable({
            "blocking": result.get("blocking", False),
            "sql": result.get("sql"),
            "explanation": result.get("explanation"),
            "spec": result.get("spec"),
            "entity_table": (result.get("spec") or {}).get("entity_table"),
            "target_column": (result.get("spec") or {}).get("target_column"),
            "columns": result.get("columns") or [],
            "errors": [i for i in issues if i.get("severity") == "error"],
            "warnings": [i for i in issues if i.get("severity") != "error"],
            # preview_plan() executes a 10-row sample. These were computed and
            # then dropped here, so the UI could never show the rows.
            "preview_rows": result.get("preview_rows") or [],
            "has_more": bool(result.get("has_more")),
            "lineage": result.get("lineage"),
            "reconciliation": result.get("reconciliation"),
        })
        _trace("[ETL preview] 7/7 response serialized, returning 200")
        return response

    except HTTPException:
        # 400 / 422 / 502 raised on purpose by _raise_for or _resolve_target.
        # These already carry a readable body - let them through untouched.
        raise

    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        _trace(
            "=== ETL PREVIEW ERROR === %s: %s\n%s=== END ETL PREVIEW ERROR ==="
            % (type(e).__name__, e, tb)
        )

        # Returned, not raised: a RETURNED response travels back out through
        # CORSMiddleware and therefore carries access-control-allow-origin.
        # A raised exception does not, which is why the browser only ever saw
        # "Failed to fetch" instead of this body.
        return JSONResponse(
            status_code=500,
            content={
                "error_type": type(e).__name__,
                "error": str(e),
                "traceback": tb.splitlines()[-25:],
                "stage": "etl_copilot_preview",
            },
        )


@router.post("/build")
def etl_copilot_build(
    request: EtlCopilotBuildRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Build the dataset, reconcile it, and register it as an AutoAI project.

    Returns the same shape /datamodel/extract and /datasource/extract return,
    so the caller can hand `file_id` and `target_column` straight to
    POST /training/start with no translation.

    Set `background: true` to get a 202 and a job_id instead.
    """
    if request.background:
        job_id = str(uuid.uuid4())
        _set_job(job_id, status="pending", stage="queued", progress=0)
        background_tasks.add_task(_run_build_job, job_id, request)
        _trace("[ETL build] job=%s queued" % job_id)

        return JSONResponse(
            status_code=202,
            content={
                "job_id": job_id,
                "status": "started",
                "poll": "/etl-copilot/status/%s" % job_id,
            },
        )

    payload = _build_and_register(db, request)

    return JSONResponse(status_code=201, content=_jsonable(payload))


@router.get("/status/{job_id}")
async def etl_copilot_status(job_id: str):
    """Progress and result of a background build."""
    job = _get_job(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job '%s' was not found. Background jobs do not survive a "
            "server restart." % job_id,
        )

    return job


# ---------------------------------------------------------------------------
# Background jobs
#
# The store is local to this router rather than main.JOB_STATUS. Reaching into
# main for it looked like reuse, but it makes every status call depend on main
# importing successfully - and main runs a PostgreSQL connectivity check at
# import time, so a job lookup could fail for a reason that has nothing to do
# with the job. The two stores also hold different shapes.
#
# Like main.JOB_STATUS this is in-process: jobs do not survive a restart and do
# not work across multiple workers. /status says so in its 404.
# ---------------------------------------------------------------------------

JOB_STATUS: Dict[str, Dict[str, Any]] = {}


def _job_store() -> Dict[str, Dict[str, Any]]:
    return JOB_STATUS


def _set_job(job_id: str, **fields) -> None:
    store = _job_store()
    job = store.setdefault(
        job_id,
        {
            "status": "pending",
            "stage": "queued",
            "progress": 0,
            "result": None,
            "error": None,
            "completed_at": None,
            "logs": [],
            "kind": "etl_copilot",
        },
    )
    previous_state = (job.get("status"), job.get("stage"), job.get("progress"))
    job.update(fields)
    current_state = (job.get("status"), job.get("stage"), job.get("progress"))
    if current_state != previous_state:
        _trace(
            "[ETL build] job=%s status=%s stage=%s progress=%s"
            % (job_id, current_state[0], current_state[1], current_state[2])
        )


def _get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _job_store().get(job_id)


def _run_build_job(job_id: str, request: EtlCopilotBuildRequest) -> None:
    """Background worker. Never raises - every outcome lands in the job."""
    from datetime import datetime  # noqa: PLC0415

    db = None

    try:
        db = SessionLocal()
        _set_job(job_id, status="running", stage="building", progress=20)
        payload = _build_and_register(db, request)
        _trace(
            "[ETL build] job=%s registered project_id=%s file_id=%s rows=%s"
            % (
                job_id,
                payload.get("project_id"),
                payload.get("file_id"),
                payload.get("rows"),
            )
        )
        _set_job(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            result=payload,
            completed_at=datetime.utcnow().isoformat(),
        )
    except HTTPException as exc:
        detail = getattr(exc, "detail", str(exc))
        _trace(
            "[ETL build] job=%s FAILED status_code=%s type=%s message=%s detail=%r"
            % (
                job_id,
                exc.status_code,
                type(exc).__name__,
                str(exc),
                detail,
            )
        )
        _trace(traceback.format_exc())
        _set_job(
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            error={"status_code": exc.status_code, "detail": exc.detail},
            completed_at=datetime.utcnow().isoformat(),
        )
    except Exception as exc:  # noqa: BLE001  (a background job must not die silently)
        detail = getattr(exc, "detail", str(exc))
        _trace(
            "[ETL build] job=%s FAILED type=%s message=%s detail=%r"
            % (job_id, type(exc).__name__, str(exc), detail)
        )
        _trace(traceback.format_exc())
        _set_job(
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            error={"status_code": 500, "detail": str(exc)},
            completed_at=datetime.utcnow().isoformat(),
        )
    finally:
        # A worker thread must not die in its own cleanup: a session that was
        # never opened, or one already closed, would otherwise mask the real
        # outcome that was just recorded on the job.
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
