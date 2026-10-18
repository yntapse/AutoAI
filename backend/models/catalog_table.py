"""A table discovered by a metadata scan."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class CatalogTable(Base):
    __tablename__ = "catalog_tables"
    __table_args__ = (
        UniqueConstraint("scan_id", "table_name", name="uq_catalog_tables_scan_table"),
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
    schema_name = Column(String, nullable=False)
    table_name = Column(String, nullable=False, index=True)
    column_count = Column(Integer, nullable=False, default=0)
    row_count = Column(BigInteger, nullable=True)
    row_count_is_estimate = Column(Boolean, nullable=False, default=True)
    has_primary_key = Column(Boolean, nullable=False, default=False)
    business_description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    scan = relationship("CatalogScan", back_populates="tables")
    data_source = relationship("DataSource", back_populates="tables")
    columns = relationship(
        "CatalogColumn",
        back_populates="table",
        cascade="all, delete-orphan",
    )
