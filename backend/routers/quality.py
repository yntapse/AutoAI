import logging
from datetime import datetime
from typing import Any, Dict, List
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import SessionLocal
from models.pipeline_version import PipelineVersion
from models.quality_rule import QualityRule
from models.source_profile_snapshot import SourceProfileSnapshot
from services.quality_rules import (
    ProfileTargetNotFound,
    QualityRuleValidationError,
    evaluate_rules,
    validate_rule_definition,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["quality-rules"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class QualityRuleCreateRequest(BaseModel):
    rule_name: str = Field(..., min_length=1, max_length=255)
    rule_type: str
    severity: str = "error"
    enabled: bool = True
    rule_config: Dict[str, Any]

    @validator("rule_name")
    @classmethod
    def validate_rule_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("rule_name must not be blank.")
        return value

    @validator("rule_type")
    @classmethod
    def validate_rule_type(cls, value: str) -> str:
        return value.strip().lower()

    @validator("severity")
    @classmethod
    def validate_severity(cls, value: str) -> str:
        return value.strip().lower()

    @validator("rule_config")
    @classmethod
    def validate_rule_config(cls, value: Dict[str, Any], values: Dict[str, Any]):
        if "rule_type" in values and "severity" in values:
            validate_rule_definition(values["rule_type"], values["severity"], value)
        return value


class QualityRuleResponse(BaseModel):
    id: UUID
    pipeline_version_id: UUID
    rule_name: str
    rule_type: str
    severity: str
    enabled: bool
    rule_config: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


class QualityEvaluationRequest(BaseModel):
    pipeline_version_id: UUID
    snapshot_id: UUID


class QualityEvaluationResponse(BaseModel):
    evaluation_id: UUID
    overall_status: str
    summary: Dict[str, int]
    results: List[Dict[str, Any]]


@router.post(
    "/pipeline/version/{version_id}/quality-rules",
    response_model=QualityRuleResponse,
    status_code=201,
)
def create_quality_rule(
    version_id: UUID,
    request: QualityRuleCreateRequest,
    db: Session = Depends(get_db),
) -> QualityRule:
    try:
        version = (
            db.query(PipelineVersion)
            .filter(PipelineVersion.id == version_id)
            .first()
        )
        if version is None:
            raise HTTPException(status_code=404, detail="Pipeline version not found.")
        validate_rule_definition(
            request.rule_type,
            request.severity,
            request.rule_config,
        )
        rule = QualityRule(
            pipeline_version_id=version_id,
            rule_name=request.rule_name,
            rule_type=request.rule_type,
            severity=request.severity,
            enabled=request.enabled,
            rule_config=request.rule_config,
        )
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return rule
    except HTTPException:
        db.rollback()
        raise
    except QualityRuleValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to create quality rule")
        raise HTTPException(status_code=500, detail="Failed to create quality rule.") from exc


@router.get(
    "/pipeline/version/{version_id}/quality-rules",
    response_model=List[QualityRuleResponse],
)
def list_quality_rules(
    version_id: UUID,
    db: Session = Depends(get_db),
) -> List[QualityRule]:
    try:
        if db.query(PipelineVersion.id).filter(PipelineVersion.id == version_id).first() is None:
            raise HTTPException(status_code=404, detail="Pipeline version not found.")
        return (
            db.query(QualityRule)
            .filter(QualityRule.pipeline_version_id == version_id)
            .order_by(QualityRule.created_at.asc(), QualityRule.id.asc())
            .all()
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Failed to list quality rules")
        raise HTTPException(status_code=500, detail="Failed to retrieve quality rules.") from exc


@router.get(
    "/pipeline/quality-rules/{rule_id}",
    response_model=QualityRuleResponse,
)
def get_quality_rule(
    rule_id: UUID,
    db: Session = Depends(get_db),
) -> QualityRule:
    try:
        rule = db.query(QualityRule).filter(QualityRule.id == rule_id).first()
        if rule is None:
            raise HTTPException(status_code=404, detail="Quality rule not found.")
        return rule
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Failed to retrieve quality rule")
        raise HTTPException(status_code=500, detail="Failed to retrieve quality rule.") from exc


@router.post("/quality/evaluate", response_model=QualityEvaluationResponse)
def evaluate_quality(
    request: QualityEvaluationRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    try:
        version = (
            db.query(PipelineVersion)
            .filter(PipelineVersion.id == request.pipeline_version_id)
            .first()
        )
        if version is None:
            raise HTTPException(status_code=404, detail="Pipeline version not found.")
        snapshot = (
            db.query(SourceProfileSnapshot)
            .filter(SourceProfileSnapshot.id == request.snapshot_id)
            .first()
        )
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Source profile snapshot not found.")

        rules = (
            db.query(QualityRule)
            .filter(
                QualityRule.pipeline_version_id == request.pipeline_version_id,
                QualityRule.enabled.is_(True),
            )
            .order_by(QualityRule.created_at.asc(), QualityRule.id.asc())
            .all()
        )
        report = evaluate_rules(rules, snapshot.profile_json)
        return {"evaluation_id": uuid4(), **report}
    except HTTPException:
        raise
    except ProfileTargetNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QualityRuleValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.exception("Failed to evaluate source profile quality rules")
        raise HTTPException(status_code=500, detail="Failed to evaluate quality rules.") from exc
