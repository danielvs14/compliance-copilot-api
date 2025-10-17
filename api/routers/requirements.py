from __future__ import annotations

import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session, joinedload

from ..dependencies.auth import AuthContext, require_auth
from ..dependencies.db import get_db
from ..models.events import Event
from ..models.requirements import (
    Requirement,
    RequirementAnchorTypeEnum,
    RequirementFrequencyEnum,
    RequirementStatusEnum,
)
from ..services.metrics import record_requirement_completed
from ..services.reminders import handle_completion_metrics
from ..services.schedule import compute_next_due

router = APIRouter()

logger = logging.getLogger(__name__)


def serialize_requirement(requirement: Requirement) -> Dict[str, Any]:
    attributes: dict[str, Any] = dict(requirement.attributes or {})
    archive_meta: dict[str, Any] = dict(attributes.get("archive") or {})
    if not archive_meta.get("state") and requirement.status == RequirementStatusEnum.ARCHIVED:
        archive_meta["state"] = "archived"
    return {
        "id": str(requirement.id),
        "org_id": str(requirement.org_id),
        "document_id": str(requirement.document_id) if requirement.document_id else None,
        "document_name": requirement.document.name if getattr(requirement, "document", None) else None,
        "title_en": requirement.title_en,
        "title_es": requirement.title_es,
        "description_en": requirement.description_en,
        "description_es": requirement.description_es,
        "category": requirement.category,
        "frequency": requirement.frequency.value if isinstance(requirement.frequency, RequirementFrequencyEnum) else requirement.frequency,
        "anchor_type": requirement.anchor_type.value if isinstance(getattr(requirement, "anchor_type", None), RequirementAnchorTypeEnum) else getattr(requirement, "anchor_type", None),
        "anchor_value": dict(getattr(requirement, "anchor_value", {}) or {}),
        "due_date": requirement.due_date.isoformat() if requirement.due_date else None,
        "next_due": requirement.next_due.isoformat() if requirement.next_due else None,
        "status": requirement.status.value if isinstance(requirement.status, RequirementStatusEnum) else requirement.status,
        "source_ref": requirement.source_ref,
        "confidence": requirement.confidence,
        "trade": requirement.trade,
        "attributes": attributes,
        "archive_state": archive_meta.get("state"),
        "created_at": requirement.created_at.isoformat() if requirement.created_at else None,
        "completed_at": requirement.completed_at.isoformat() if requirement.completed_at else None,
    }


def _get_requirement_or_404(
    db: Session,
    *,
    org_id: uuid.UUID,
    req_id: str,
) -> Requirement:
    try:
        requirement_uuid = uuid.UUID(req_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid requirement id") from exc

    requirement = (
        db.query(Requirement)
        .options(joinedload(Requirement.document))
        .filter(Requirement.id == requirement_uuid, Requirement.org_id == org_id)
        .one_or_none()
    )
    if requirement is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return requirement


@router.get("/requirements")
def list_requirements(
    status: str | None = Query(default=None),
    due: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=100),
    archived: bool | None = Query(default=None),
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    query = (
        db.query(Requirement)
        .options(joinedload(Requirement.document))
        .filter(Requirement.org_id == context.org.id)
    )

    archive_state_field = Requirement.attributes["archive"]["state"].astext
    archived_states = ("archived", "deleted", "pending")
    if archived:
        query = query.filter(
            or_(
                archive_state_field.in_(archived_states),
                Requirement.status == RequirementStatusEnum.ARCHIVED,
            )
        )
    else:
        query = query.filter(
            and_(
                or_(archive_state_field.is_(None), archive_state_field.notin_(archived_states)),
                Requirement.status != RequirementStatusEnum.ARCHIVED,
            )
        )

    if status:
        requested_statuses: set[RequirementStatusEnum] = set()
        for token in status.split(","):
            token = token.strip().upper()
            if not token:
                continue
            try:
                requested_statuses.add(RequirementStatusEnum(token))
            except ValueError:
                continue
        if requested_statuses:
            query = query.filter(Requirement.status.in_(requested_statuses))
    elif not archived:
        query = query.filter(
            Requirement.status.notin_(
                [RequirementStatusEnum.PENDING_REVIEW, RequirementStatusEnum.ARCHIVED]
            )
        )

    if due:
        now = datetime.now(timezone.utc)
        if due == "overdue":
            query = query.filter(Requirement.due_date.isnot(None), Requirement.due_date < now)
        elif due == "due7":
            query = query.filter(
                Requirement.due_date.isnot(None),
                Requirement.due_date >= now,
                Requirement.due_date <= now + timedelta(days=7),
            )
        elif due == "due30":
            query = query.filter(
                Requirement.due_date.isnot(None),
                Requirement.due_date >= now,
                Requirement.due_date <= now + timedelta(days=30),
            )

    total = query.count()

    requirements = (
        query.order_by(
            Requirement.due_date.asc().nulls_last(),
            Requirement.created_at.desc(),
            Requirement.id.asc(),
        )
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return {
        "items": [serialize_requirement(req) for req in requirements],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
        },
    }


@router.get("/requirements/{req_id}")
def get_requirement(
    req_id: str,
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    requirement = _get_requirement_or_404(db, org_id=context.org.id, req_id=req_id)
    return serialize_requirement(requirement)


class CompletePayload(BaseModel):
    completed_by: str | None = None
    notes: str | None = None
    photo_count: int | None = None


@router.post("/requirements/{req_id}/complete")
def complete_requirement(
    req_id: str,
    payload: CompletePayload = Body(...),
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    requirement = _get_requirement_or_404(db, org_id=context.org.id, req_id=req_id)

    if isinstance(requirement.status, RequirementStatusEnum) and requirement.status == RequirementStatusEnum.DONE:
        return serialize_requirement(requirement)

    try:
        history = requirement.mark_complete(
            completed_by=payload.completed_by,
            notes=payload.notes,
            photo_count=payload.photo_count or 0,
        )
        db.add(requirement)
        db.add(history)
        record_requirement_completed(db, requirement)
        handle_completion_metrics(db, requirement)

        db.add(
            Event(
                org_id=context.org.id,
                document_id=requirement.document_id,
                requirement_id=requirement.id,
                type="completed",
                data={
                    "completed_by": payload.completed_by,
                    "completed_at": requirement.completed_at.isoformat() if requirement.completed_at else None,
                    "notes": payload.notes,
                    "photo_count": payload.photo_count or 0,
                },
            )
        )
        db.commit()
        db.refresh(requirement)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive guardrail
        db.rollback()
        logger.exception("Failed to mark requirement complete")
        raise HTTPException(status_code=500, detail="Unable to complete requirement") from exc

    return serialize_requirement(requirement)


class UpdateRequirementPayload(BaseModel):
    status: RequirementStatusEnum | None = None
    due_date: datetime | None = None


class BulkTriageUpdatePayload(BaseModel):
    requirement_ids: list[str]
    frequency: RequirementFrequencyEnum | None = None
    anchor_type: RequirementAnchorTypeEnum | None = None
    anchor_value: dict[str, Any] | None = None
    due_date: datetime | None = None
    assignee: str | None = None
    status: RequirementStatusEnum | None = None


class ArchiveRequestPayload(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class ArchiveRestorePayload(BaseModel):
    note: str | None = None


@router.patch("/requirements/{req_id}")
def update_requirement(
    req_id: str,
    payload: UpdateRequirementPayload,
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    requirement = _get_requirement_or_404(db, org_id=context.org.id, req_id=req_id)

    updated = False

    if payload.due_date is not None:
        due = payload.due_date
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        due = due.replace(hour=12, minute=0, second=0, microsecond=0)
        requirement.due_date = due
        updated = True

    if payload.status is not None and payload.status != requirement.status:
        requirement.status = payload.status
        updated = True

    if not updated:
        return serialize_requirement(requirement)

    db.add(requirement)
    db.commit()
    db.refresh(requirement)
    return serialize_requirement(requirement)


@router.post("/requirements/triage/bulk")
def bulk_triage_requirements(
    payload: BulkTriageUpdatePayload,
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    if not payload.requirement_ids:
        raise HTTPException(status_code=400, detail="No requirements selected")

    try:
        requirement_ids = [uuid.UUID(value) for value in payload.requirement_ids]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid requirement id") from exc

    requirements = (
        db.query(Requirement)
        .filter(Requirement.org_id == context.org.id, Requirement.id.in_(requirement_ids))
        .all()
    )

    if not requirements:
        raise HTTPException(status_code=404, detail="No matching requirements")

    anchor_value = dict(payload.anchor_value or {})
    now = datetime.now(timezone.utc)

    for requirement in requirements:
        if payload.frequency is not None:
            requirement.frequency = payload.frequency

        if payload.anchor_type is not None:
            requirement.anchor_type = payload.anchor_type

        if payload.anchor_value is not None or payload.anchor_type is not None:
            requirement.anchor_value = dict(anchor_value)

        if payload.due_date is not None:
            due = payload.due_date
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            requirement.due_date = due

        next_due = compute_next_due(
            requirement.frequency,
            requirement.anchor_type,
            requirement.anchor_value,
            reference_time=requirement.due_date or now,
        )
        requirement.next_due = next_due
        if requirement.due_date is None and next_due is not None:
            requirement.due_date = next_due

        attributes = dict(requirement.attributes or {})
        previous_triage = dict(attributes.get("triage") or {})
        if payload.assignee is not None:
            attributes["assignee"] = payload.assignee

        attributes.pop("triage_flags", None)
        attributes["triage"] = {
            "resolved_at": now.isoformat(),
            "resolved_by": context.user.email if context.user else None,
            "reasons": previous_triage.get("reasons", []),
            **({"assignee": payload.assignee} if payload.assignee is not None else {}),
        }
        requirement.attributes = attributes

        if payload.status is not None:
            requirement.status = payload.status
        else:
            requirement.status = RequirementStatusEnum.READY

        db.add(
            Event(
                org_id=context.org.id,
                document_id=requirement.document_id,
                requirement_id=requirement.id,
                type="requirement_triaged",
                data={
                    "frequency": requirement.frequency.value if requirement.frequency else None,
                    "anchor_type": requirement.anchor_type.value if requirement.anchor_type else None,
                    "due_date": requirement.due_date.isoformat() if requirement.due_date else None,
                    "assignee": payload.assignee,
                    "status": requirement.status.value if isinstance(requirement.status, RequirementStatusEnum) else requirement.status,
                },
            )
        )

    db.commit()
    for requirement in requirements:
        db.refresh(requirement)

    return {
        "items": [serialize_requirement(requirement) for requirement in requirements],
        "updated": len(requirements),
    }


@router.post("/requirements/{req_id}/archive")
def request_requirement_archive(
    req_id: str,
    payload: ArchiveRequestPayload,
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    requirement = _get_requirement_or_404(db, org_id=context.org.id, req_id=req_id)

    attributes = dict(requirement.attributes or {})
    archive_meta = dict(attributes.get("archive") or {})

    state = archive_meta.get("state")
    if state == "archived":
        raise HTTPException(status_code=409, detail="Requirement already archived")

    now = datetime.now(timezone.utc)
    user_email = context.user.email if context.user else None
    current_status = (
        requirement.status.value
        if isinstance(requirement.status, RequirementStatusEnum)
        else str(requirement.status)
    )

    archive_meta = {
        "state": "archived",
        "reason": payload.reason,
        "requested_at": now.isoformat(),
        "requested_by": user_email,
        "resolved_at": now.isoformat(),
        "resolved_by": user_email,
        "previous_status": current_status,
    }

    attributes["archive"] = archive_meta
    requirement.attributes = attributes
    requirement.status = RequirementStatusEnum.ARCHIVED

    db.add(requirement)
    db.add(
        Event(
            org_id=context.org.id,
            document_id=requirement.document_id,
            requirement_id=requirement.id,
            type="requirement_archived",
            data={"reason": payload.reason},
        )
    )
    db.commit()
    db.refresh(requirement)
    return serialize_requirement(requirement)


@router.post("/requirements/{req_id}/archive/restore")
def restore_requirement(
    req_id: str,
    payload: ArchiveRestorePayload,
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    requirement = _get_requirement_or_404(db, org_id=context.org.id, req_id=req_id)
    attributes = dict(requirement.attributes or {})
    archive_meta = dict(attributes.get("archive") or {})

    if archive_meta.get("state") != "archived":
        raise HTTPException(status_code=400, detail="Requirement is not archived")

    now = datetime.now(timezone.utc)
    user_email = context.user.email if context.user else None
    previous_status = archive_meta.get("previous_status")

    archive_meta.update(
        {
            "state": "restored",
            "restored_at": now.isoformat(),
            "restored_by": user_email,
        }
    )
    if payload.note:
        archive_meta["note"] = payload.note

    attributes["archive"] = archive_meta
    requirement.attributes = attributes
    if previous_status:
        try:
            requirement.status = RequirementStatusEnum(previous_status)
        except ValueError:
            requirement.status = RequirementStatusEnum.OPEN
    else:
        requirement.status = RequirementStatusEnum.OPEN

    db.add(requirement)
    db.add(
        Event(
            org_id=context.org.id,
            document_id=requirement.document_id,
            requirement_id=requirement.id,
            type="requirement_restored",
            data={"note": payload.note},
        )
    )
    db.commit()
    db.refresh(requirement)
    return serialize_requirement(requirement)
