from sqlalchemy import Column, Integer, DateTime, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from .base import Base


class OrgRequirementMetrics(Base):
    __tablename__ = "org_requirement_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    requirements_created_total = Column(Integer, nullable=False, server_default=text("0"))
    requirements_completed_total = Column(Integer, nullable=False, server_default=text("0"))
    completion_time_histogram = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
