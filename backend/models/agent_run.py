from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = Column(String, nullable=False)
    current_iteration = Column(Integer, nullable=False, default=0, server_default="0")
    max_iterations = Column(Integer, nullable=False)
    improvement_threshold = Column(Float, nullable=False)
    best_training_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("training_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.timezone("utc", func.now()),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_agent_runs_status",
        ),
        Index("ix_agent_runs_project_id", "project_id"),
        Index("ix_agent_runs_status", "status"),
        Index("ix_agent_runs_project_status", "project_id", "status"),
    )
