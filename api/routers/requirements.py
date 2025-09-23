from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db.session import SessionLocal
from ..models.events import Event
from ..models.requirements import Requirement, RequirementStatusEnum
from ..services.metrics import record_requirement_completed

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
        "status": requirement.status.value if isinstance(requirement.status, RequirementStatusEnum) else requirement.status,
        "source_ref": requirement.source_ref,
        "confidence": requirement.confidence,
        "trade": requirement.trade,
        "attributes": requirement.attributes or {},
        "created_at": requirement.created_at.isoformat() if requirement.created_at else None,
        "completed_at": requirement.completed_at.isoformat() if requirement.completed_at else None,
    }


def parse_org_id(org_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(org_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid org_id") from exc


@router.get("/requirements")
def list_requirements(
    org_id: str = Query(...),
    status: RequirementStatusEnum | None = Query(default=None),
    db: Session = Depends(get_db),
):
    org_uuid = parse_org_id(org_id)

    query = db.query(Requirement).filter(Requirement.org_id == org_uuid)
    if status:
        query = query.filter(Requirement.status == status)

    requirements = query.order_by(Requirement.created_at.desc()).all()
    return [serialize_requirement(req) for req in requirements]


@router.get("/requirements/{req_id}")
def get_requirement(req_id: str, org_id: str = Query(...), db: Session = Depends(get_db)):
    org_uuid = parse_org_id(org_id)
    try:
        requirement_uuid = uuid.UUID(req_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid requirement id") from exc

    requirement = (
        db.query(Requirement)
        .filter(Requirement.id == requirement_uuid, Requirement.org_id == org_uuid)
        .one_or_none()
    )
    if requirement is None:
        raise HTTPException(status_code=404, detail="Requirement not found")

    return serialize_requirement(requirement)


class CompletePayload(BaseModel):
    org_id: uuid.UUID
    completed_by: str | None = None


@router.post("/requirements/{req_id}/complete")
def complete_requirement(req_id: str, payload: CompletePayload = Body(...), db: Session = Depends(get_db)):
    try:
        requirement_uuid = uuid.UUID(req_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid requirement id") from exc

    requirement = (
        db.query(Requirement)
        .filter(Requirement.id == requirement_uuid, Requirement.org_id == payload.org_id)
        .one_or_none()
    )
    if requirement is None:
        raise HTTPException(status_code=404, detail="Requirement not found")

    if isinstance(requirement.status, RequirementStatusEnum) and requirement.status == RequirementStatusEnum.DONE:
        return serialize_requirement(requirement)

    requirement.mark_complete()
    db.add(requirement)
    record_requirement_completed(db, requirement)

    db.add(
        Event(
            org_id=requirement.org_id,
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
