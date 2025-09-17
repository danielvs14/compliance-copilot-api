from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from .base import Base

class Document(Base):
    __tablename__ = "documents"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), nullable=True)   # single-tenant in MVP
    name = Column(String, nullable=False)
    storage_url = Column(String, nullable=True)          # S3/local path
    text_excerpt = Column(String, nullable=True)         # optional short text
    created_at = Column(DateTime(timezone=True), server_default=func.now())
