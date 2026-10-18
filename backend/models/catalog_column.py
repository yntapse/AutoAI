"""A column discovered by a metadata scan.

sample_values is OPT-IN per scan and holds customer row data. It is excluded
from every LLM payload - see services/metadata_scanner.catalog_for_llm().
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class CatalogColumn(Base):
    __tablename__ = "catalog_columns"
    __table_args__ = (
        UniqueConstraint(
            "catalog_table_id", "column_name", name="uq_catalog_columns_table_column"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    catalog_table_id = Column(
        UUID(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    column_name = Column(String, nullable=False, index=True)
    data_type = Column(String, nullable=False)
    ordinal_position = Column(Integer, nullable=False, default=0)
    is_nullable = Column(Boolean, nullable=False, default=True)
    is_primary_key = Column(Boolean, nullable=False, default=False)
    is_foreign_key = Column(Boolean, nullable=False, default=False)
    normalized_name = Column(String, nullable=True, index=True)
    sample_values = Column(JSON, nullable=True)
    business_description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    table = relationship("CatalogTable", back_populates="columns")
