from uuid import uuid4

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class CleaningRule(Base):
    __tablename__ = "cleaning_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pipeline_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_name = Column(String, nullable=False, default="Cleaning step")
    rule_type = Column(String, nullable=False)
    rule_config = Column(JSON, nullable=False, default=dict)
    step_order = Column(Integer, nullable=False, default=0)
    enabled = Column(Boolean, nullable=False, default=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    pipeline_version = relationship("PipelineVersion", back_populates="cleaning_rules")
