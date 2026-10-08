from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class PipelineDefinition(Base):
    __tablename__ = "pipeline_definitions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    pipeline_name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(
        String,
        nullable=False,
        default="active",
        server_default="active",
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    project = relationship("Project", back_populates="pipeline_definitions")
    versions = relationship(
        "PipelineVersion",
        back_populates="pipeline_definition",
        cascade="all, delete-orphan",
    )

    @property
    def latest_run_status(self):
        runs = [
            run
            for version in self.versions
            for run in version.pipeline_runs
        ]
        if not runs:
            return None
        return max(
            runs,
            key=lambda run: run.started_at or datetime.min,
        ).status
