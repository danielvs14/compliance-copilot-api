from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..dependencies.auth import AuthContext, require_auth
from ..dependencies.db import get_db
from ..models import Event, Permit
from ..services.storage import get_storage_service
from ..config import settings

router = APIRouter(prefix="/permits")

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _storage_key_from_url(storage_url: str | None) -> str | None:
    if not storage_url:
        return None
    if storage_url.startswith("s3://"):
        remainder = storage_url[len("s3://") :]
        slash = remainder.find("/")
        if slash == -1:
            return None
        return remainder[slash + 1 :]

    from urllib.parse import urlparse

    parsed = urlparse(storage_url)
    if parsed.scheme == "s3" and parsed.path:
        return parsed.path.lstrip("/")

    if "/" in storage_url and not storage_url.startswith("http"):
        return storage_url.split("/", 1)[1]

    return storage_url


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


def _serialize_permit(permit: Permit, download_url: str | None = None) -> dict:
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
        "download_url": download_url,
        "download_path": f"/permits/{permit.id}/download",
        "created_at": permit.created_at.isoformat() if permit.created_at else None,
        "updated_at": permit.updated_at.isoformat() if getattr(permit, "updated_at", None) else None,
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

    return {"permit": _serialize_permit(permit, download_url=stored.presigned_url), "download_url": stored.presigned_url}


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
    return [_serialize_permit(permit, download_url=None) for permit in permits]


@router.get("/{permit_id}/download")
def download_permit(
    permit_id: str,
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    try:
        permit_uuid = uuid.UUID(permit_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid permit id") from exc

    permit = (
        db.query(Permit)
        .filter(Permit.id == permit_uuid, Permit.org_id == context.org.id)
        .one_or_none()
    )
    if permit is None:
        raise HTTPException(status_code=404, detail="Permit not found")

    key = _storage_key_from_url(permit.storage_url)
    if not key:
        raise HTTPException(status_code=404, detail="Permit storage unavailable")

    candidate_keys = [key]
    if "/" not in key:
        candidate_keys.append(f"{context.org.id}/{key}")

    upload_event = (
        db.query(Event)
        .filter(Event.type == "permit_uploaded", Event.data["permit_id"].astext == str(permit.id))
        .order_by(Event.at.desc())
        .first()
    )
    if upload_event and isinstance(upload_event.data, dict):
        event_key = upload_event.data.get("storage_key")
        if isinstance(event_key, str):
            if event_key.startswith("s3://"):
                normalized = _storage_key_from_url(event_key)
            elif event_key.startswith(f"{settings.aws.s3_bucket}/"):
                normalized = event_key[len(settings.aws.s3_bucket) + 1 :]
            else:
                normalized = event_key
            if normalized and normalized not in candidate_keys:
                candidate_keys.append(normalized)

    logger.debug(
        "permit_download_candidates",
        extra={"permit_id": str(permit.id), "candidates": candidate_keys, "storage_url": permit.storage_url},
    )

    storage = get_storage_service()

    last_error: RuntimeError | None = None
    for candidate in candidate_keys:
        try:
            iterator, metadata, closer = storage.open_stream(candidate)
            break
        except RuntimeError as exc:
            last_error = exc
    else:
        logger.warning(
            "Failed to stream permit",
            extra={"permit_id": permit.id, "storage_url": permit.storage_url},
            exc_info=True,
        )
        status = 404 if last_error and "NoSuchKey" in str(last_error) else 500
        detail = (
            "Permit file not found. Expected keys: " + ", ".join(candidate_keys)
            if status == 404
            else "Unable to download permit"
        )
        raise HTTPException(status_code=status, detail=detail) from last_error

    headers = {
        "Content-Disposition": f'attachment; filename="{permit.name}"',
    }
    if metadata.get("content_length") is not None:
        headers["Content-Length"] = str(metadata["content_length"])

    background = BackgroundTasks()
    background.add_task(closer)
    return StreamingResponse(
        iterator(),
        media_type=metadata.get("content_type") or "application/octet-stream",
        headers=headers,
        background=background,
    )
