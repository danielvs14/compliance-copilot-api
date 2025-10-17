from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, String, text
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from .base import Base
from .requirements import RequirementAnchorTypeEnum, RequirementFrequencyEnum


class DocumentTemplate(Base):
    __tablename__ = "document_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    version = Column(String, nullable=False)
    trade = Column(String, nullable=False)
    fingerprint = Column(String, nullable=False, unique=True)
    metadata_json = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    requirement_templates = relationship(
        "RequirementTemplate",
        back_populates="document_template",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class RequirementTemplate(Base):
    __tablename__ = "requirement_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_template_id = Column(
        UUID(as_uuid=True),
        ForeignKey("document_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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
    attributes = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    document_template = relationship("DocumentTemplate", back_populates="requirement_templates")
