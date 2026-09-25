"""
ETL Agent HTTP surface.

    POST /etl/agent/run     run the agent end to end, return file_id
    GET  /etl/agent/tools   list the registered tools (for Swagger / debugging)

One request runs the whole loop. There is no persistence and no new database
table: the working DataFrame lives in an ETLSession for the life of the
request and is discarded afterwards. What survives is what finish_etl writes -
the CSV, the Project row and the DatasetProfile row, through the same helper
/datamodel/extract uses.

Pydantic v1 style, matching routers/datamodel.py. Module-level imports are
limited to services.*, database and models.* so that main.py can import this
router at startup without a circular import; anything main owns is imported 
inside the call.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from database import SessionLocal
from services.db_connector import (
    DataSourceError,
    build_connection_string,
    default_schema,
)
from services.etl_agent import (
    MAX_STEPS,
    ETLAgentError,
    ETLSession,
    get_tool_names,
    get_tools_schema,
    run_etl_agent,
)
from services.sql_builder import MAX_TABLES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/etl/agent", tags=["etl-agent"])


# ---------------------------------------------------------------------------
# Local get_db - identical behaviour to main.get_db, no import from main.
# ---------------------------------------------------------------------------


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class ETLAgentRunRequest(BaseModel):
    """Connection details plus the natural-language request.

    Connection fields mirror routers.datamodel.DataModelConnection field for
    field, so the same body works against both routers.
    """

    db_type: str = Field(default="mssql", description="'mssql' or 'postgresql'")
    host: str = Field(..., description="Server hostname or IP, e.g. localhost")
    port: Optional[int] = Field(default=None, description="Defaults to 1433 / 5432")
    database: str = Field(..., description="Database name")
    username: Optional[str] = Field(default=None)
    password: Optional[str] = Field(default=None)
    trusted_connection: bool = Field(
        default=False, description="SQL Server Windows Authentication"
    )
    odbc_driver: Optional[str] = Field(default=None)
    schema_name: Optional[str] = Field(
        default=None, description="Defaults to dbo / public."
    )

    request: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="What the dataset is for, in plain language.",
    )
    tables: Optional[List[str]] = Field(
        default=None,
        max_items=8,
        description=(
            "Optional allow-list. When given, the agent may only touch these "
            "tables. When omitted it discovers them with inspect_tables."
        ),
    )
    max_steps: int = Field(
        default=MAX_STEPS,
        ge=1,
        le=MAX_STEPS,
        description=f"Tool-calling rounds. Hard ceiling {MAX_STEPS}.",
    )

    @validator("db_type")
    @classmethod
    def _check_db_type(cls, value: str) -> str:
        normalised = (value or "").strip().lower()
        if normalised not in {"mssql", "postgresql"}:
            raise ValueError("db_type must be 'mssql' or 'postgresql'.")
        return normalised

    @validator("tables")
    @classmethod
    def _check_tables(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        cleaned = [str(name).strip() for name in value if str(name).strip()]
        if not cleaned:
            raise ValueError("tables, when supplied, must contain a table name.")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("tables must be unique.")
        return cleaned


# ---------------------------------------------------------------------------
# Shared internals
# ---------------------------------------------------------------------------


def _connection_string(request: ETLAgentRunRequest) -> str:
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


def _resolved_schema(request: ETLAgentRunRequest) -> str:
    if request.schema_name:
        return request.schema_name
    try:
        return default_schema(request.db_type)
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/run")
def etl_agent_run(
    request: ETLAgentRunRequest, db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Run the ETL agent end to end.

    On success the response carries `file_id`, which is the handoff point to
    the existing AutoML flow - the same identifier a CSV upload or
    /datamodel/extract produces.

    Status values:
        complete   finish_etl ran; file_id is set
        partial    the step ceiling was reached; nothing was saved
        incomplete the model answered without calling a tool
    """
    if request.tables and len(request.tables) > MAX_TABLES:
        logger.info(
            "ETL run allow-lists %s tables; at most %s can be joined at once.",
            len(request.tables),
            MAX_TABLES,
        )

    session = ETLSession(
        connection_string=_connection_string(request),
        db_type=request.db_type,
        schema=_resolved_schema(request),
        db=db,
        allowed_tables=request.tables,
    )

    try:
        result = run_etl_agent(
            session=session,
            user_request=request.request,
            max_steps=request.max_steps,
        )
    except ETLAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception:
        logger.exception("ETL agent run failed")
        raise HTTPException(
            status_code=500, detail="The ETL Agent could not complete the run."
        )

    if result["status"] == "error":
        raise HTTPException(status_code=502, detail=result["message"])

    return result


@router.get("/tools")
def etl_agent_tools() -> Dict[str, Any]:
    """Every tool the agent can call, and the schema offered at the start of a
    run (before a dataset exists, the transform tools are withheld)."""
    initial = get_tools_schema(None)
    return {
        "all_tool_names": get_tool_names(),
        "initial_tool_names": [tool["function"]["name"] for tool in initial],
        "max_steps": MAX_STEPS,
    }
