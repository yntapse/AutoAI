from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class TrainingRun(Base):
    __tablename__ = "training_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    agent_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    version_number = Column(Integer, nullable=False)
    status = Column(String, nullable=False)
    stage = Column(String, nullable=True)
    progress = Column(Integer, nullable=True)
    best_model_name = Column(String, nullable=True)
    rmse = Column(Float, nullable=True)
    mae = Column(Float, nullable=True)
    r2 = Column(Float, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "version_number", name="uq_training_runs_project_version"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_training_runs_status",
        ),
        CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 100)",
            name="ck_training_runs_progress_range",
        ),
        Index("ix_training_runs_project_id", "project_id"),
        Index("ix_training_runs_agent_run_id", "agent_run_id"),
        Index("ix_training_runs_status", "status"),
        Index("ix_training_runs_status_stage", "status", "stage"),
        Index("ix_training_runs_created_at", "created_at"),
        Index("ix_training_runs_project_status", "project_id", "status"),
    )
