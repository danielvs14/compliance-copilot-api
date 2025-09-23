from enum import Enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Enum as SAEnum, Float, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from .base import Base


class RequirementStatusEnum(str, Enum):
    OPEN = "OPEN"
    REVIEW = "REVIEW"
    DONE = "DONE"


class Requirement(Base):
    __tablename__ = "requirements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), nullable=False)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    title_en = Column(String, nullable=False)
    title_es = Column(String, nullable=False)
    description_en = Column(String, nullable=False)
    description_es = Column(String, nullable=False)
    category = Column(String, nullable=True)
    frequency = Column(String, nullable=True)   # "weekly", "annual", "before each use"
    due_date = Column(DateTime(timezone=True), nullable=True)
    status = Column(SAEnum(RequirementStatusEnum, name="requirement_status"), nullable=False, server_default=RequirementStatusEnum.OPEN.value)
    source_ref = Column(String, nullable=False)
    confidence = Column(Float, nullable=False, default=0.7)
    trade = Column(String, nullable=False, default="electrical")
    attributes = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def mark_complete(self):
        self.status = RequirementStatusEnum.DONE
        self.completed_at = datetime.now(timezone.utc)
