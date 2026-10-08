import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from database import SessionLocal
from models.pipeline_definition import PipelineDefinition
from models.pipeline_run import PipelineRun
from models.pipeline_version import PipelineVersion
from models.project import Project
from models.cleaning_rule import CleaningRule
from services.data_cleaner import CleaningValidationError, validate_cleaning_steps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["pipelines"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class PipelineSaveRequest(BaseModel):
    project_id: UUID
    pipeline_name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    pipeline_json: Dict[str, Any]
    initial_run_id: Optional[UUID] = None

    @validator("pipeline_name")
    @classmethod
    def validate_pipeline_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("pipeline_name must not be blank.")
        return cleaned


class PipelineUpdateRequest(BaseModel):
    pipeline_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    pipeline_json: Optional[Dict[str, Any]] = None

    @validator("pipeline_name")
    @classmethod
    def validate_pipeline_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            raise ValueError("pipeline_name must not be null.")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("pipeline_name must not be blank.")
        return cleaned


class PipelineVersionResponse(BaseModel):
    id: UUID
    pipeline_definition_id: UUID
    version_number: int
    pipeline_json: Dict[str, Any]
    created_at: datetime

    class Config:
        orm_mode = True


class PipelineResponse(BaseModel):
    id: UUID
    project_id: UUID
    pipeline_name: str
    description: Optional[str]
    status: str
    latest_run_status: Optional[str]
    created_at: datetime
    updated_at: datetime
    versions: List[PipelineVersionResponse]

    class Config:
        orm_mode = True


class PipelineRunResponse(BaseModel):
    id: UUID
    pipeline_version_id: Optional[UUID]
    project_id: UUID
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    execution_log: Optional[str]
    dataset_file_id: Optional[str]
    row_count: Optional[int]
    execution_time: Optional[float]

    class Config:
        orm_mode = True


class PipelineRunRequest(BaseModel):
    db_type: str
    host: str
    port: int
    database: str
    username: Optional[str] = None
    password: Optional[str] = None
    trusted_connection: bool = False
    odbc_driver: Optional[str] = None
    schema_name: Optional[str] = None
    target_column: Optional[str] = None


class CleaningStepsUpdateRequest(BaseModel):
    steps: List[Dict[str, Any]]


def _require_project(db: Session, project_id: UUID) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def _require_pipeline(db: Session, pipeline_id: UUID) -> PipelineDefinition:
    pipeline = (
        db.query(PipelineDefinition)
        .options(joinedload(PipelineDefinition.versions))
        .filter(PipelineDefinition.id == pipeline_id)
        .first()
    )
    if pipeline is None or pipeline.status == "deleted":
        raise HTTPException(status_code=404, detail="Pipeline not found.")
    return pipeline


def _validate_pipeline_json(value: Dict[str, Any]) -> None:
    forbidden_names = {
        "password",
        "passwd",
        "username",
        "credentials",
        "connectionstring",
        "connectionurl",
        "databaseurl",
        "apikey",
        "accesstoken",
        "token",
        "secret",
        "secretkey",
        "dsn",
    }

    def inspect_value(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                normalized_key = "".join(
                    character
                    for character in str(key).lower()
                    if character.isalnum()
                )
                if normalized_key in forbidden_names or normalized_key.endswith(
                    ("password", "passwd", "secret", "apikey", "accesstoken")
                ):
                    raise HTTPException(
                        status_code=422,
                        detail="Pipeline configuration must not contain credentials.",
                    )
                inspect_value(nested)
        elif isinstance(item, list):
            for nested in item:
                inspect_value(nested)

    inspect_value(value)


def _next_version_number(db: Session, pipeline: PipelineDefinition) -> int:
    current = (
        db.query(PipelineVersion)
        .filter(PipelineVersion.pipeline_definition_id == pipeline.id)
        .order_by(PipelineVersion.version_number.desc())
        .first()
    )
    return (current.version_number if current else 0) + 1


def _link_initial_run(
    db: Session,
    pipeline: PipelineDefinition,
    version: PipelineVersion,
    run_id: Optional[UUID],
) -> None:
    if run_id is None:
        return
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Initial pipeline run not found.")
    if run.project_id != pipeline.project_id or run.pipeline_version_id is not None:
        raise HTTPException(
            status_code=409,
            detail="Initial run does not belong to this project or is already linked.",
        )
    run.pipeline_version = version


def _write_cleaning_rules(
    db: Session,
    version: PipelineVersion,
    steps: List[Dict[str, Any]],
) -> None:
    normalized = validate_cleaning_steps(steps)
    version.cleaning_rules = [
        CleaningRule(
            step_name=step["step_name"],
            rule_type=step["step_type"],
            rule_config=step["configuration"],
            step_order=step["step_order"],
            enabled=step["enabled"],
            created_by=None,
        )
        for step in normalized
    ]


def _cleaning_steps_for_version(version: PipelineVersion) -> List[Dict[str, Any]]:
    if version.cleaning_rules:
        return [
            {
                "id": str(rule.id),
                "step_name": rule.step_name,
                "step_type": rule.rule_type,
                "configuration": rule.rule_config,
                "step_order": rule.step_order,
                "enabled": rule.enabled,
                "created_by": rule.created_by,
                "created_at": rule.created_at.isoformat(),
                "updated_at": rule.updated_at.isoformat(),
            }
            for rule in sorted(version.cleaning_rules, key=lambda item: item.step_order)
        ]
    return version.pipeline_json.get("cleaning_steps", [])


@router.post("/save", response_model=PipelineResponse, status_code=201)
def save_pipeline(
    request: PipelineSaveRequest,
    db: Session = Depends(get_db),
) -> PipelineDefinition:
    """Create a pipeline and its initial version after its project's build."""
    _validate_pipeline_json(request.pipeline_json)
    try:
        cleaning_steps = validate_cleaning_steps(
            request.pipeline_json.get("cleaning_steps", [])
        )
    except CleaningValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        project = _require_project(db, request.project_id)
        pipeline = PipelineDefinition(
            project=project,
            pipeline_name=request.pipeline_name,
            description=request.description,
        )
        version = PipelineVersion(
            version_number=1,
            pipeline_json=request.pipeline_json,
        )
        pipeline.versions.append(version)
        db.add(pipeline)
        db.flush()
        _write_cleaning_rules(db, version, cleaning_steps)
        _link_initial_run(db, pipeline, version, request.initial_run_id)
        db.commit()
        db.refresh(pipeline)
        return pipeline
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        logger.exception("Pipeline version uniqueness conflict while saving")
        raise HTTPException(status_code=409, detail="Pipeline could not be saved.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to save pipeline")
        raise HTTPException(status_code=500, detail="Failed to save pipeline.") from exc


@router.get("/{project_id}", response_model=List[PipelineResponse])
def list_pipelines(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> List[PipelineDefinition]:
    try:
        _require_project(db, project_id)
        return (
            db.query(PipelineDefinition)
            .options(joinedload(PipelineDefinition.versions))
            .filter(
                PipelineDefinition.project_id == project_id,
                PipelineDefinition.status != "deleted",
            )
            .order_by(PipelineDefinition.created_at.desc())
            .all()
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Failed to list project pipelines")
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve project pipelines.",
        ) from exc


@router.get("/detail/{pipeline_id}", response_model=PipelineResponse)
def get_pipeline(pipeline_id: UUID, db: Session = Depends(get_db)):
    try:
        return _require_pipeline(db, pipeline_id)
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Failed to retrieve pipeline")
        raise HTTPException(status_code=500, detail="Failed to retrieve pipeline.") from exc


@router.put("/{pipeline_id}", response_model=PipelineResponse)
def update_pipeline(
    pipeline_id: UUID,
    request: PipelineUpdateRequest,
    db: Session = Depends(get_db),
):
    changes = request.dict(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="At least one field is required.")
    if "pipeline_json" in changes:
        if changes["pipeline_json"] is None:
            raise HTTPException(status_code=422, detail="pipeline_json must not be null.")
        _validate_pipeline_json(changes["pipeline_json"])
        try:
            validate_cleaning_steps(changes["pipeline_json"].get("cleaning_steps", []))
        except CleaningValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        pipeline = _require_pipeline(db, pipeline_id)
        if "pipeline_name" in changes:
            pipeline.pipeline_name = changes["pipeline_name"]
        if "description" in changes:
            pipeline.description = changes["description"]
        if "pipeline_json" in changes:
            latest = max(pipeline.versions, key=lambda item: item.version_number)
            if changes["pipeline_json"] != latest.pipeline_json:
                version = PipelineVersion(
                    version_number=_next_version_number(db, pipeline),
                    pipeline_json=changes["pipeline_json"],
                )
                pipeline.versions.append(version)
                db.flush()
                _write_cleaning_rules(
                    db,
                    version,
                    changes["pipeline_json"].get("cleaning_steps", []),
                )
        pipeline.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(pipeline)
        return pipeline
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        logger.exception("Pipeline version uniqueness conflict while updating")
        raise HTTPException(status_code=409, detail="Pipeline update conflicted.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to update pipeline")
        raise HTTPException(status_code=500, detail="Failed to update pipeline.") from exc


@router.delete("/{pipeline_id}", status_code=204)
def delete_pipeline(pipeline_id: UUID, db: Session = Depends(get_db)):
    try:
        pipeline = _require_pipeline(db, pipeline_id)
        pipeline.status = "deleted"
        pipeline.updated_at = datetime.utcnow()
        db.commit()
        return None
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to delete pipeline")
        raise HTTPException(status_code=500, detail="Failed to delete pipeline.") from exc


@router.post("/{pipeline_id}/clone", response_model=PipelineResponse, status_code=201)
def clone_pipeline(pipeline_id: UUID, db: Session = Depends(get_db)):
    try:
        original = _require_pipeline(db, pipeline_id)
        if not original.versions:
            raise HTTPException(status_code=409, detail="Pipeline has no saved version.")
        latest = max(original.versions, key=lambda item: item.version_number)
        clone = PipelineDefinition(
            project_id=original.project_id,
            pipeline_name=f"{original.pipeline_name} (Copy)",
            description=original.description,
        )
        clone.versions.append(
            PipelineVersion(version_number=1, pipeline_json=latest.pipeline_json)
        )
        db.add(clone)
        db.flush()
        _write_cleaning_rules(
            db,
            clone.versions[0],
            _cleaning_steps_for_version(latest),
        )
        db.commit()
        db.refresh(clone)
        return clone
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to clone pipeline")
        raise HTTPException(status_code=500, detail="Failed to clone pipeline.") from exc


@router.get("/version/{pipeline_id}", response_model=List[PipelineVersionResponse])
def list_pipeline_versions(pipeline_id: UUID, db: Session = Depends(get_db)):
    try:
        _require_pipeline(db, pipeline_id)
        return (
            db.query(PipelineVersion)
            .filter(PipelineVersion.pipeline_definition_id == pipeline_id)
            .order_by(PipelineVersion.version_number.asc())
            .all()
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Failed to retrieve pipeline version history")
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve pipeline version history.",
        ) from exc


@router.get("/version/{pipeline_id}/compare/{from_version}/{to_version}")
def compare_pipeline_versions(
    pipeline_id: UUID,
    from_version: int,
    to_version: int,
    db: Session = Depends(get_db),
):
    try:
        _require_pipeline(db, pipeline_id)
        versions = (
            db.query(PipelineVersion)
            .filter(
                PipelineVersion.pipeline_definition_id == pipeline_id,
                PipelineVersion.version_number.in_([from_version, to_version]),
            )
            .all()
        )
        by_number = {version.version_number: version for version in versions}
        if from_version not in by_number or to_version not in by_number:
            raise HTTPException(status_code=404, detail="Pipeline version not found.")
        before = by_number[from_version].pipeline_json
        after = by_number[to_version].pipeline_json
        changed_keys = sorted(
            key
            for key in set(before) | set(after)
            if before.get(key) != after.get(key)
        )
        return {
            "pipeline_id": pipeline_id,
            "from_version": from_version,
            "to_version": to_version,
            "changes": {
                key: {"before": before.get(key), "after": after.get(key)}
                for key in changed_keys
            },
        }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Failed to compare pipeline versions")
        raise HTTPException(status_code=500, detail="Failed to compare versions.") from exc


@router.post("/{pipeline_id}/restore/{version_id}", response_model=PipelineResponse)
def restore_pipeline_version(
    pipeline_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
):
    try:
        pipeline = _require_pipeline(db, pipeline_id)
        source = (
            db.query(PipelineVersion)
            .filter(
                PipelineVersion.id == version_id,
                PipelineVersion.pipeline_definition_id == pipeline_id,
            )
            .first()
        )
        if source is None:
            raise HTTPException(status_code=404, detail="Pipeline version not found.")
        restored = PipelineVersion(
            version_number=_next_version_number(db, pipeline),
            pipeline_json=source.pipeline_json,
        )
        pipeline.versions.append(restored)
        db.flush()
        _write_cleaning_rules(
            db,
            restored,
            _cleaning_steps_for_version(source),
        )
        pipeline.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(pipeline)
        return pipeline
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        logger.exception("Pipeline version uniqueness conflict while restoring")
        raise HTTPException(status_code=409, detail="Pipeline restore conflicted.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to restore pipeline version")
        raise HTTPException(status_code=500, detail="Failed to restore pipeline.") from exc


@router.get(
    "/run-history/{project_id}",
    response_model=List[PipelineRunResponse],
)
def list_pipeline_run_history(project_id: UUID, db: Session = Depends(get_db)):
    try:
        _require_project(db, project_id)
        return (
            db.query(PipelineRun)
            .filter(PipelineRun.project_id == project_id)
            .order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
            .all()
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Failed to retrieve pipeline run history")
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve pipeline run history.",
        ) from exc


@router.get("/version/{version_id}/cleaning-steps")
def list_cleaning_steps(version_id: UUID, db: Session = Depends(get_db)):
    version = (
        db.query(PipelineVersion)
        .options(joinedload(PipelineVersion.cleaning_rules))
        .filter(PipelineVersion.id == version_id)
        .first()
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Pipeline version not found.")
    return _cleaning_steps_for_version(version)


@router.put("/{pipeline_id}/cleaning-steps", response_model=PipelineResponse)
def save_cleaning_steps(
    pipeline_id: UUID,
    request: CleaningStepsUpdateRequest,
    db: Session = Depends(get_db),
):
    try:
        pipeline = _require_pipeline(db, pipeline_id)
        try:
            steps = validate_cleaning_steps(request.steps)
        except CleaningValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        current = max(pipeline.versions, key=lambda item: item.version_number)
        current_steps = _cleaning_steps_for_version(current)
        canonical_current = [
            {
                "step_name": step["step_name"],
                "step_type": step["step_type"],
                "configuration": step["configuration"],
                "enabled": step["enabled"],
            }
            for step in current_steps
        ]
        canonical_new = [
            {
                "step_name": step["step_name"],
                "step_type": step["step_type"],
                "configuration": step["configuration"],
                "enabled": step["enabled"],
            }
            for step in steps
        ]
        if canonical_current == canonical_new:
            return pipeline
        pipeline_json = dict(current.pipeline_json)
        pipeline_json["cleaning_steps"] = canonical_new
        new_version = PipelineVersion(
            version_number=_next_version_number(db, pipeline),
            pipeline_json=pipeline_json,
        )
        pipeline.versions.append(new_version)
        pipeline.updated_at = datetime.utcnow()
        db.flush()
        _write_cleaning_rules(db, new_version, steps)
        db.commit()
        db.refresh(pipeline)
        return pipeline
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        logger.exception("Cleaning version number conflict")
        raise HTTPException(status_code=409, detail="Cleaning version conflicted.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to save cleaning steps")
        raise HTTPException(status_code=500, detail="Failed to save cleaning steps.") from exc


@router.post("/{pipeline_id}/run", status_code=202)
def run_pipeline(
    pipeline_id: UUID,
    request: PipelineRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Re-run the latest immutable version with request-only DB credentials."""
    try:
        pipeline = _require_pipeline(db, pipeline_id)
        if not pipeline.versions:
            raise HTTPException(status_code=409, detail="Pipeline has no saved version.")
        latest = max(pipeline.versions, key=lambda item: item.version_number)
        configuration = latest.pipeline_json
        user_request = configuration.get("user_request")
        if not isinstance(user_request, str) or not user_request.strip():
            raise HTTPException(
                status_code=422,
                detail="Saved pipeline version does not contain a dataset request.",
            )
        project = _require_project(db, pipeline.project_id)
        from routers.etl_copilot import EtlCopilotBuildRequest, _run_build_job

        build_request = EtlCopilotBuildRequest(
            db_type=request.db_type,
            host=request.host,
            port=request.port,
            database=request.database,
            username=request.username,
            password=request.password,
            trusted_connection=request.trusted_connection,
            odbc_driver=request.odbc_driver,
            schema_name=request.schema_name,
            project_name=f"{project.project_name}_rerun",
            user_request=user_request,
            target_column=request.target_column or project.target_column,
            background=True,
            pipeline_version_id=latest.id,
            cleaning_steps=_cleaning_steps_for_version(latest),
        )
        job_id = str(uuid4())
        from routers.etl_copilot import _set_job

        _set_job(job_id, status="pending", stage="queued", progress=0)
        background_tasks.add_task(_run_build_job, job_id, build_request)
        return {
            "job_id": job_id,
            "status": "started",
            "poll": f"/etl-copilot/status/{job_id}",
            "pipeline_id": pipeline.id,
            "pipeline_version_id": latest.id,
        }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Failed to queue pipeline run")
        raise HTTPException(status_code=500, detail="Failed to start pipeline run.") from exc
