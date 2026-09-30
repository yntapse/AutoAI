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
import os
import pathlib
import threading
import traceback
import uuid
from decimal import Decimal
from json import JSONDecodeError
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from openai import OpenAIError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import SessionLocal
from services.db_connector import (
    DataSourceError,
    build_connection_string,
    default_schema,
)

from etl_copilot.build_service import BuildError
from etl_copilot.copilot_service import CopilotError, ETLCopilot, catalog_for_llm
from etl_copilot.dataset_compiler import SpecCompileError
from etl_copilot.reconciler import ReconciliationError

# routers.datamodel imports `main` only inside function bodies, so importing it
# here is safe and introduces no cycle.
from routers.datamodel import _create_project_from_frame

router = APIRouter(
    prefix="/etl-copilot",
    tags=["ETL Copilot"],
)


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
        raise HTTPException(status_code=400, detail=str(exc))


def _resolved_schema(request: EtlCopilotConnection) -> str:
    if request.schema_name:
        return request.schema_name
    try:
        return default_schema(request.db_type)
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
        raise HTTPException(
            status_code=400,
            detail=_issue_detail(str(exc), getattr(exc, "issues", None)),
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
        raise HTTPException(status_code=400, detail=str(exc))

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
        raise HTTPException(
            status_code=400,
            detail=(
                "Target column '%s' is not in the generated dataset. "
                "Available columns: %s" % (requested, ", ".join(map(str, columns)))
            ),
        )

    if not columns:
        raise HTTPException(status_code=400, detail="The dataset has no columns.")

    return str(columns[-1])


def _build_and_register(
    db: Session,
    request: EtlCopilotBuildRequest,
) -> Dict[str, Any]:
    """The whole build path, shared by the sync and background entry points."""

    copilot = get_copilot()
    connection_string = _connection_string(request)
    schema = _resolved_schema(request)

    try:
        result = copilot.create_dataset(
            user_request=request.user_request,
            connection_string=connection_string,
            schema=schema,
            dataset_name=request.project_name,
            # AutoAI owns the file and the file_id. Letting BuildService also
            # write one would give a single dataset two ids and two copies on
            # disk that can drift apart.
            persist=False,
        )
    except Exception as exc:
        _raise_for(exc)

    frame = result.pop("frame", None)

    if frame is None:
        raise HTTPException(
            status_code=500,
            detail="The dataset was built but its rows were not returned.",
        )

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
    payload = _create_project_from_frame(
        db=db,
        df=frame,
        project_name=request.project_name,
        target_column=target_column,
        source_label=source_label,
    )

    payload["user_request"] = request.user_request
    payload["sql"] = result.get("sql")
    payload["explanation"] = result.get("explanation")
    payload["reconciliation"] = result.get("reconciliation")
    payload["warnings"] = result.get("warnings")
    payload["lineage"] = result.get("lineage")
    payload["spec"] = result.get("spec")

    # Coerced once, here, so the background job stores a JSON-safe result too.
    return _jsonable(payload)


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
        raise HTTPException(status_code=400, detail=str(exc))

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
            "columns": _preview_columns(result.get("spec")),
            "errors": [i for i in issues if i.get("severity") == "error"],
            "warnings": [i for i in issues if i.get("severity") != "error"],
            # preview_plan() executes a 10-row sample. These were computed and
            # then dropped here, so the UI could never show the rows.
            "preview_rows": result.get("preview_rows") or [],
            "has_more": bool(result.get("has_more")),
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


def _preview_columns(spec: Optional[Dict[str, Any]]) -> List[str]:
    """The columns the dataset will have, from the spec alone.

    The plan is not executed during a preview, so this is derived rather than
    observed: entity key, then attributes, then measures - the order the
    compiler emits.
    """
    if not spec:
        return []

    columns: List[str] = list(spec.get("entity_key") or [])

    for attribute in spec.get("attributes") or []:
        columns.append(attribute.get("name") or attribute.get("source_column"))

    for measure in spec.get("measures") or []:
        columns.append(measure.get("name"))

    return [str(c) for c in columns if c]


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
    job.update(fields)


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
        _set_job(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            result=payload,
            completed_at=datetime.utcnow().isoformat(),
        )
    except HTTPException as exc:
        _set_job(
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            error={"status_code": exc.status_code, "detail": exc.detail},
            completed_at=datetime.utcnow().isoformat(),
        )
    except Exception as exc:  # noqa: BLE001  (a background job must not die silently)
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
