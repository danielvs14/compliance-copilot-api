from enum import Enum
from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base


class RequirementStatusEnum(str, Enum):
    OPEN = "OPEN"
    REVIEW = "REVIEW"
    PENDING_REVIEW = "PENDING_REVIEW"
    READY = "READY"
    DONE = "DONE"
    ARCHIVED = "ARCHIVED"


class RequirementFrequencyEnum(str, Enum):
    ONE_TIME = "ONE_TIME"
    BEFORE_EACH_USE = "BEFORE_EACH_USE"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"
    EVERY_N_DAYS = "EVERY_N_DAYS"
    EVERY_N_WEEKS = "EVERY_N_WEEKS"
    EVERY_N_MONTHS = "EVERY_N_MONTHS"


class RequirementAnchorTypeEnum(str, Enum):
    UPLOAD_DATE = "UPLOAD_DATE"
    ISSUE_DATE = "ISSUE_DATE"
    CALENDAR = "CALENDAR"
    FIRST_COMPLETION = "FIRST_COMPLETION"
    CUSTOM_DATE = "CUSTOM_DATE"


class Requirement(Base):
    __tablename__ = "requirements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id = Column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    title_en = Column(String, nullable=False)
    title_es = Column(String, nullable=False)
    description_en = Column(String, nullable=False)
    description_es = Column(String, nullable=False)
    category = Column(String, nullable=True)
    frequency = Column(
        SAEnum(RequirementFrequencyEnum, name="requirement_frequency"), nullable=True
    )
    anchor_type = Column(
        SAEnum(RequirementAnchorTypeEnum, name="requirement_anchor_type"), nullable=True
    )
    anchor_value = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    due_date = Column(DateTime(timezone=True), nullable=True)
    next_due = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        SAEnum(RequirementStatusEnum, name="requirement_status"),
        nullable=False,
        server_default=RequirementStatusEnum.OPEN.value,
    )
    source_ref = Column(String, nullable=False)
    confidence = Column(Float, nullable=False, default=0.7)
    trade = Column(String, nullable=False, default="electrical")
    attributes = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    document = relationship("Document", back_populates="requirements", lazy="joined")
    history_entries = relationship(
        "RequirementHistory",
        back_populates="requirement",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def mark_complete(
        self,
        *,
        completed_by: str | None = None,
        notes: str | None = None,
        photo_count: int = 0,
        completed_at: datetime | None = None,
    ) -> "RequirementHistory":
        from ..services.schedule import RecurrenceError, compute_next_due

        timestamp = completed_at or datetime.now(timezone.utc)
        timestamp = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)

        history = RequirementHistory(
            requirement=self,
            completed_by=completed_by,
            completed_at=timestamp,
            notes=notes,
            photo_count=photo_count or 0,
        )
        self.history_entries.append(history)

        anchor_value = dict(self.anchor_value or {})
        if (
            self.anchor_type == RequirementAnchorTypeEnum.FIRST_COMPLETION
            and "date" not in anchor_value
        ):
            anchor_value["date"] = timestamp.isoformat()
            self.anchor_value = anchor_value
        elif not anchor_value:
            self.anchor_value = anchor_value

        try:
            next_due = compute_next_due(
                self.frequency,
                self.anchor_type,
                self.anchor_value or {},
                last_completion=timestamp,
                reference_time=timestamp,
            )
        except RecurrenceError:
            next_due = None

        self.completed_at = timestamp

        if self.frequency in (None, RequirementFrequencyEnum.ONE_TIME):
            self.status = RequirementStatusEnum.DONE
            self.next_due = None
            self.due_date = None
        else:
            self.next_due = next_due
            self.due_date = next_due
            if self.frequency == RequirementFrequencyEnum.BEFORE_EACH_USE:
                self.status = RequirementStatusEnum.READY
            else:
                self.status = RequirementStatusEnum.READY if next_due else RequirementStatusEnum.DONE

        return history


class RequirementHistory(Base):
    __tablename__ = "requirement_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requirement_id = Column(
        UUID(as_uuid=True),
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    completed_by = Column(String, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    notes = Column(Text, nullable=True)
    photo_count = Column(Integer, nullable=False, server_default=text("0"))

    requirement = relationship("Requirement", back_populates="history_entries", lazy="joined")
