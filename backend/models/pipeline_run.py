from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pipeline_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_versions.id", ondelete="CASCADE"),
        nullable=True,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = Column(String, nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    execution_log = Column(Text, nullable=True)
    dataset_file_id = Column(String, nullable=True)
    row_count = Column(Integer, nullable=True)
    execution_time = Column(Float, nullable=True)

    pipeline_version = relationship("PipelineVersion", back_populates="pipeline_runs")
    project = relationship("Project", back_populates="pipeline_runs")
    agent_decisions = relationship(
        "AgentDecision",
        back_populates="pipeline_run",
        cascade="all, delete-orphan",
    )
    source_profile_snapshots = relationship(
        "SourceProfileSnapshot",
        back_populates="pipeline_run",
    )
