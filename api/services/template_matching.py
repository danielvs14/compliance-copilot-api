from __future__ import annotations

import hashlib
import re
from typing import Iterable

from sqlalchemy.orm import Session, joinedload

from ..models import Document, DocumentTemplate, Requirement, RequirementStatusEnum
from ..models.requirements import RequirementAnchorTypeEnum, RequirementFrequencyEnum
from ..models.templates import RequirementTemplate
from .schedule import compute_next_due


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    cleaned = _WHITESPACE_RE.sub(" ", text.strip().lower())
    return cleaned[:5000]


def compute_fingerprint(text: str) -> str:
    """Return a deterministic fingerprint for known-document matching."""
    if not text:
        return ""
    normalized = _normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def lookup_document_template(db: Session, fingerprint: str) -> DocumentTemplate | None:
    if not fingerprint:
        return None
    return (
        db.query(DocumentTemplate)
        .options(joinedload(DocumentTemplate.requirement_templates))
        .filter(DocumentTemplate.fingerprint == fingerprint)
        .one_or_none()
    )


def _to_requirement(template: RequirementTemplate, *, document: Document) -> Requirement:
    frequency: RequirementFrequencyEnum | None = template.frequency
    anchor_type: RequirementAnchorTypeEnum | None = template.anchor_type
    anchor_value = dict(template.anchor_value or {})

    reference_time = document.created_at
    next_due = compute_next_due(
        frequency,
        anchor_type,
        anchor_value,
        reference_time=reference_time,
    )

    attributes = dict(template.attributes or {})
    attributes.update({
        "origin": "template",
        "template_id": str(template.id),
        "document_template_id": str(template.document_template_id),
    })

    requirement = Requirement(
        org_id=document.org_id,
        document_id=document.id,
        title_en=template.title_en,
        title_es=template.title_es,
        description_en=template.description_en,
        description_es=template.description_es,
        category=template.category,
        frequency=frequency,
        anchor_type=anchor_type,
        anchor_value=anchor_value,
        due_date=next_due,
        next_due=next_due,
        status=RequirementStatusEnum.READY,
        source_ref=f"template:{template.id}",
        confidence=1.0,
        trade=(template.document_template.trade or "general").lower(),
        attributes=attributes,
    )
    return requirement


def instantiate_from_template(
    db: Session,
    *,
    template: DocumentTemplate,
    document: Document,
) -> Iterable[Requirement]:
    requirements = [_to_requirement(req_template, document=document) for req_template in template.requirement_templates]
    for requirement in requirements:
        db.add(requirement)
    return requirements
