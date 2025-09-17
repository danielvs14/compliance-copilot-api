from sqlalchemy import Column, String, DateTime, ForeignKey, Enum, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from .base import Base

class RequirementStatusEnum(str, Enum):
    OPEN = "OPEN"
    DONE = "DONE"

class Requirement(Base):
    __tablename__ = "requirements"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), nullable=True)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    title = Column(String, nullable=False)
    category = Column(String, nullable=True)
    frequency = Column(String, nullable=True)   # "weekly", "annual", "before each use"
    due_date = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, default="OPEN")
    source_ref = Column(String, nullable=False)
    confidence = Column(Float, nullable=False, default=0.7)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
