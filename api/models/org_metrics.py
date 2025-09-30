from sqlalchemy import Column, DateTime, ForeignKey, Integer, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from .base import Base


class OrgRequirementMetrics(Base):
    __tablename__ = "org_requirement_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    requirements_created_total = Column(Integer, nullable=False, server_default=text("0"))
    requirements_completed_total = Column(Integer, nullable=False, server_default=text("0"))
    completion_time_histogram = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    reminders_scheduled_total = Column(Integer, nullable=False, server_default=text("0"))
    reminders_sent_total = Column(Integer, nullable=False, server_default=text("0"))
    reminders_failed_total = Column(Integer, nullable=False, server_default=text("0"))
    overdue_completion_total = Column(Integer, nullable=False, server_default=text("0"))
    post_reminder_completion_total = Column(Integer, nullable=False, server_default=text("0"))
    overdue_completion_histogram = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    post_reminder_completion_histogram = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
