from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from .base import Base

class Event(Base):
    __tablename__ = "events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), nullable=True)
    requirement_id = Column(UUID(as_uuid=True), ForeignKey("requirements.id"), nullable=True)
    type = Column(String, nullable=False)  # "upload", "extracted", "reminder_sent", "completed"
    data = Column(String, nullable=True)
    at = Column(DateTime(timezone=True), server_default=func.now())
