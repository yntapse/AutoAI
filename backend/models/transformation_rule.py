from uuid import uuid4

from sqlalchemy import Column, ForeignKey, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class TransformationRule(Base):
    __tablename__ = "transformation_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pipeline_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    transformation_type = Column(String, nullable=False)
    transformation_config = Column(JSON, nullable=False, default=dict)

    pipeline_version = relationship("PipelineVersion", back_populates="transformation_rules")
