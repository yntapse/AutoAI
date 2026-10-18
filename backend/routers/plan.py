"""AI Pipeline Builder - requirement in, reviewable plan out.

Flow:
  POST /plan                        requirement + source -> recommendations
  GET  /plan/{id}                   recommendations grouped by kind
  POST /plan/{id}/recommendations/{rec_id}/decide   accept | reject | edit
  GET  /plan/{id}/canvas            nodes + edges for the visual canvas
  GET  /plan/{id}/preview           estimated impact before anything runs
  POST /plan/{id}/compile           accepted recommendations -> PipelineVersion

Compile is the hand-off to the existing deterministic machinery: it writes a
PipelineDefinition + PipelineVersion, which routers/pipelines.py already knows
how to run. Execution never re-plans and never calls a model.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import SessionLocal
from models.catalog_column import CatalogColumn
from models.catalog_relationship import CatalogRelationship
from models.catalog_scan import CatalogScan
from models.catalog_table import CatalogTable
from models.data_source import DataSource
from models.pipeline_definition import PipelineDefinition
from models.pipeline_plan import PipelinePlan
from models.pipeline_version import PipelineVersion
from models.plan_recommendation import PlanRecommendation
from models.project import Project
from services.agents.planner_agent import parse_requirement
from services.pipeline_execution import (
    PipelineExecutionError,
    build_execution_graph,
    validate_derived_columns,
)
from services.plan_builder import SOURCE_SEPARATOR, bare_table_name, build_plan

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plan", tags=["ai-pipeline-builder"])

_DECISIONS = {"accepted", "rejected", "edited"}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------


class PlanCreateRequest(BaseModel):
    data_source_id: UUID
    # Optional extra sources for multi-source discovery. When given, every
    # table is qualified as "<source_name>/<table>" so names cannot collide.
    data_source_ids: Optional[List[UUID]] = None
    requirement: str = Field(..., min_length=5, max_length=2000)
    use_model: bool = Field(
        True,
        description="False forces the heuristic planner - used by tests and offline demos.",
    )

    @validator("requirement")
    @classmethod
    def validate_requirement(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("requirement must not be blank.")
        return cleaned


class RecommendationResponse(BaseModel):
    id: UUID
    kind: str
    ref_key: str
    title: str
    payload: Dict[str, Any]
    reason: str
    alternative_reason: Optional[str]
    confidence: float
    expected_impact: Optional[str]
    risk_level: str
    origin: str
    decision: str
    user_payload: Optional[Dict[str, Any]]
    sort_order: int

    class Config:
        orm_mode = True


class PlanResponse(BaseModel):
    id: UUID
    data_source_id: UUID
    requirement_text: str
    schema_name: Optional[str]
    status: str
    intent: Dict[str, Any]
    intent_origin: str
    recommendation_count: int
    accepted_count: int
    pipeline_definition_id: Optional[UUID]
    created_at: datetime
    recommendations: Dict[str, List[RecommendationResponse]] = Field(
        default_factory=dict
    )


class DecisionRequest(BaseModel):
    decision: str
    user_payload: Optional[Dict[str, Any]] = None
    decided_by: Optional[str] = None

    @validator("decision")
    @classmethod
    def validate_decision(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in _DECISIONS:
            raise ValueError("decision must be 'accepted', 'rejected' or 'edited'.")
        return normalized


class CompileRequest(BaseModel):
    pipeline_name: str = Field(..., min_length=1, max_length=160)
    # pipeline_definitions.project_id is NOT NULL in the existing schema, so a
    # compiled pipeline must belong to a project. Required, not optional.
    project_id: UUID
    description: Optional[str] = None
    # When set, the compiled definition is appended to this pipeline as
    # version N+1 instead of creating a new pipeline. This is what makes
    # version comparison meaningful.
    pipeline_definition_id: Optional[UUID] = None


class _RecView:
    """Read-only view of a recommendation with a rewritten payload.

    Used by multi-source compile to strip the '<source>/' qualifier without
    mutating the stored recommendation.
    """

    def __init__(self, rec: "PlanRecommendation", payload: Any):
        self.id = rec.id
        self.kind = rec.kind
        self.ref_key = rec.ref_key
        self.decision = rec.decision
        self.origin = rec.origin
        self.confidence = rec.confidence
        self.decided_by = rec.decided_by
        self.effective_payload = payload


def _strip_qualifier(value: Any, prefix: str) -> Any:
    if isinstance(value, str):
        return value.replace(prefix, "")
    if isinstance(value, list):
        return [_strip_qualifier(item, prefix) for item in value]
    if isinstance(value, dict):
        return {key: _strip_qualifier(item, prefix) for key, item in value.items()}
    return value


def _is_kept_as_is(rec: Any) -> bool:
    """A transformation whose approved action is 'keep' compiles to nothing."""
    payload = rec.effective_payload or {}
    config = payload.get("configuration") or {}
    return rec.kind == "transformation" and (
        payload.get("action") == "keep" or config.get("strategy") == "keep"
    )


def _referenced_columns(accepted: List[Any]) -> set:
    """(table, column) pairs a compiled pipeline depends on - never dropped."""
    refs = set()
    for rec in accepted:
        p = rec.effective_payload or {}
        if rec.kind in ("join", "relationship"):
            refs.add((p.get("left_table"), p.get("left_column")))
            refs.add((p.get("right_table"), p.get("right_column")))
        elif rec.kind == "filter":
            refs.add((p.get("source_table"), p.get("column")))
        elif rec.kind == "aggregation":
            refs.add((p.get("source_table"), p.get("source_column")))
            for g in p.get("group_by") or []:
                refs.add((g.get("source_table"), g.get("column")))
        elif rec.kind == "transformation":
            cfg = p.get("configuration") or {}
            refs.add((p.get("table_name"), cfg.get("column_name")))
            for c in cfg.get("columns") or []:
                refs.add((p.get("table_name"), c))
        elif rec.kind == "quality_rule":
            cfg = p.get("rule_config") or {}
            refs.add((cfg.get("table_name"), cfg.get("column_name")))
        elif rec.kind == "derived_column":
            expr = str(p.get("expression") or p.get("expression_hint") or "")
            refs.add(("__expr__", expr))
    return refs


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _require_plan(db: Session, plan_id: UUID) -> PipelinePlan:
    plan = db.query(PipelinePlan).filter(PipelinePlan.id == plan_id).first()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")
    return plan


def _load_catalog(db: Session, source_id: UUID) -> Dict[str, Any]:
    """Read the latest completed scan into the shape plan_builder expects."""
    scan = (
        db.query(CatalogScan)
        .filter(
            CatalogScan.data_source_id == source_id,
            CatalogScan.status == "completed",
        )
        .order_by(CatalogScan.created_at.desc())
        .first()
    )
    if scan is None:
        raise HTTPException(
            status_code=409,
            detail="This source has no completed metadata scan. Scan it first.",
        )

    tables = (
        db.query(CatalogTable).filter(CatalogTable.scan_id == scan.id).all()
    )
    table_rows = [
        {
            "table_name": table.table_name,
            "row_count": table.row_count,
            "column_count": table.column_count,
            "has_primary_key": table.has_primary_key,
        }
        for table in tables
    ]

    columns_by_table: Dict[str, List[Dict[str, Any]]] = {}
    for table in tables:
        columns = (
            db.query(CatalogColumn)
            .filter(CatalogColumn.catalog_table_id == table.id)
            .order_by(CatalogColumn.ordinal_position.asc())
            .all()
        )
        columns_by_table[table.table_name] = [
            {
                "column_name": column.column_name,
                "data_type": column.data_type,
                "is_nullable": column.is_nullable,
                "is_primary_key": column.is_primary_key,
                "is_foreign_key": column.is_foreign_key,
                "normalized_name": column.normalized_name,
            }
            for column in columns
        ]

    relationships = [
        {
            "left_table": edge.left_table,
            "left_column": edge.left_column,
            "right_table": edge.right_table,
            "right_column": edge.right_column,
            "constraint_name": edge.constraint_name,
            "origin": edge.origin,
            "confidence": edge.confidence,
        }
        for edge in db.query(CatalogRelationship)
        .filter(CatalogRelationship.scan_id == scan.id)
        .all()
    ]

    return {
        "scan": scan,
        "tables": table_rows,
        "columns_by_table": columns_by_table,
        "relationships": relationships,
    }


def _load_multi_catalog(db: Session, sources: List[DataSource]) -> Dict[str, Any]:
    """Merge several sources' latest scans; qualify every table by source."""
    merged: Dict[str, Any] = {
        "tables": [],
        "columns_by_table": {},
        "relationships": [],
        "sources": {},
    }
    for source in sources:
        catalog = _load_catalog(db, source.id)
        prefix = f"{source.source_name}{SOURCE_SEPARATOR}"
        merged["sources"][source.source_name] = {
            "data_source_id": str(source.id),
            "scan_id": str(catalog["scan"].id),
            "schema_name": catalog["scan"].schema_name,
            "db_type": source.db_type,
            "owner": source.created_by,
        }
        merged.setdefault("scan", catalog["scan"])
        for table in catalog["tables"]:
            merged["tables"].append(
                dict(table, table_name=prefix + table["table_name"])
            )
        for name, columns in catalog["columns_by_table"].items():
            merged["columns_by_table"][prefix + name] = columns
        for edge in catalog["relationships"]:
            merged["relationships"].append(
                dict(
                    edge,
                    left_table=prefix + edge["left_table"],
                    right_table=prefix + edge["right_table"],
                )
            )
    return merged


def _persist_recommendations(
    db: Session, plan: PipelinePlan, result: Dict[str, Any]
) -> int:
    order = 0
    rows: List[PlanRecommendation] = []

    for item in result["tables"]:
        order += 1
        rows.append(
            PlanRecommendation(
                plan_id=plan.id,
                kind="table",
                ref_key=f"table:{item['table_name']}",
                title=f"{item['classification'].title()}: {item['table_name']}",
                payload=item,
                reason=item["reason"],
                confidence=item["confidence"],
                expected_impact=(
                    f"{item.get('row_count') or 'unknown'} rows, "
                    f"{item.get('column_count') or 0} columns"
                ),
                risk_level="low",
                origin="deterministic",
                decision="accepted" if item["classification"] == "required" else "pending",
                sort_order=order,
            )
        )

    for item in result["relationships"]:
        order += 1
        rows.append(
            PlanRecommendation(
                plan_id=plan.id,
                kind="relationship",
                ref_key=(
                    f"rel:{item['left_table']}.{item['left_column']}"
                    f"->{item['right_table']}.{item['right_column']}"
                ),
                title=(
                    f"{item['left_table']}.{item['left_column']} -> "
                    f"{item['right_table']}.{item['right_column']}"
                ),
                payload=item,
                reason=item["reason"],
                confidence=item["confidence"],
                risk_level="low" if item["origin"] == "declared" else "medium",
                origin=item["origin"],
                sort_order=order,
            )
        )

    for item in result["joins"]:
        order += 1
        rows.append(
            PlanRecommendation(
                plan_id=plan.id,
                kind="join",
                ref_key=(
                    f"join:{item['left_table']}.{item['left_column']}"
                    f"->{item['right_table']}.{item['right_column']}"
                ),
                title=(
                    f"{item['join_type'].upper()} JOIN {item['right_table']} "
                    f"({item['cardinality']})"
                ),
                payload=item,
                reason=item["reason"],
                alternative_reason=item["alternative_reason"],
                confidence=item["confidence"],
                expected_impact=(
                    f"Cardinality {item['cardinality']}, row explosion risk "
                    f"{item['explosion_risk']}"
                    + (
                        ". Pre-aggregation required."
                        if item["requires_pre_aggregation"]
                        else "."
                    )
                ),
                risk_level=(
                    "high" if item["explosion_risk"] == "high"
                    else "medium" if item["explosion_risk"] == "medium"
                    else "low"
                ),
                origin=item["origin"],
                sort_order=order,
            )
        )

    for item in result["transformations"]:
        order += 1
        rows.append(
            PlanRecommendation(
                plan_id=plan.id,
                kind="transformation",
                ref_key=(
                    f"tf:{item['step_type']}:{item['table_name']}:"
                    f"{item['configuration'].get('column_name') or 'table'}"
                ),
                title=f"{item['step_type']} on {item['table_name']}",
                payload=item,
                reason=item["reason"],
                confidence=item["confidence"],
                expected_impact=item.get("expected_impact"),
                risk_level=item.get("risk_level", "medium"),
                origin="deterministic",
                sort_order=order,
            )
        )

    for index, item in enumerate(result["quality_rules"]):
        order += 1
        rows.append(
            PlanRecommendation(
                plan_id=plan.id,
                kind="quality_rule",
                # severity is part of the key: the same rule_type can legitimately
                # apply twice to one column (blocking on the key, warning on the FK).
                ref_key=(
                    f"qr:{item['rule_type']}:{item['severity']}:"
                    f"{item['rule_config'].get('table_name')}:"
                    f"{item['rule_config'].get('column_name') or index}"
                ),
                title=(
                    f"{item['rule_type']} on "
                    f"{item['rule_config'].get('table_name')}."
                    f"{item['rule_config'].get('column_name') or '*'}"
                ),
                payload=item,
                reason=item["reason"],
                confidence=item["confidence"],
                risk_level=item.get("risk_level", "medium"),
                origin="deterministic",
                sort_order=order,
            )
        )

    for item in result["derived_columns"]:
        order += 1
        rows.append(
            PlanRecommendation(
                plan_id=plan.id,
                kind="derived_column",
                ref_key=f"dc:{item['name']}",
                title=f"Derived column: {item['name']}",
                payload=item,
                reason=item["reason"],
                confidence=item["confidence"],
                risk_level=item.get("risk_level", "medium"),
                origin="ai",
                sort_order=order,
            )
        )

    for item in result.get("aggregations") or []:
        order += 1
        rows.append(
            PlanRecommendation(
                plan_id=plan.id,
                kind="aggregation",
                ref_key=(
                    f"agg:{item['function']}:{item['source_table']}."
                    f"{item['source_column']}:{item['name']}"
                ),
                title=f"{item['function']} {item['source_table']}.{item['source_column']} as {item['name']}",
                payload=item,
                reason=item["reason"],
                confidence=item["confidence"],
                risk_level=item.get("risk_level", "medium"),
                origin="ai",
                sort_order=order,
            )
        )

    for index, item in enumerate(result.get("filters") or [], 1):
        if not isinstance(item, dict):
            continue
        order += 1
        rows.append(
            PlanRecommendation(
                plan_id=plan.id,
                kind="filter",
                ref_key=f"filter:{index}:{item.get('source_table') or item.get('table_name')}."
                f"{item.get('column') or item.get('column_name')}",
                title=(
                    "Filter: %s.%s %s"
                    % (
                        item.get("source_table") or item.get("table_name") or "*",
                        item.get("column") or item.get("column_name") or "?",
                        item.get("operator") or "?",
                    )
                ),
                payload=item,
                reason=str(item.get("reason") or "Saved structured filter."),
                confidence=float(item.get("confidence", 1.0)),
                risk_level=str(item.get("risk_level", "medium")),
                origin="deterministic",
                sort_order=order,
            )
        )

    for row in rows:
        db.add(row)
    return len(rows)


def _grouped(plan: PipelinePlan) -> Dict[str, List[RecommendationResponse]]:
    grouped: Dict[str, List[RecommendationResponse]] = {}
    for rec in sorted(plan.recommendations, key=lambda r: r.sort_order):
        grouped.setdefault(rec.kind, []).append(RecommendationResponse.from_orm(rec))
    return grouped


def _plan_response(plan: PipelinePlan) -> PlanResponse:
    return PlanResponse(
        id=plan.id,
        data_source_id=plan.data_source_id,
        requirement_text=plan.requirement_text,
        schema_name=plan.schema_name,
        status=plan.status,
        intent=plan.intent or {},
        intent_origin=plan.intent_origin,
        recommendation_count=plan.recommendation_count,
        accepted_count=plan.accepted_count,
        pipeline_definition_id=plan.pipeline_definition_id,
        created_at=plan.created_at,
        recommendations=_grouped(plan),
    )


def _recount(db: Session, plan: PipelinePlan) -> None:
    accepted = sum(
        1 for rec in plan.recommendations if rec.decision in ("accepted", "edited")
    )
    plan.accepted_count = accepted
    plan.recommendation_count = len(plan.recommendations)
    db.flush()


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------


@router.post("", response_model=PlanResponse, status_code=201)
def create_plan(request: PlanCreateRequest, db: Session = Depends(get_db)):
    """Requirement + catalog -> a full set of reviewable recommendations."""
    source = (
        db.query(DataSource).filter(DataSource.id == request.data_source_id).first()
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Data source not found.")

    extra_ids = [
        sid for sid in (request.data_source_ids or []) if sid != source.id
    ]
    multi_sources = None
    if extra_ids:
        extra = db.query(DataSource).filter(DataSource.id.in_(extra_ids)).all()
        if len(extra) != len(set(extra_ids)):
            raise HTTPException(status_code=404, detail="Data source not found.")
        multi_sources = [source] + extra
        catalog = _load_multi_catalog(db, multi_sources)
    else:
        catalog = _load_catalog(db, source.id)
    table_names = [table["table_name"] for table in catalog["tables"]]

    if request.use_model:
        intent = parse_requirement(
            request.requirement,
            table_names,
            columns_by_table=catalog["columns_by_table"],
        )
    else:
        from services.agents.planner_agent import parse_requirement_heuristic

        intent = parse_requirement_heuristic(request.requirement, table_names)

    result = build_plan(catalog, intent)
    intent = dict(intent)
    intent["base_table"] = result.get("anchor_table")
    if multi_sources:
        intent["sources"] = catalog["sources"]

    plan = PipelinePlan(
        data_source_id=source.id,
        catalog_scan_id=catalog["scan"].id,
        requirement_text=request.requirement,
        schema_name=catalog["scan"].schema_name,
        status="draft",
        intent=intent,
        intent_origin=intent.get("origin", "heuristic"),
    )
    db.add(plan)
    db.flush()

    try:
        count = _persist_recommendations(db, plan, result)
        plan.recommendation_count = count
        db.flush()
        _recount(db, plan)
        db.commit()
        db.refresh(plan)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to save plan")
        raise HTTPException(status_code=500, detail="Failed to save the plan.") from exc

    return _plan_response(plan)


@router.get("/{plan_id}", response_model=PlanResponse)
def get_plan(plan_id: UUID, db: Session = Depends(get_db)):
    return _plan_response(_require_plan(db, plan_id))


@router.get("", response_model=List[Dict[str, Any]])
def list_plans(db: Session = Depends(get_db)):
    plans = (
        db.query(PipelinePlan).order_by(PipelinePlan.created_at.desc()).limit(100).all()
    )
    return [
        {
            "id": str(plan.id),
            "data_source_id": str(plan.data_source_id),
            "requirement_text": plan.requirement_text,
            "status": plan.status,
            "recommendation_count": plan.recommendation_count,
            "accepted_count": plan.accepted_count,
            "created_at": plan.created_at.isoformat(),
        }
        for plan in plans
    ]


@router.post(
    "/{plan_id}/recommendations/{rec_id}/decide",
    response_model=RecommendationResponse,
)
def decide_recommendation(
    plan_id: UUID,
    rec_id: UUID,
    request: DecisionRequest,
    db: Session = Depends(get_db),
):
    """Accept, reject or edit one recommendation. Nothing executes here."""
    plan = _require_plan(db, plan_id)
    rec = (
        db.query(PlanRecommendation)
        .filter(
            PlanRecommendation.id == rec_id,
            PlanRecommendation.plan_id == plan.id,
        )
        .first()
    )
    if rec is None:
        raise HTTPException(status_code=404, detail="Recommendation not found.")

    if request.decision == "edited" and not request.user_payload:
        raise HTTPException(
            status_code=422,
            detail="An edited recommendation must include user_payload.",
        )

    rec.decision = request.decision
    rec.user_payload = request.user_payload if request.decision == "edited" else None
    rec.decided_by = request.decided_by
    rec.decided_at = datetime.utcnow()

    try:
        _recount(db, plan)
        plan.status = "reviewed"
        db.commit()
        db.refresh(rec)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to record decision")
        raise HTTPException(status_code=500, detail="Failed to save decision.") from exc

    return RecommendationResponse.from_orm(rec)


@router.get("/{plan_id}/canvas", response_model=Dict[str, Any])
def plan_canvas(plan_id: UUID, db: Session = Depends(get_db)):
    """Nodes and edges for the visual pipeline canvas."""
    plan = _require_plan(db, plan_id)

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    sources_meta = (plan.intent or {}).get("sources") or {}
    primary = (
        db.query(DataSource).filter(DataSource.id == plan.data_source_id).first()
    )

    def _source_of(table_name: str) -> Dict[str, Any]:
        if SOURCE_SEPARATOR in table_name:
            name = table_name.split(SOURCE_SEPARATOR, 1)[0]
            meta = sources_meta.get(name, {})
            return {
                "source_name": name,
                "db_type": meta.get("db_type"),
                "schema_name": meta.get("schema_name"),
                "owner": meta.get("owner"),
                "data_source_id": meta.get("data_source_id"),
            }
        return {
            "source_name": primary.source_name if primary else None,
            "db_type": primary.db_type if primary else None,
            "schema_name": plan.schema_name,
            "owner": primary.created_by if primary else None,
            "data_source_id": str(plan.data_source_id),
        }

    for rec in plan.recommendations:
        payload = rec.effective_payload or {}
        if rec.kind == "table" and payload.get("classification") in (
            "required",
            "useful",
        ):
            if rec.decision == "rejected":
                continue
            nodes.append(
                {
                    "id": f"table:{payload['table_name']}",
                    "type": "table",
                    "label": payload["table_name"],
                    "classification": payload["classification"],
                    "row_count": payload.get("row_count"),
                    "column_count": payload.get("column_count"),
                    "decision": rec.decision,
                    "recommendation_id": str(rec.id),
                    "table_name": bare_table_name(payload["table_name"]),
                    **_source_of(payload["table_name"]),
                }
            )
        elif rec.kind == "join" and rec.decision != "rejected":
            edges.append(
                {
                    "id": str(rec.id),
                    "source": f"table:{payload['left_table']}",
                    "target": f"table:{payload['right_table']}",
                    "join_type": payload.get("join_type"),
                    "cardinality": payload.get("cardinality"),
                    "explosion_risk": payload.get("explosion_risk"),
                    "requires_pre_aggregation": payload.get("requires_pre_aggregation"),
                    "label": f"{payload.get('join_type', '').upper()} · {payload.get('cardinality')}",
                    "decision": rec.decision,
                }
            )

    stage_counts = {
        "transformations": sum(
            1 for r in plan.recommendations
            if r.kind == "transformation" and r.decision in ("accepted", "edited")
        ),
        "quality_rules": sum(
            1 for r in plan.recommendations
            if r.kind == "quality_rule" and r.decision in ("accepted", "edited")
        ),
        "derived_columns": sum(
            1 for r in plan.recommendations
            if r.kind == "derived_column" and r.decision in ("accepted", "edited")
        ),
        "aggregations": sum(
            1 for r in plan.recommendations
            if r.kind == "aggregation" and r.decision in ("accepted", "edited")
        ),
    }

    return {
        "plan_id": str(plan.id),
        "anchor_table": (plan.intent or {}).get("anchor_entity"),
        "multi_source": bool(sources_meta),
        "sources": sorted({node["source_name"] for node in nodes if node.get("source_name")}),
        "nodes": nodes,
        "edges": edges,
        "stages": stage_counts,
    }


@router.get("/{plan_id}/preview", response_model=Dict[str, Any])
def plan_preview(plan_id: UUID, db: Session = Depends(get_db)):
    """Estimated impact of the accepted plan. Reads metadata, runs no SQL."""
    plan = _require_plan(db, plan_id)

    accepted_tables = [
        r.effective_payload for r in plan.recommendations
        if r.kind == "table"
        and r.decision in ("accepted", "edited")
        and (r.effective_payload or {}).get("classification") in ("required", "useful")
    ]
    accepted_joins = [
        r.effective_payload for r in plan.recommendations
        if r.kind == "join" and r.decision in ("accepted", "edited")
    ]
    accepted_tf = [
        r.effective_payload for r in plan.recommendations
        if r.kind == "transformation" and r.decision in ("accepted", "edited")
    ]
    accepted_qr = [
        r.effective_payload for r in plan.recommendations
        if r.kind == "quality_rule" and r.decision in ("accepted", "edited")
    ]

    anchor_rows = None
    if accepted_tables:
        anchor_rows = accepted_tables[0].get("row_count")

    column_estimate = sum(t.get("column_count") or 0 for t in accepted_tables)
    fanout_joins = [j for j in accepted_joins if j.get("requires_pre_aggregation")]

    return {
        "plan_id": str(plan.id),
        "tables_selected": len(accepted_tables),
        "joins_selected": len(accepted_joins),
        "estimated_rows": anchor_rows,
        "estimated_columns": column_estimate,
        "row_estimate_basis": (
            "Anchor table row count. Measure tables are aggregated to this grain, "
            "so the output row count equals the anchor."
        ),
        "join_impact": [
            {
                "join": f"{j['left_table']} -> {j['right_table']}",
                "cardinality": j.get("cardinality"),
                "explosion_risk": j.get("explosion_risk"),
                "pre_aggregated": bool(j.get("requires_pre_aggregation")),
            }
            for j in accepted_joins
        ],
        "duplicate_impact": {
            "dedupe_steps": sum(
                1 for t in accepted_tf if t.get("step_type") == "remove_duplicates"
            ),
        },
        "null_impact": {
            "fill_steps": sum(
                1 for t in accepted_tf if t.get("step_type") == "replace_null_values"
            ),
            "drop_steps": sum(
                1 for t in accepted_tf if t.get("step_type") == "remove_null_rows"
            ),
        },
        "quality_impact": {
            "rules": len(accepted_qr),
            "blocking_rules": sum(
                1 for r in accepted_qr if r.get("severity") == "error"
            ),
        },
        "warnings": (
            [
                f"{len(fanout_joins)} join(s) need pre-aggregation to avoid row "
                f"multiplication. The compiler handles this, but check the grain."
            ]
            if fanout_joins
            else []
        ),
    }


@router.post("/{plan_id}/compile", response_model=Dict[str, Any], status_code=201)
def compile_plan(
    plan_id: UUID,
    request: CompileRequest,
    db: Session = Depends(get_db),
):
    """Freeze the accepted plan into a PipelineVersion.

    After this, execution is the existing deterministic path. The saved JSON is
    the contract: the same definition always produces the same dataset, and no
    model is called at run time.
    """
    plan = _require_plan(db, plan_id)

    project = db.query(Project).filter(Project.id == request.project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    accepted = [
        rec for rec in plan.recommendations if rec.decision in ("accepted", "edited")
    ]
    if not accepted:
        raise HTTPException(
            status_code=409,
            detail="Nothing has been accepted yet. Approve recommendations first.",
        )

    scan_id = plan.catalog_scan_id
    source_id = plan.data_source_id
    schema_name = plan.schema_name
    sources_meta = (plan.intent or {}).get("sources") or {}
    if sources_meta:
        used = {
            str((rec.effective_payload or {}).get("table_name", "")).split(SOURCE_SEPARATOR, 1)[0]
            for rec in accepted
            if rec.kind == "table"
            and (rec.effective_payload or {}).get("classification") in ("required", "useful")
        }
        if len(used) > 1:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Approved tables come from more than one source (%s). The "
                    "execution engine runs one SQL connection per pipeline, so a "
                    "cross-source pipeline cannot be compiled yet. Verify the "
                    "cross-source joins, then keep tables from one source."
                    % ", ".join(sorted(used))
                ),
            )
        if used:
            chosen = used.pop()
            meta = sources_meta.get(chosen)
            if not meta:
                raise HTTPException(status_code=409, detail="Unknown plan source.")
            prefix = chosen + SOURCE_SEPARATOR
            others = [name + SOURCE_SEPARATOR for name in sources_meta if name != chosen]
            views = []
            for rec in accepted:
                if rec.kind == "column_usage":
                    payload = rec.effective_payload or {}
                    if not str(payload.get("table_name", "")).startswith(prefix):
                        continue
                elif rec.kind == "table" and not str(
                    (rec.effective_payload or {}).get("table_name", "")
                ).startswith(prefix):
                    continue
                elif any(o in json.dumps(rec.effective_payload, default=str) for o in others):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Approved recommendation '%s' references another source. "
                            "Reject it, or keep every table in one source."
                            % rec.ref_key
                        ),
                    )
                views.append(_RecView(rec, _strip_qualifier(rec.effective_payload, prefix)))
            accepted = views
            scan_id = UUID(meta["scan_id"])
            source_id = UUID(meta["data_source_id"])
            schema_name = meta.get("schema_name")

    # 'Keep' decisions (keep nulls, keep duplicates) are approved choices that
    # deliberately compile to no step.
    accepted = [rec for rec in accepted if not _is_kept_as_is(rec)]

    accepted_tables = [
        rec.effective_payload
        for rec in accepted
        if rec.kind == "table"
        and (rec.effective_payload or {}).get("classification") in ("required", "useful")
    ]
    required_tables = [
        item["table_name"]
        for item in accepted_tables
        if item.get("classification") == "required"
    ]
    useful_tables = [
        item["table_name"]
        for item in accepted_tables
        if item.get("classification") == "useful"
    ]
    if not required_tables:
        raise HTTPException(
            status_code=409,
            detail="No required base table is approved - cannot compile a pipeline.",
        )

    saved_base_table = (plan.intent or {}).get("base_table")
    anchor_hint = str((plan.intent or {}).get("anchor_entity") or "").casefold()
    inferred_base_table = next(
        (
            table
            for table in required_tables
            if anchor_hint
            and (
                anchor_hint in table.casefold()
                or table.casefold() in anchor_hint
            )
        ),
        required_tables[0],
    )
    base_table = (
        saved_base_table
        if isinstance(saved_base_table, str) and saved_base_table in required_tables
        else inferred_base_table
    )
    tables = [base_table] + [
        table
        for table in required_tables + useful_tables
        if table != base_table
    ]
    if not tables:
        raise HTTPException(
            status_code=409, detail="No tables accepted - cannot compile a pipeline."
        )
    catalog_tables = (
        db.query(CatalogTable)
        .filter(
            CatalogTable.scan_id == scan_id,
            CatalogTable.table_name.in_(tables),
        )
        .all()
    )
    tables_by_name = {table.table_name: table for table in catalog_tables}
    if set(tables_by_name) != set(tables):
        raise HTTPException(
            status_code=409,
            detail="The plan's catalog snapshot is missing an approved table.",
        )
    selected_columns = {
        table_name: [
            column.column_name
            for column in sorted(
                tables_by_name[table_name].columns,
                key=lambda item: item.ordinal_position,
            )
        ]
        for table_name in tables
    }
    if any(not columns for columns in selected_columns.values()):
        raise HTTPException(
            status_code=409,
            detail="The plan's catalog snapshot is missing table columns.",
        )

    # Column usage: approved 'unused' columns leave the output dataset - unless
    # a join, filter, aggregation, cleaning step, quality rule, derived column
    # or the training target still depends on them.
    referenced = _referenced_columns(accepted)
    expressions = " ".join(c for t, c in referenced if t == "__expr__" and c)
    dropped_columns = []
    for rec in accepted:
        if rec.kind != "column_usage":
            continue
        usage = rec.effective_payload or {}
        table_name = usage.get("table_name")
        column_name = usage.get("column_name")
        if (
            usage.get("usage") != "unused"
            or table_name not in selected_columns
            or (table_name, column_name) in referenced
            or column_name == project.target_column
            or (column_name and column_name in expressions)
        ):
            continue
        remaining = [c for c in selected_columns[table_name] if c != column_name]
        if remaining:
            selected_columns[table_name] = remaining
            dropped_columns.append(f"{table_name}.{column_name}")

    cleaning_steps = []
    for index, rec in enumerate(
        [r for r in accepted if r.kind == "transformation"], start=1
    ):
        payload = rec.effective_payload
        cleaning_steps.append(
            {
                "step_name": payload.get("step_type"),
                "rule_type": payload.get("step_type"),
                "rule_config": payload.get("configuration", {}),
                "step_order": index,
                "enabled": True,
            }
        )

    approved_filters = [
        rec.effective_payload for rec in accepted if rec.kind == "filter"
    ]
    for item in approved_filters:
        if (
            not isinstance(item, dict)
            or item.get("source_table") not in tables
            or item.get("column") not in selected_columns.get(item.get("source_table"), [])
        ):
            raise HTTPException(
                status_code=409,
                detail="An approved filter targets a table or column not selected for the pipeline.",
            )

    approved_aggregations = [
        rec.effective_payload for rec in accepted if rec.kind == "aggregation"
    ]
    approved_group_by = []
    if approved_aggregations:
        approved_group_by = approved_aggregations[0].get("group_by", [])
        if any(
            item.get("group_by", []) != approved_group_by
            for item in approved_aggregations
        ):
            raise HTTPException(
                status_code=409,
                detail="Approved aggregations must use the same group_by columns.",
            )
        for item in approved_aggregations:
            if item.get("source_table") not in tables:
                raise HTTPException(
                    status_code=409,
                    detail="An approved aggregation targets a table not selected for the pipeline.",
                )
        for item in approved_group_by:
            if (
                not isinstance(item, dict)
                or item.get("source_table") not in tables
                or item.get("column")
                not in selected_columns.get(item.get("source_table"), [])
            ):
                raise HTTPException(
                    status_code=409,
                    detail="An approved group_by targets a table or column not selected for the pipeline.",
                )

    pipeline_json = {
        "schema_version": 1,
        "source": {
            "data_source_id": str(source_id),
            "schema_name": schema_name,
        },
        "base_table": base_table,
        "target_column": project.target_column,
        "user_request": plan.requirement_text,
        "plan_id": str(plan.id),
        "intent": plan.intent,
        "tables": tables,
        "selected_columns": selected_columns,
        "joins": [
            rec.effective_payload for rec in accepted if rec.kind == "join"
        ],
        "filters": approved_filters,
        "group_by": approved_group_by,
        "aggregations": approved_aggregations,
        "relationships": [
            rec.effective_payload for rec in accepted if rec.kind == "relationship"
        ],
        "cleaning_steps": cleaning_steps,
        "transformations": [
            rec.effective_payload for rec in accepted if rec.kind == "transformation"
        ],
        "quality_rules": [
            rec.effective_payload
            for rec in accepted
            if rec.kind == "quality_rule"
            and (rec.effective_payload or {}).get("rule_type") != "freshness"
        ],
        "freshness": next(
            (
                (rec.effective_payload or {}).get("rule_config")
                for rec in accepted
                if rec.kind == "quality_rule"
                and (rec.effective_payload or {}).get("rule_type") == "freshness"
            ),
            None,
        ),
        "column_usage": [
            rec.effective_payload for rec in accepted if rec.kind == "column_usage"
        ],
        "derived_columns": [
            rec.effective_payload for rec in accepted if rec.kind == "derived_column"
        ],
        "decisions": [
            {
                "ref_key": rec.ref_key,
                "kind": rec.kind,
                "decision": rec.decision,
                "origin": rec.origin,
                "confidence": rec.confidence,
                "decided_by": rec.decided_by,
            }
            for rec in plan.recommendations
        ],
    }
    pipeline_json["execution_graph"] = build_execution_graph(pipeline_json)
    source_aliases = {}
    for table, columns in selected_columns.items():
        for column in columns:
            source_aliases["_saved_column_%d" % len(source_aliases)] = (
                "%s.%s" % (table, column)
            )
    try:
        validate_derived_columns(
            "postgresql",
            source_aliases,
            pipeline_json["derived_columns"],
        )
    except PipelineExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    pipeline_json["dropped_columns"] = dropped_columns

    existing_definition = None
    if request.pipeline_definition_id is not None:
        existing_definition = (
            db.query(PipelineDefinition)
            .filter(PipelineDefinition.id == request.pipeline_definition_id)
            .first()
        )
        if existing_definition is None or existing_definition.status == "deleted":
            raise HTTPException(status_code=404, detail="Pipeline not found.")
        if existing_definition.project_id != request.project_id:
            raise HTTPException(
                status_code=409,
                detail="The pipeline belongs to a different project.",
            )

    try:
        if existing_definition is not None:
            definition = existing_definition
            next_number = (
                max((v.version_number for v in definition.versions), default=0) + 1
            )
        else:
            definition = PipelineDefinition(
                project_id=request.project_id,
                pipeline_name=request.pipeline_name,
                description=request.description
                or f"Compiled from plan: {plan.requirement_text[:120]}",
                status="draft",
            )
            db.add(definition)
            db.flush()
            next_number = 1

        version = PipelineVersion(
            pipeline_definition_id=definition.id,
            version_number=next_number,
            pipeline_json=pipeline_json,
        )
        db.add(version)
        db.flush()

        plan.pipeline_definition_id = definition.id
        plan.status = "compiled"
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to compile plan")
        raise HTTPException(
            status_code=500, detail="Failed to compile the plan."
        ) from exc

    return {
        "plan_id": str(plan.id),
        "pipeline_definition_id": str(definition.id),
        "pipeline_version_id": str(version.id),
        "version_number": version.version_number,
        "tables": tables,
        "cleaning_steps": len(cleaning_steps),
        "quality_rules": len(pipeline_json["quality_rules"]),
        "filters": len(pipeline_json["filters"]),
        "aggregations": len(pipeline_json["aggregations"]),
        "status": plan.status,
        "note": (
            "Execution runs this saved definition exactly as stored. No model is "
            "called at run time and the plan is never regenerated."
        ),
    }
