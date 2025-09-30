from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..dependencies.auth import AuthContext, require_auth
from ..dependencies.db import get_db
from ..models import Event, Permit
from ..services.storage import get_storage_service

router = APIRouter(prefix="/permits")

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime format")


def _serialize_permit(permit: Permit) -> dict:
    return {
        "id": str(permit.id),
        "org_id": str(permit.org_id),
        "name": permit.name,
        "permit_number": permit.permit_number,
        "permit_type": permit.permit_type,
        "jurisdiction": permit.jurisdiction,
        "issued_at": permit.issued_at.isoformat() if permit.issued_at else None,
        "expires_at": permit.expires_at.isoformat() if permit.expires_at else None,
        "storage_url": permit.storage_url,
    }


@router.post("/upload")
def upload_permit(
    name: str = Form(...),
    permit_number: str | None = Form(default=None),
    permit_type: str | None = Form(default=None),
    jurisdiction: str | None = Form(default=None),
    issued_at: str | None = Form(default=None),
    expires_at: str | None = Form(default=None),
    file: UploadFile = File(...),
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename required")

    buffer = io.BytesIO()
    total_bytes = 0
    while True:
        chunk = file.file.read(1024 * 1024)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="File too large.")
        buffer.write(chunk)
    buffer.seek(0)

    storage = get_storage_service()
    stored = storage.upload_fileobj(
        context.org.id,
        buffer,
        filename=file.filename,
        content_type=file.content_type or "application/octet-stream",
    )

    permit = Permit(
        org_id=context.org.id,
        name=name,
        permit_number=permit_number,
        permit_type=permit_type,
        jurisdiction=jurisdiction,
        issued_at=_parse_datetime(issued_at),
        expires_at=_parse_datetime(expires_at),
        storage_url=stored.storage_url,
    )
    db.add(permit)
    db.add(
        Event(
            org_id=context.org.id,
            document_id=None,
            requirement_id=None,
            type="permit_uploaded",
            data={"permit_id": str(permit.id), "filename": file.filename, "storage_key": stored.key},
        )
    )
    db.commit()
    db.refresh(permit)

    logger.info(
        "permit_uploaded org_id=%s permit_id=%s storage_key=%s",
        context.org.id,
        permit.id,
        stored.key,
    )

    return {"permit": _serialize_permit(permit), "download_url": stored.presigned_url}


@router.get("")
def list_permits(
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> list[dict]:
    permits = (
        db.query(Permit)
        .filter(Permit.org_id == context.org.id)
        .order_by(Permit.created_at.desc())
        .all()
    )
    return [_serialize_permit(permit) for permit in permits]
