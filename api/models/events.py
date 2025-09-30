from sqlalchemy import Column, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from .base import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=True)
    requirement_id = Column(UUID(as_uuid=True), ForeignKey("requirements.id"), nullable=True)
    type = Column(String, nullable=False)  # "upload", "extracted", "reminder_sent", "completed"
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    at = Column(DateTime(timezone=True), server_default=func.now())
