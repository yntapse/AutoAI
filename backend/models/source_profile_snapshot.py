from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class SourceProfileSnapshot(Base):
    __tablename__ = "source_profile_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pipeline_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    db_type = Column(String, nullable=False)
    schema_name = Column(String, nullable=False)
    selected_tables = Column(JSON, nullable=False)
    profile_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    pipeline_run = relationship(
        "PipelineRun",
        back_populates="source_profile_snapshots",
    )
