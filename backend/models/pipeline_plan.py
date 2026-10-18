"""An AI-assisted pipeline plan produced from a business requirement.

A plan is the reviewable artefact between "the user typed a sentence" and
"a pipeline version exists". It holds recommendations, not executable steps.
Compiling an approved plan produces a PipelineVersion, which is what actually
runs - so execution stays deterministic and never re-plans.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class PipelinePlan(Base):
    __tablename__ = "pipeline_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    data_source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    catalog_scan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("catalog_scans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    pipeline_definition_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_definitions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    requirement_text = Column(Text, nullable=False)
    schema_name = Column(String, nullable=True)
    status = Column(String, nullable=False, default="draft")
    intent = Column(JSON, nullable=False, default=dict)
    intent_origin = Column(String, nullable=False, default="heuristic")
    recommendation_count = Column(Integer, nullable=False, default=0)
    accepted_count = Column(Integer, nullable=False, default=0)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    recommendations = relationship(
        "PlanRecommendation",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="PlanRecommendation.sort_order",
    )
