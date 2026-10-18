"""A relationship captured by a metadata scan.

Sprint 1 records only DECLARED foreign keys (origin='declared', confidence 1.0,
approved NULL). Sprint 2 adds inferred relationships from name similarity and
value overlap. The approval columns exist now so that Sprint 2 adds no
migration to this table.

Nothing here is a verified join path - only etl_copilot/join_verifier.py may
mark a path verified, and that contract is unchanged.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class CatalogRelationship(Base):
    __tablename__ = "catalog_relationships"
    __table_args__ = (
        UniqueConstraint(
            "scan_id",
            "left_table",
            "left_column",
            "right_table",
            "right_column",
            name="uq_catalog_relationships_edge",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    scan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("catalog_scans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    data_source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    left_table = Column(String, nullable=False)
    left_column = Column(String, nullable=False)
    right_table = Column(String, nullable=False)
    right_column = Column(String, nullable=False)
    constraint_name = Column(String, nullable=True)
    origin = Column(String, nullable=False, default="declared")
    confidence = Column(Float, nullable=False, default=1.0)
    cardinality = Column(String, nullable=True)
    approved = Column(Boolean, nullable=True)
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    scan = relationship("CatalogScan", back_populates="relationships")
