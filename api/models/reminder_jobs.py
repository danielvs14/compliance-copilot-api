from __future__ import annotations

from enum import Enum

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .base import Base


class ReminderStatusEnum(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class ReminderJob(Base):
    __tablename__ = "reminder_jobs"
    __table_args__ = (
        UniqueConstraint(
            "target_type",
            "target_id",
            "recipient_email",
            "reminder_offset_days",
            "target_due_at",
            name="uq_reminder_jobs_target_offset",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_type = Column(String, nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)
    target_due_at = Column(DateTime(timezone=True), nullable=True)
    reminder_offset_days = Column(Integer, nullable=False)
    run_at = Column(DateTime(timezone=True), nullable=False, index=True)
    recipient_email = Column(String, nullable=False)
    recipient_locale = Column(String, nullable=False, server_default="en")
    status = Column(
        SAEnum(ReminderStatusEnum, name="reminder_status"),
        nullable=False,
        server_default=ReminderStatusEnum.PENDING.value,
    )
    attempts = Column(Integer, nullable=False, server_default=text("0"))
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String, nullable=True)
    payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
