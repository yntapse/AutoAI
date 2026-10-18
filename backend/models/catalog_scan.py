"""One metadata scan of one registered source.

A scan is the unit of versioning for the catalog: tables and columns always
belong to a scan, so an earlier picture of a schema is never overwritten.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class CatalogScan(Base):
    __tablename__ = "catalog_scans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    data_source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    schema_name = Column(String, nullable=False)
    status = Column(String, nullable=False, default="running")
    table_count = Column(Integer, nullable=False, default=0)
    column_count = Column(Integer, nullable=False, default=0)
    relationship_count = Column(Integer, nullable=False, default=0)
    included_row_counts = Column(Integer, nullable=False, default=0)
    included_sample_values = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    data_source = relationship("DataSource", back_populates="scans")
    tables = relationship(
        "CatalogTable",
        back_populates="scan",
        cascade="all, delete-orphan",
    )
    relationships = relationship(
        "CatalogRelationship",
        back_populates="scan",
        cascade="all, delete-orphan",
    )
