"""A monitoring alert: schema change, missing source, failed connection,
volume/quality drift, failed run or stale data.

Alerts are de-duplicated by `fingerprint` while open, so a detector that runs
every minute does not create a new row every minute.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID

from database import Base

ALERT_TYPES = (
    "schema_change",
    "source_missing",
    "connection_failure",
    "data_drift",
    "quality_drop",
    "run_failed",
    "stale_data",
)


class MonitoringAlert(Base):
    __tablename__ = "monitoring_alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pipeline_definition_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_definitions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    data_source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    pipeline_run_id = Column(UUID(as_uuid=True), nullable=True)
    alert_type = Column(String, nullable=False, index=True)
    severity = Column(String, nullable=False, default="warning")
    title = Column(String, nullable=False)
    message = Column(Text, nullable=True)
    detail = Column(JSON, nullable=True)
    fingerprint = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="open")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
