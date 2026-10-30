"""A measured verification of one candidate relationship.

Feature 4 of Phase 4. The table is created by migration
0005_pipeline_intelligence; this model mirrors that schema exactly - the
migration is the source of truth for column names and nullability.

Every row is the result of running services/join_profiler.build_profile_query
against the real source, never an inference from metadata. `measured` is True
only when a query returned counts, and `verdict` stays "unmeasured" otherwise.
That is what makes "never mark a relationship verified without measurement"
enforceable at the storage layer rather than by convention.

Verifications are append-only: re-verifying writes a new row, so the history of
what the data looked like over time is preserved.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

from database import Base

# How the measurement was taken. Only one method exists today, but the column
# exists so a sampled or estimated measurement can never be mistaken for a
# full one.
METHODS = ("single_query",)

VERDICT_UNMEASURED = "unmeasured"
VERDICT_SAFE = "safe"
VERDICT_CAUTION = "caution"
VERDICT_RISKY = "risky"
VERDICTS = (VERDICT_UNMEASURED, VERDICT_SAFE, VERDICT_CAUTION, VERDICT_RISKY)

# Derived from measured cardinality plus max_rows_per_key.
FANOUT_RISK_LEVELS = ("none", "low", "medium", "high")


class JoinVerification(Base):
    __tablename__ = "join_verifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recommendation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("plan_recommendations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Plain UUIDs, not foreign keys: a multi-source join may reference a source
    # that is later de-registered, and losing the measurement history with it
    # would defeat the point of keeping it.
    left_source_id = Column(UUID(as_uuid=True), nullable=True)
    right_source_id = Column(UUID(as_uuid=True), nullable=True)

    left_table = Column(String, nullable=False)
    left_column = Column(String, nullable=False)
    right_table = Column(String, nullable=False)
    right_column = Column(String, nullable=False)

    method = Column(String, nullable=False, default="single_query")
    measured = Column(Boolean, nullable=False, default=False)

    # --- derived from the measurements ---
    cardinality = Column(String, nullable=True)
    match_rate = Column(Float, nullable=True)
    orphan_rate = Column(Float, nullable=True)

    # --- measured by the database (join_profiler.PROFILE_COLUMNS) ---
    base_rows = Column(BigInteger, nullable=True)
    related_rows = Column(BigInteger, nullable=True)
    base_null_keys = Column(BigInteger, nullable=True)
    inner_rows = Column(BigInteger, nullable=True)
    left_rows = Column(BigInteger, nullable=True)
    max_rows_per_key = Column(BigInteger, nullable=True)

    row_multiplication = Column(Boolean, nullable=True)
    fanout_risk = Column(String, nullable=True)

    recommended_join = Column(String, nullable=True)
    verdict = Column(String, nullable=False, default=VERDICT_UNMEASURED)
    explanation = Column(Text, nullable=True)
    reasons = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)

    verified_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    @property
    def row_explosion_ratio(self):
        """Output rows per base row, measured. 1.0 preserves the grain."""
        if not self.base_rows or self.inner_rows is None:
            return None
        return round(self.inner_rows / self.base_rows, 4)

    @property
    def is_trustworthy(self) -> bool:
        """Only a measured, non-risky verification may back a join."""
        return bool(self.measured) and self.verdict in (
            VERDICT_SAFE,
            VERDICT_CAUTION,
        )
