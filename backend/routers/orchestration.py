"""Pipeline operations: scheduling, monitoring and run metrics.

The three capabilities Pipeline Management was missing. Everything here reads
or writes tables that already exist (pipeline_definitions, pipeline_versions,
pipeline_runs, pipeline_schedules, monitoring_alerts) and delegates every
calculation to a pure service.

What this router deliberately does NOT do:
  * execute anything - POST /pipeline/{id}/run already does that, and a
    schedule records WHEN to call it, never a second way to run
  * build or verify joins, evaluate quality, or answer questions - those
    routers and services already exist and are not duplicated here
  * re-implement version history or version compare - routers/pipelines.py
    already serves both

Credentials: a schedule stores `credential_env`, the NAME of an environment
variable the worker resolves at fire time. No password reaches a row.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import SessionLocal
from models.monitoring_alert import ALERT_TYPES, MonitoringAlert
from models.pipeline_definition import PipelineDefinition
from models.pipeline_run import PipelineRun
from models.pipeline_schedule import (
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_PAUSED,
    PipelineSchedule,
)
from models.pipeline_version import PipelineVersion
from models.project import Project
from services import run_metrics
from services.schedule_planner import ScheduleError, advance, describe, resolve

# Datasets land in main.UPLOAD_DIR; the path is restated rather than imported
# so this router never triggers main.py's database connection.
UPLOAD_DIR = Path("uploads")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orchestration", tags=["pipeline-operations"])

_ALERT_SEVERITIES = ("info", "warning", "critical")
_OPEN = "open"
_RESOLVED = "resolved"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------


class ScheduleCreateRequest(BaseModel):
    pipeline_definition_id: UUID
    # Omit to pin the pipeline's latest saved version. A draft can never be
    # scheduled because only saved versions exist as rows.
    pipeline_version_id: Optional[UUID] = None
    data_source_id: UUID
    frequency: str
    cron_expression: Optional[str] = None
    run_at: Optional[datetime] = None
    credential_env: Optional[str] = Field(default=None, max_length=120)
    target_column: Optional[str] = None
    freshness_sla_hours: Optional[int] = Field(default=None, ge=1, le=8760)
    created_by: Optional[str] = None

    @validator("credential_env")
    @classmethod
    def validate_credential_env(cls, value: Optional[str]) -> Optional[str]:
        """Accept an env var NAME, never a secret value."""
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if not cleaned.replace("_", "").isalnum():
            raise ValueError(
                "credential_env must be an environment variable name, "
                "not a password."
            )
        return cleaned.upper()


class ScheduleUpdateRequest(BaseModel):
    frequency: Optional[str] = None
    cron_expression: Optional[str] = None
    run_at: Optional[datetime] = None
    credential_env: Optional[str] = Field(default=None, max_length=120)
    target_column: Optional[str] = None
    freshness_sla_hours: Optional[int] = Field(default=None, ge=1, le=8760)


class AlertCreateRequest(BaseModel):
    pipeline_definition_id: Optional[UUID] = None
    data_source_id: Optional[UUID] = None
    pipeline_run_id: Optional[UUID] = None
    alert_type: str
    severity: str = "warning"
    title: str = Field(..., min_length=1, max_length=255)
    message: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None

    @validator("alert_type")
    @classmethod
    def validate_alert_type(cls, value: str) -> str:
        normalised = (value or "").strip().lower()
        if normalised not in ALERT_TYPES:
            raise ValueError(
                f"alert_type must be one of: {', '.join(ALERT_TYPES)}."
            )
        return normalised

    @validator("severity")
    @classmethod
    def validate_severity(cls, value: str) -> str:
        normalised = (value or "").strip().lower()
        if normalised not in _ALERT_SEVERITIES:
            raise ValueError(
                f"severity must be one of: {', '.join(_ALERT_SEVERITIES)}."
            )
        return normalised


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _require_pipeline(db: Session, pipeline_id: UUID) -> PipelineDefinition:
    pipeline = (
        db.query(PipelineDefinition)
        .filter(PipelineDefinition.id == pipeline_id)
        .first()
    )
    if pipeline is None or pipeline.status == "deleted":
        raise HTTPException(status_code=404, detail="Pipeline not found.")
    return pipeline


def _require_schedule(db: Session, schedule_id: UUID) -> PipelineSchedule:
    schedule = (
        db.query(PipelineSchedule)
        .filter(
            PipelineSchedule.id == schedule_id,
            PipelineSchedule.is_deleted.is_(False),
        )
        .first()
    )
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return schedule


def _run_dict(run: PipelineRun) -> Dict[str, Any]:
    return {
        "id": str(run.id),
        "pipeline_version_id": (
            str(run.pipeline_version_id) if run.pipeline_version_id else None
        ),
        "project_id": str(run.project_id) if run.project_id else None,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "row_count": run.row_count,
        "execution_time": run.execution_time,
        "dataset_file_id": run.dataset_file_id,
    }


def _runs_for_pipeline(
    db: Session, pipeline: PipelineDefinition, limit: int = 200
) -> List[Dict[str, Any]]:
    """Newest-first runs across every version of one pipeline.

    routers/pipelines.py serves run history per PROJECT; a project can hold
    several pipelines, so per-pipeline history did not exist.
    """
    version_ids = [version.id for version in pipeline.versions]
    if not version_ids:
        return []
    runs = (
        db.query(PipelineRun)
        .filter(PipelineRun.pipeline_version_id.in_(version_ids))
        .order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
        .limit(limit)
        .all()
    )
    return [_run_dict(run) for run in runs]


def _schedule_dict(schedule: PipelineSchedule) -> Dict[str, Any]:
    return {
        "id": str(schedule.id),
        "pipeline_definition_id": str(schedule.pipeline_definition_id),
        "pipeline_version_id": str(schedule.pipeline_version_id),
        "data_source_id": str(schedule.data_source_id),
        "frequency": schedule.frequency,
        "cron_expression": schedule.cron_expression,
        "run_at": schedule.run_at.isoformat() if schedule.run_at else None,
        "status": schedule.status,
        "is_active": schedule.is_active,
        "description": describe(
            schedule.frequency, schedule.cron_expression, schedule.run_at
        ),
        "credential_env": schedule.credential_env,
        "target_column": schedule.target_column,
        "freshness_sla_hours": schedule.freshness_sla_hours,
        "next_run_at": (
            schedule.next_run_at.isoformat() if schedule.next_run_at else None
        ),
        "last_run_at": (
            schedule.last_run_at.isoformat() if schedule.last_run_at else None
        ),
        "last_run_status": schedule.last_run_status,
        "last_error": schedule.last_error,
        "run_count": schedule.run_count,
        "created_by": schedule.created_by,
        "created_at": schedule.created_at.isoformat() if schedule.created_at else None,
    }


def _alert_dict(alert: MonitoringAlert) -> Dict[str, Any]:
    return {
        "id": str(alert.id),
        "pipeline_definition_id": (
            str(alert.pipeline_definition_id)
            if alert.pipeline_definition_id
            else None
        ),
        "data_source_id": (
            str(alert.data_source_id) if alert.data_source_id else None
        ),
        "pipeline_run_id": (
            str(alert.pipeline_run_id) if alert.pipeline_run_id else None
        ),
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "title": alert.title,
        "message": alert.message,
        "detail": alert.detail,
        "status": alert.status,
        "fingerprint": alert.fingerprint,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
    }


# --------------------------------------------------------------------------
# Run metrics
# --------------------------------------------------------------------------


@router.get("/pipelines", response_model=List[Dict[str, Any]])
def list_pipelines_with_metrics(
    project_id: Optional[UUID] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Pipeline List and Pipeline Dashboard rows: definition + run metrics."""
    try:
        query = db.query(PipelineDefinition).filter(
            PipelineDefinition.status != "deleted"
)
        if project_id is not None:
            query = query.filter(PipelineDefinition.project_id == project_id)
        pipelines = query.order_by(PipelineDefinition.created_at.desc()).all()

        rows: List[Dict[str, Any]] = []
        for pipeline in pipelines:
            runs = _runs_for_pipeline(db, pipeline)
            versions = sorted(
                pipeline.versions, key=lambda item: item.version_number
            )
            schedules = (
                db.query(PipelineSchedule)
                .filter(
                    PipelineSchedule.pipeline_definition_id == pipeline.id,
                    PipelineSchedule.is_deleted.is_(False),
                )
                .all()
            )
            open_alerts = (
                db.query(MonitoringAlert)
                .filter(
                    MonitoringAlert.pipeline_definition_id == pipeline.id,
                    MonitoringAlert.status == _OPEN,
                )
                .count()
            )
            rows.append(
                {
                    "pipeline_id": str(pipeline.id),
                    "pipeline_name": pipeline.pipeline_name,
                    "project_id": str(pipeline.project_id),
                    "description": pipeline.description,
                    "status": pipeline.status,
                    "created_at": (
                        pipeline.created_at.isoformat()
                        if pipeline.created_at
                        else None
                    ),
                    "updated_at": (
                        pipeline.updated_at.isoformat()
                        if pipeline.updated_at
                        else None
                    ),
                    "version_count": len(versions),
                    "latest_version": (
                        versions[-1].version_number if versions else None
                    ),
                    "schedule_count": len(schedules),
                    "active_schedules": sum(
                        1 for schedule in schedules if schedule.is_active
                    ),
                    "next_run_at": min(
                        (
                            schedule.next_run_at.isoformat()
                            for schedule in schedules
                            if schedule.is_active and schedule.next_run_at
                        ),
                        default=None,
                    ),
                    "open_alerts": open_alerts,
                    "metrics": run_metrics.summarise(runs),
                }
            )
        return rows
    except SQLAlchemyError as exc:
        logger.exception("Failed to list pipelines with metrics")
        raise HTTPException(
            status_code=500, detail="Failed to load pipelines."
        ) from exc


@router.get("/dashboard", response_model=Dict[str, Any])
def pipeline_dashboard(
    project_id: Optional[UUID] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Portfolio totals plus the rows behind them, for the dashboard."""
    rows = list_pipelines_with_metrics(project_id=project_id, db=db)
    alert_query = db.query(MonitoringAlert).filter(
        MonitoringAlert.status == _OPEN
    )
    if project_id is not None:
        pipeline_ids = [UUID(row["pipeline_id"]) for row in rows]
        alert_query = (
            alert_query.filter(
                MonitoringAlert.pipeline_definition_id.in_(pipeline_ids)
            )
            if pipeline_ids
            else alert_query.filter(False)
        )
    alerts = alert_query.order_by(MonitoringAlert.created_at.desc()).limit(10).all()

    return {
        "totals": run_metrics.portfolio(rows),
        "pipelines": rows,
        "open_alerts": [_alert_dict(alert) for alert in alerts],
        "generated_at": datetime.utcnow().isoformat(),
    }


@router.get("/pipelines/{pipeline_id}/runs", response_model=List[Dict[str, Any]])
def list_pipeline_runs(
    pipeline_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Run History tab. Per-pipeline, which the per-project endpoint cannot do."""
    pipeline = _require_pipeline(db, pipeline_id)
    return _runs_for_pipeline(db, pipeline, limit=limit)


@router.get("/pipelines/{pipeline_id}/metrics", response_model=Dict[str, Any])
def pipeline_run_metrics(pipeline_id: UUID, db: Session = Depends(get_db)):
    """Success rate, durations, rows processed and the trend series."""
    pipeline = _require_pipeline(db, pipeline_id)
    runs = _runs_for_pipeline(db, pipeline)
    return {
        "pipeline_id": str(pipeline.id),
        "pipeline_name": pipeline.pipeline_name,
        "metrics": run_metrics.summarise(runs),
        "trend": run_metrics.trend(runs),
    }


# --------------------------------------------------------------------------
# Scheduling
# --------------------------------------------------------------------------


@router.post("/schedules", response_model=Dict[str, Any], status_code=201)
def create_schedule(
    request: ScheduleCreateRequest, db: Session = Depends(get_db)
):
    """Pin a saved version to a schedule. Never a draft - see the model."""
    pipeline = _require_pipeline(db, request.pipeline_definition_id)

    if request.pipeline_version_id is not None:
        version = (
            db.query(PipelineVersion)
            .filter(
                PipelineVersion.id == request.pipeline_version_id,
                PipelineVersion.pipeline_definition_id == pipeline.id,
            )
            .first()
        )
        if version is None:
            raise HTTPException(
                status_code=404,
                detail="That version does not belong to this pipeline.",
            )
    else:
        if not pipeline.versions:
            raise HTTPException(
                status_code=409,
                detail="This pipeline has no saved version to schedule.",
            )
        version = max(pipeline.versions, key=lambda item: item.version_number)

    try:
        plan = resolve(
            frequency=request.frequency,
            now=datetime.utcnow(),
            cron_expression=request.cron_expression,
            run_at=request.run_at,
        )
    except ScheduleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    schedule = PipelineSchedule(
        pipeline_definition_id=pipeline.id,
        pipeline_version_id=version.id,
        data_source_id=request.data_source_id,
        frequency=plan["frequency"],
        cron_expression=plan["cron_expression"],
        run_at=plan["run_at"],
        next_run_at=plan["next_run_at"],
        status=STATUS_ACTIVE,
        credential_env=request.credential_env,
        target_column=request.target_column,
        freshness_sla_hours=request.freshness_sla_hours,
        created_by=request.created_by,
        run_count=0,
        is_deleted=False,
    )
    try:
        db.add(schedule)
        db.commit()
        db.refresh(schedule)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to create schedule")
        raise HTTPException(
            status_code=500, detail="Failed to create the schedule."
        ) from exc

    return _schedule_dict(schedule)


@router.get("/schedules", response_model=List[Dict[str, Any]])
def list_schedules(
    pipeline_id: Optional[UUID] = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(PipelineSchedule).filter(
        PipelineSchedule.is_deleted.is_(False)
    )
    if pipeline_id is not None:
        query = query.filter(
            PipelineSchedule.pipeline_definition_id == pipeline_id
        )
    schedules = query.order_by(PipelineSchedule.created_at.desc()).all()
    return [_schedule_dict(schedule) for schedule in schedules]


@router.get("/schedules/due", response_model=List[Dict[str, Any]])
def list_due_schedules(
    as_of: Optional[datetime] = Query(default=None),
    db: Session = Depends(get_db),
):
    """What a worker would pick up now. Declared before /schedules/{id}."""
    moment = as_of or datetime.utcnow()
    schedules = (
        db.query(PipelineSchedule)
        .filter(
            PipelineSchedule.is_deleted.is_(False),
            PipelineSchedule.status == STATUS_ACTIVE,
            PipelineSchedule.next_run_at.isnot(None),
            PipelineSchedule.next_run_at <= moment,
        )
        .order_by(PipelineSchedule.next_run_at.asc())
        .all()
    )
    return [_schedule_dict(schedule) for schedule in schedules]


@router.get("/schedules/{schedule_id}", response_model=Dict[str, Any])
def get_schedule(schedule_id: UUID, db: Session = Depends(get_db)):
    return _schedule_dict(_require_schedule(db, schedule_id))


@router.put("/schedules/{schedule_id}", response_model=Dict[str, Any])
def update_schedule(
    schedule_id: UUID,
    request: ScheduleUpdateRequest,
    db: Session = Depends(get_db),
):
    schedule = _require_schedule(db, schedule_id)

    if request.frequency is not None or request.cron_expression is not None:
        try:
            plan = resolve(
                frequency=request.frequency or schedule.frequency,
                now=datetime.utcnow(),
                cron_expression=request.cron_expression
                or schedule.cron_expression,
                run_at=request.run_at or schedule.run_at,
            )
        except ScheduleError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        schedule.frequency = plan["frequency"]
        schedule.cron_expression = plan["cron_expression"]
        schedule.run_at = plan["run_at"]
        schedule.next_run_at = plan["next_run_at"]

    if request.credential_env is not None:
        schedule.credential_env = request.credential_env
    if request.target_column is not None:
        schedule.target_column = request.target_column
    if request.freshness_sla_hours is not None:
        schedule.freshness_sla_hours = request.freshness_sla_hours

    try:
        db.commit()
        db.refresh(schedule)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to update schedule")
        raise HTTPException(
            status_code=500, detail="Failed to update the schedule."
        ) from exc
    return _schedule_dict(schedule)


def _set_status(
    db: Session, schedule_id: UUID, status: str, recompute: bool
) -> Dict[str, Any]:
    schedule = _require_schedule(db, schedule_id)
    schedule.status = status

    if recompute:
        # Resuming must not fire immediately for every minute it was paused.
        schedule.next_run_at = advance(
            schedule.frequency, schedule.cron_expression, datetime.utcnow()
        )
        if schedule.frequency == "once" and schedule.run_at:
            schedule.next_run_at = (
                schedule.run_at if schedule.run_at > datetime.utcnow() else None
            )
    else:
        schedule.next_run_at = None

    try:
        db.commit()
        db.refresh(schedule)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to change schedule status")
        raise HTTPException(
            status_code=500, detail="Failed to change the schedule status."
        ) from exc
    return _schedule_dict(schedule)


@router.post("/schedules/{schedule_id}/pause", response_model=Dict[str, Any])
def pause_schedule(schedule_id: UUID, db: Session = Depends(get_db)):
    return _set_status(db, schedule_id, STATUS_PAUSED, recompute=False)


@router.post("/schedules/{schedule_id}/resume", response_model=Dict[str, Any])
def resume_schedule(schedule_id: UUID, db: Session = Depends(get_db)):
    return _set_status(db, schedule_id, STATUS_ACTIVE, recompute=True)


@router.post("/schedules/{schedule_id}/disable", response_model=Dict[str, Any])
def disable_schedule(schedule_id: UUID, db: Session = Depends(get_db)):
    return _set_status(db, schedule_id, STATUS_DISABLED, recompute=False)


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: UUID, db: Session = Depends(get_db)):
    """Soft delete: run history keeps pointing at a schedule that existed."""
    schedule = _require_schedule(db, schedule_id)
    schedule.is_deleted = True
    schedule.status = STATUS_DISABLED
    schedule.next_run_at = None
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to delete schedule")
        raise HTTPException(
            status_code=500, detail="Failed to delete the schedule."
        ) from exc
    return None


# --------------------------------------------------------------------------
# Monitoring
# --------------------------------------------------------------------------


@router.get("/monitoring/overview", response_model=Dict[str, Any])
def monitoring_overview(
    pipeline_id: Optional[UUID] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Monitoring tab: health, freshness against the SLA, and open alerts."""
    pipelines = (
        [_require_pipeline(db, pipeline_id)]
        if pipeline_id is not None
        else db.query(PipelineDefinition).filter(
            PipelineDefinition.status != "deleted"
            ).all()
        )

    now = datetime.utcnow()
    health: List[Dict[str, Any]] = []
    for pipeline in pipelines:
        runs = _runs_for_pipeline(db, pipeline)
        metrics = run_metrics.summarise(runs)
        schedules = (
            db.query(PipelineSchedule)
            .filter(
                PipelineSchedule.pipeline_definition_id == pipeline.id,
                PipelineSchedule.is_deleted.is_(False),
            )
            .all()
        )

        # Freshness: only a schedule that declares an SLA can breach one.
        sla_hours = min(
            (
                schedule.freshness_sla_hours
                for schedule in schedules
                if schedule.freshness_sla_hours
            ),
            default=None,
        )
        last_success = next(
            (
                run
                for run in runs
                if str(run.get("status", "")).lower()
                in run_metrics.SUCCESS_STATES
            ),
            None,
        )
        hours_since_success = None
        if last_success and last_success.get("completed_at"):
            try:
                completed = datetime.fromisoformat(last_success["completed_at"])
                hours_since_success = round(
                    (now - completed).total_seconds() / 3600.0, 2
                )
            except (TypeError, ValueError):
                hours_since_success = None

        health.append(
            {
                "pipeline_id": str(pipeline.id),
                "pipeline_name": pipeline.pipeline_name,
                "metrics": metrics,
                "freshness_sla_hours": sla_hours,
                "hours_since_last_success": hours_since_success,
                "stale": bool(
                    sla_hours
                    and hours_since_success is not None
                    and hours_since_success > sla_hours
                ),
                "never_succeeded": last_success is None,
                "open_alerts": (
                    db.query(MonitoringAlert)
                    .filter(
                        MonitoringAlert.pipeline_definition_id == pipeline.id,
                        MonitoringAlert.status == _OPEN,
                    )
                    .count()
                ),
            }
        )

    alert_query = db.query(MonitoringAlert).filter(
        MonitoringAlert.status == _OPEN
    )
    if pipeline_id is not None:
        alert_query = alert_query.filter(
            MonitoringAlert.pipeline_definition_id == pipeline_id
        )

    return {
        "generated_at": now.isoformat(),
        "pipelines": health,
        "alert_types": list(ALERT_TYPES),
        "open_alerts": [
            _alert_dict(alert)
            for alert in alert_query.order_by(
                MonitoringAlert.created_at.desc()
            )
            .limit(50)
            .all()
        ],
    }


@router.get("/alerts", response_model=List[Dict[str, Any]])
def list_alerts(
    pipeline_id: Optional[UUID] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(MonitoringAlert)
    if pipeline_id is not None:
        query = query.filter(
            MonitoringAlert.pipeline_definition_id == pipeline_id
        )
    if status is not None:
        query = query.filter(MonitoringAlert.status == status.strip().lower())
    alerts = (
        query.order_by(MonitoringAlert.created_at.desc()).limit(limit).all()
    )
    return [_alert_dict(alert) for alert in alerts]


@router.post("/alerts", response_model=Dict[str, Any], status_code=201)
def create_alert(request: AlertCreateRequest, db: Session = Depends(get_db)):
    """Record a detected condition, de-duplicated while it stays open."""
    fingerprint = "|".join(
        [
            str(request.pipeline_definition_id or ""),
            str(request.data_source_id or ""),
            request.alert_type,
            request.title.strip().lower(),
        ]
    )

    existing = (
        db.query(MonitoringAlert)
        .filter(
            MonitoringAlert.fingerprint == fingerprint,
            MonitoringAlert.status == _OPEN,
        )
        .first()
    )
    if existing is not None:
        # A detector that runs every minute must not create a row every
        # minute. The open alert is returned unchanged.
        return _alert_dict(existing)

    alert = MonitoringAlert(
        pipeline_definition_id=request.pipeline_definition_id,
        data_source_id=request.data_source_id,
        pipeline_run_id=request.pipeline_run_id,
        alert_type=request.alert_type,
        severity=request.severity,
        title=request.title.strip(),
        message=request.message,
        detail=request.detail,
        fingerprint=fingerprint,
        status=_OPEN,
    )
    try:
        db.add(alert)
        db.commit()
        db.refresh(alert)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to record alert")
        raise HTTPException(
            status_code=500, detail="Failed to record the alert."
        ) from exc
    return _alert_dict(alert)


@router.post("/alerts/{alert_id}/resolve", response_model=Dict[str, Any])
def resolve_alert(alert_id: UUID, db: Session = Depends(get_db)):
    alert = (
        db.query(MonitoringAlert).filter(MonitoringAlert.id == alert_id).first()
    )
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found.")
    alert.status = _RESOLVED
    alert.resolved_at = datetime.utcnow()
    try:
        db.commit()
        db.refresh(alert)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to resolve alert")
        raise HTTPException(
            status_code=500, detail="Failed to resolve the alert."
        ) from exc
    return _alert_dict(alert)


# --------------------------------------------------------------------------
# AutoML handoff
# --------------------------------------------------------------------------


@router.get(
    "/pipelines/{pipeline_id}/automl-readiness", response_model=Dict[str, Any]
)
def automl_readiness(pipeline_id: UUID, db: Session = Depends(get_db)):
    """Is the executed dataset ready to train on, and on what target?

    Reuses main.assess_training_readiness - the same check the training path
    already applies - via a deferred import, the pattern services/etl_agent.py
    uses to avoid a circular import.
    """
    pipeline = _require_pipeline(db, pipeline_id)
    runs = _runs_for_pipeline(db, pipeline)
    successful = [
        run
        for run in runs
        if str(run.get("status", "")).lower() in run_metrics.SUCCESS_STATES
    ]

    if not successful or not successful[0].get("dataset_file_id"):
        return {
            "pipeline_id": str(pipeline.id),
            "pipeline_name": pipeline.pipeline_name,
            "dataset_ready": False,
            "reason": (
                "This pipeline has no successful run with a dataset yet. "
                "Run it once, then come back."
            ),
            "run_id": None,
            "dataset_file_id": None,
            "rows": None,
            "columns": None,
            "target_candidates": [],
            "warnings": [],
        }

    latest = successful[0]
    file_id = latest["dataset_file_id"]

    try:
        import pandas as pd  # noqa: PLC0415 - deferred, keeps import cost off startup

        # Imported from the service, not from main: main connects to the
        # database at import time, and a read-only metrics call must not
        # depend on application startup.
        from services.training_readiness import (  # noqa: PLC0415
            assess_training_readiness,
        )

        path = UPLOAD_DIR / str(file_id)
        if not path.exists():
            raise FileNotFoundError(str(path))
        frame = pd.read_csv(path)
    except FileNotFoundError:
        return {
            "pipeline_id": str(pipeline.id),
            "pipeline_name": pipeline.pipeline_name,
            "dataset_ready": False,
            "reason": (
                "The dataset this run produced is no longer on disk. "
                "Re-run the pipeline to regenerate it."
            ),
            "run_id": latest["id"],
            "dataset_file_id": file_id,
            "rows": latest.get("row_count"),
            "columns": None,
            "target_candidates": [],
            "warnings": [],
        }
    except Exception as exc:  # noqa: BLE001 - never 500 the readiness check
        logger.exception("Failed to assess AutoML readiness")
        return {
            "pipeline_id": str(pipeline.id),
            "pipeline_name": pipeline.pipeline_name,
            "dataset_ready": False,
            "reason": f"Could not read the dataset: {exc}",
            "run_id": latest["id"],
            "dataset_file_id": file_id,
            "rows": latest.get("row_count"),
            "columns": None,
            "target_candidates": [],
            "warnings": [],
        }

    readiness = assess_training_readiness(frame)

    # Target candidates, ranked. A low-cardinality non-key column is the
    # classic label; a numeric column with spread is a regression target.
    candidates: List[Dict[str, Any]] = []
    row_total = max(1, len(frame))
    for column in frame.columns:
        series = frame[column]
        distinct = int(series.nunique(dropna=True))
        null_share = round(100.0 * float(series.isna().mean()), 2)
        if distinct <= 1:
            continue
        uniqueness = distinct / row_total
        if uniqueness > 0.95:
            continue  # an identifier, not a label
        numeric = bool(getattr(series.dtype, "kind", "") in "ifc")
        if 2 <= distinct <= 20:
            task, score = ("classification", 0.9 if distinct <= 10 else 0.75)
        elif numeric:
            task, score = ("regression", 0.6)
        else:
            continue
        candidates.append(
            {
                "column": str(column),
                "task_type": task,
                "distinct_values": distinct,
                "null_percentage": null_share,
                "score": round(score * (1 - min(null_share, 100) / 200), 3),
            }
        )
    candidates.sort(key=lambda item: item["score"], reverse=True)

    # Quality score: the share of cells that are populated. Honest and
    # explainable, not a composite nobody can reproduce.
    completeness = round(
        100.0 * float(1 - frame.isna().to_numpy().mean()), 2
    ) if len(frame.columns) else 0.0

    return {
        "pipeline_id": str(pipeline.id),
        "pipeline_name": pipeline.pipeline_name,
        "dataset_ready": bool(readiness["ready"]),
        "reason": readiness.get("reason"),
        "run_id": latest["id"],
        "dataset_file_id": file_id,
        "project_id": latest.get("project_id"),
        "rows": readiness["n_rows"],
        "columns": readiness["n_columns"],
        "cv_folds": readiness["n_folds"],
        "quality_score": completeness,
        "quality_basis": "Percentage of populated cells across the dataset.",
        "target_candidates": candidates[:8],
        "warnings": readiness.get("warnings") or [],
    }
