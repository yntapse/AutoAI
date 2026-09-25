from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database import Base


class SandboxJob(Base):
    __tablename__ = "sandbox_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    iteration_number = Column(Integer, nullable=False)
    script_content = Column(Text, nullable=False)
    status = Column(String, nullable=False)
    result_json = Column(JSONB, nullable=True)
    error_log = Column(Text, nullable=True)
    timeout_seconds = Column(Integer, nullable=False, default=60, server_default="60")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.timezone("utc", func.now()),
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'timeout')",
            name="ck_sandbox_jobs_status",
        ),
        Index("ix_sandbox_jobs_status", "status"),
        Index("ix_sandbox_jobs_created_at", "created_at"),
    )