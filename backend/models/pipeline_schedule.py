"""A stored schedule for an existing pipeline version.

Feature 9 of Phase 4. The table is created by migration
0005_pipeline_intelligence; this model mirrors that schema exactly - the
migration is the source of truth for column names and nullability.

The schedule pins a specific pipeline_version_id, and the column is NOT NULL.
That is the mechanism behind "never execute draft version": a schedule cannot
exist without a saved version to point at, so firing it can only ever run that
version's stored pipeline_json as written.

Credentials are never stored here either. `credential_env` names an environment
variable the worker resolves at run time, so a scheduled run authenticates
without a password ever being written to a row.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

from database import Base

FREQUENCIES = ("once", "hourly", "daily", "weekly", "monthly", "cron")

# Sub-hourly schedules are deliberately not offered: a run is measured in
# minutes, so a finer schedule would overlap itself.
CRON_FOR_FREQUENCY = {
    "hourly": "0 * * * *",
    "daily": "0 2 * * *",
    "weekly": "0 2 * * 1",
    "monthly": "0 2 1 * *",
}

STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_DISABLED = "disabled"
STATUSES = (STATUS_ACTIVE, STATUS_PAUSED, STATUS_DISABLED)


class PipelineSchedule(Base):
    __tablename__ = "pipeline_schedules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pipeline_definition_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The exact saved version this schedule executes. NOT NULL by design.
    pipeline_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    data_source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
    )

    frequency = Column(String, nullable=False)
    cron_expression = Column(String, nullable=True)
    run_at = Column(DateTime, nullable=True)

    status = Column(String, nullable=False, default=STATUS_ACTIVE)
    credential_env = Column(String, nullable=True)
    target_column = Column(String, nullable=True)
    freshness_sla_hours = Column(Integer, nullable=True)

    next_run_at = Column(DateTime, nullable=True, index=True)
    last_run_at = Column(DateTime, nullable=True)
    last_run_status = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)
    run_count = Column(Integer, nullable=False, default=0)

    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    is_deleted = Column(Boolean, nullable=False, default=False)

    @property
    def is_active(self) -> bool:
        """A schedule only fires when it is active and not soft-deleted."""
        return self.status == STATUS_ACTIVE and not bool(self.is_deleted)
