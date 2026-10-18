"""One reviewable AI or deterministic recommendation inside a plan.

Every recommendation carries a reason, a confidence and an expected impact, and
starts at decision='pending'. Nothing is executable until a human accepts it.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base

KINDS = (
    "table",
    "relationship",
    "join",
    "cardinality",
    "transformation",
    "derived_column",
    "quality_rule",
    "filter",
    "aggregation",
    "column_usage",
)

DECISIONS = ("pending", "accepted", "rejected", "edited")


class PlanRecommendation(Base):
    __tablename__ = "plan_recommendations"
    __table_args__ = (
        UniqueConstraint("plan_id", "ref_key", name="uq_plan_recommendations_ref"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(String, nullable=False, index=True)
    ref_key = Column(String, nullable=False)
    title = Column(String, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    reason = Column(Text, nullable=False)
    alternative_reason = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    expected_impact = Column(Text, nullable=True)
    risk_level = Column(String, nullable=False, default="low")
    origin = Column(String, nullable=False, default="deterministic")
    decision = Column(String, nullable=False, default="pending")
    user_payload = Column(JSON, nullable=True)
    decided_by = Column(String, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    plan = relationship("PipelinePlan", back_populates="recommendations")

    @property
    def effective_payload(self):
        """What compilation uses: the user's edit if present, else the AI's."""
        if self.decision == "edited" and self.user_payload:
            return self.user_payload
        return self.payload
