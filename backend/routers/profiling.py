import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import SessionLocal
from models.pipeline_run import PipelineRun
from models.source_profile_snapshot import SourceProfileSnapshot
from routers.etl_copilot import EtlCopilotConnection, _connection_string, _resolved_schema
from services.data_profiler import profile_source_tables
from services.db_connector import DataSourceError
from services.sql_builder import MAX_TABLES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profiling", tags=["data-profiling"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ProfilingDatasource(EtlCopilotConnection):
    @validator("db_type")
    @classmethod
    def validate_db_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in {"mssql", "postgresql"}:
            raise ValueError("db_type must be 'mssql' or 'postgresql'.")
        return normalized


class ProfilingAnalyzeRequest(BaseModel):
    datasource: ProfilingDatasource
    selected_tables: List[str] = Field(
        ...,
        min_items=1,
        max_items=MAX_TABLES,
        description="Source tables to profile (same maximum as the ETL builder).",
    )
    pipeline_run_id: Optional[UUID] = None
    null_threshold: float = Field(
        5.0,
        ge=0,
        le=100,
        description="Flag columns whose null percentage exceeds this value.",
    )

    @validator("selected_tables")
    @classmethod
    def validate_selected_tables(cls, tables: List[str]) -> List[str]:
        cleaned = [table.strip() for table in tables]
        if any(not table for table in cleaned):
            raise ValueError("selected_tables must not contain blank names.")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("selected_tables must contain unique table names.")
        return cleaned


class ColumnProfileResponse(BaseModel):
    column_name: str
    data_type: str
    null_count: int
    null_percentage: Optional[float]
    distinct_count: Optional[int]
    distinct_percentage: Optional[float]
    min: Any = None
    max: Any = None
    mean: Optional[float] = None
    median: Optional[float] = None
    top_values: List[Dict[str, Any]]
    std_dev: Optional[float] = None
    quartiles: Optional[Dict[str, Optional[float]]] = None
    outliers: Dict[str, Any] = Field(default_factory=dict)
    type_analysis: Dict[str, Any] = Field(default_factory=dict)


class TableProfileResponse(BaseModel):
    table_name: str
    row_count: int
    column_count: int
    columns: List[ColumnProfileResponse]
    duplicate_rows: Dict[str, Any] = Field(default_factory=dict)
    duplicate_keys: Dict[str, Any] = Field(default_factory=dict)
    issues: List[Dict[str, Any]] = Field(default_factory=list)
    quality_score: Optional[float] = None
    score_coverage: Optional[float] = None
    score_formula: Dict[str, Any] = Field(default_factory=dict)


class ProfilingAnalyzeResponse(BaseModel):
    snapshot_id: UUID
    pipeline_run_id: Optional[UUID]
    analyzed_at: datetime
    schema_name: str
    table_count: int
    tables: List[TableProfileResponse]
    summary: Dict[str, Any] = Field(default_factory=dict)
    issues: List[Dict[str, Any]] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)


@router.post("/analyze", response_model=ProfilingAnalyzeResponse, status_code=201)
def analyze_source_data(
    request: ProfilingAnalyzeRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Analyze selected source tables and persist the full profile snapshot."""
    pipeline_run = None
    try:
        if request.pipeline_run_id is not None:
            pipeline_run = (
                db.query(PipelineRun)
                .filter(PipelineRun.id == request.pipeline_run_id)
                .first()
            )
            if pipeline_run is None:
                raise HTTPException(status_code=404, detail="Pipeline run not found.")

        connection_string = _connection_string(request.datasource)
        schema = _resolved_schema(request.datasource)
        profile = profile_source_tables(
            connection_string=connection_string,
            schema=schema,
            selected_tables=request.selected_tables,
            null_threshold=request.null_threshold,
        )
        analyzed_at = datetime.utcnow()
        snapshot = SourceProfileSnapshot(
            pipeline_run=pipeline_run,
            db_type=request.datasource.db_type,
            schema_name=schema,
            selected_tables=request.selected_tables,
            profile_json=profile,
            created_at=analyzed_at,
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
    except HTTPException:
        db.rollback()
        raise
    except DataSourceError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to save source profile snapshot")
        raise HTTPException(
            status_code=500,
            detail="Failed to save the source profile snapshot.",
        ) from exc

    return {
        "snapshot_id": snapshot.id,
        "pipeline_run_id": snapshot.pipeline_run_id,
        "analyzed_at": snapshot.created_at,
        **profile,
    }
