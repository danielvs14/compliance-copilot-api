from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..dependencies.auth import AuthContext, require_auth
from ..dependencies.db import get_db
from ..models.events import Event
from ..models.requirements import Requirement, RequirementStatusEnum
from ..services.metrics import record_requirement_completed
from ..services.reminders import handle_completion_metrics

router = APIRouter()


def serialize_requirement(requirement: Requirement) -> Dict[str, Any]:
    return {
        "id": str(requirement.id),
        "org_id": str(requirement.org_id),
        "document_id": str(requirement.document_id),
        "title_en": requirement.title_en,
        "title_es": requirement.title_es,
        "description_en": requirement.description_en,
        "description_es": requirement.description_es,
        "category": requirement.category,
        "frequency": requirement.frequency,
        "due_date": requirement.due_date.isoformat() if requirement.due_date else None,
        "next_due": requirement.next_due.isoformat() if requirement.next_due else None,
        "status": requirement.status.value if isinstance(requirement.status, RequirementStatusEnum) else requirement.status,
        "source_ref": requirement.source_ref,
        "confidence": requirement.confidence,
        "trade": requirement.trade,
        "attributes": requirement.attributes or {},
        "created_at": requirement.created_at.isoformat() if requirement.created_at else None,
        "completed_at": requirement.completed_at.isoformat() if requirement.completed_at else None,
    }


@router.get("/requirements")
def list_requirements(
    status: RequirementStatusEnum | None = Query(default=None),
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    query = db.query(Requirement).filter(Requirement.org_id == context.org.id)
    if status:
        query = query.filter(Requirement.status == status)

    requirements = query.order_by(Requirement.created_at.desc()).all()
    return [serialize_requirement(req) for req in requirements]


@router.get("/requirements/{req_id}")
def get_requirement(
    req_id: str,
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    try:
        requirement_uuid = uuid.UUID(req_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid requirement id") from exc

    requirement = (
        db.query(Requirement)
        .filter(Requirement.id == requirement_uuid, Requirement.org_id == context.org.id)
        .one_or_none()
    )
    if requirement is None:
        raise HTTPException(status_code=404, detail="Requirement not found")

    return serialize_requirement(requirement)


class CompletePayload(BaseModel):
    completed_by: str | None = None


@router.post("/requirements/{req_id}/complete")
def complete_requirement(
    req_id: str,
    payload: CompletePayload = Body(...),
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    try:
        requirement_uuid = uuid.UUID(req_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid requirement id") from exc

    requirement = (
        db.query(Requirement)
        .filter(Requirement.id == requirement_uuid, Requirement.org_id == context.org.id)
        .one_or_none()
    )
    if requirement is None:
        raise HTTPException(status_code=404, detail="Requirement not found")

    if isinstance(requirement.status, RequirementStatusEnum) and requirement.status == RequirementStatusEnum.DONE:
        return serialize_requirement(requirement)

    requirement.mark_complete()
    db.add(requirement)
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
            },
        )
    )
    db.commit()
    db.refresh(requirement)

    return serialize_requirement(requirement)
