from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class ExperimentMemory(Base):
    """Persistent experiment memory for cross-run knowledge accumulation.

    Each row summarises a completed training iteration so that future agent
    runs can retrieve previously successful strategies on similar datasets.
    Records are append-only: the autonomous agent is never allowed to modify
    or delete past entries.
    """

    __tablename__ = "experiment_memory"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    # ── dataset identity ──
    dataset_fingerprint = Column(String(128), nullable=False, index=True)
    # ── experiment summary ──
    model_family = Column(String(100), nullable=True)
    preprocessing_tokens = Column(Text, nullable=True)       # comma-separated
    training_tokens = Column(Text, nullable=True)             # comma-separated
    strategy_summary = Column(Text, nullable=True)
    feature_count = Column(Integer, nullable=True)
    categorical_ratio = Column(Float, nullable=True)
    rmse_cv = Column(Float, nullable=True)
    rmse_holdout = Column(Float, nullable=True)
    # ── provenance (informational, not FK-enforced) ──
    project_id = Column(UUID(as_uuid=True), nullable=True)
    agent_run_id = Column(UUID(as_uuid=True), nullable=True)
    iteration_number = Column(Integer, nullable=True)
    # ── numeric profile fields used for similarity scoring ──
    num_rows = Column(Integer, nullable=True)
    num_features = Column(Integer, nullable=True)
    num_numeric_features = Column(Integer, nullable=True)
    num_categorical_features = Column(Integer, nullable=True)
    missing_value_ratio = Column(Float, nullable=True)
    target_std = Column(Float, nullable=True)
    # ── timestamps ──
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_experiment_memory_fingerprint", "dataset_fingerprint"),
        Index("ix_experiment_memory_rmse_cv", "rmse_cv"),
        Index("ix_experiment_memory_created", "created_at"),
    )
