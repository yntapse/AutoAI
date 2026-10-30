from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class QualityRule(Base):
    __tablename__ = "quality_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pipeline_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_name = Column(String, nullable=False)
    rule_type = Column(String, nullable=False)
    severity = Column(String, nullable=False, default="error")
    enabled = Column(Boolean, nullable=False, default=True)
    rule_config = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    pipeline_version = relationship("PipelineVersion", back_populates="quality_rules")
