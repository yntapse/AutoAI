from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base
from models.quality_rule import QualityRule


class PipelineVersion(Base):
    __tablename__ = "pipeline_versions"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_definition_id",
            "version_number",
            name="uq_pipeline_version_number",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pipeline_definition_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number = Column(Integer, nullable=False)
    pipeline_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    pipeline_definition = relationship(
        "PipelineDefinition",
        back_populates="versions",
    )
    pipeline_runs = relationship(
        "PipelineRun",
        back_populates="pipeline_version",
        cascade="all, delete-orphan",
    )
    cleaning_rules = relationship(
        "CleaningRule",
        back_populates="pipeline_version",
        cascade="all, delete-orphan",
    )
    transformation_rules = relationship(
        "TransformationRule",
        back_populates="pipeline_version",
        cascade="all, delete-orphan",
    )
    quality_rules = relationship(
        QualityRule,
        back_populates="pipeline_version",
        cascade="all, delete-orphan",
    )
