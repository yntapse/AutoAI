from uuid import uuid4

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSON, UUID

from database import Base


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    training_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("training_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_name = Column(String, nullable=False)
    rmse = Column(Float, nullable=True)
    mae = Column(Float, nullable=True)
    r2 = Column(Float, nullable=True)
    hyperparameters = Column(JSON, nullable=True)
    rank_position = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.timezone("utc", func.now()),
    )

    __table_args__ = (
        UniqueConstraint(
            "training_run_id",
            "model_name",
            name="uq_model_versions_training_run_id_model_name",
        ),
        Index("ix_model_versions_training_run_id", "training_run_id"),
        Index("ix_model_versions_model_name", "model_name"),
    )
