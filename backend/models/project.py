from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_name = Column(String, nullable=False)
    file_id = Column(String, nullable=False)
    target_column = Column(String, nullable=False)
    num_rows = Column(Integer, nullable=False, default=0)
    num_features = Column(Integer, nullable=False, default=0)
    num_numeric_features = Column(Integer, nullable=False, default=0)
    num_categorical_features = Column(Integer, nullable=False, default=0)
    missing_value_count = Column(Integer, nullable=False, default=0)
    target_variance = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
