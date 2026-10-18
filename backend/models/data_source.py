"""A data source the customer has registered with the platform.

Connection metadata only. The password is NEVER stored here - every existing
router already takes credentials per request (see EtlCopilotConnection), and
Sprint 1 keeps that posture. A scan supplies the password in the request body.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class DataSource(Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("source_name", name="uq_data_sources_source_name"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_name = Column(String, nullable=False)
    db_type = Column(String, nullable=False)
    host = Column(String, nullable=True)
    port = Column(Integer, nullable=True)
    database = Column(String, nullable=True)
    username = Column(String, nullable=True)
    schema_name = Column(String, nullable=True)
    trusted_connection = Column(Boolean, nullable=False, default=False)
    odbc_driver = Column(String, nullable=True)
    description = Column(String, nullable=True)
    status = Column(String, nullable=False, default="registered")
    last_scanned_at = Column(DateTime, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    scans = relationship(
        "CatalogScan",
        back_populates="data_source",
        cascade="all, delete-orphan",
    )
    tables = relationship(
        "CatalogTable",
        back_populates="data_source",
        cascade="all, delete-orphan",
    )
