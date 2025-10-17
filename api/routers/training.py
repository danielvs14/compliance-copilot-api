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
from ..models import Event, TrainingCert
from ..services.storage import get_storage_service
from ..config import settings

router = APIRouter(prefix="/training")

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


def _serialize_cert(cert: TrainingCert, download_url: str | None = None) -> dict:
    return {
        "id": str(cert.id),
        "org_id": str(cert.org_id),
        "worker_name": cert.worker_name,
        "certification_type": cert.certification_type,
        "authority": cert.authority,
        "issued_at": cert.issued_at.isoformat() if cert.issued_at else None,
        "expires_at": cert.expires_at.isoformat() if cert.expires_at else None,
        "storage_url": cert.storage_url,
        "download_url": download_url,
        "download_path": f"/training/{cert.id}/download",
        "created_at": cert.created_at.isoformat() if cert.created_at else None,
        "updated_at": cert.updated_at.isoformat() if getattr(cert, "updated_at", None) else None,
    }


@router.post("/upload")
def upload_training_cert(
    worker_name: str = Form(...),
    certification_type: str = Form(...),
    authority: str | None = Form(default=None),
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
        content_type=file.content_type or "application/pdf",
    )

    cert = TrainingCert(
        org_id=context.org.id,
        worker_name=worker_name,
        certification_type=certification_type,
        authority=authority,
        issued_at=_parse_datetime(issued_at),
        expires_at=_parse_datetime(expires_at),
        storage_url=stored.storage_url,
    )
    db.add(cert)
    db.add(
        Event(
            org_id=context.org.id,
            document_id=None,
            requirement_id=None,
            type="training_cert_uploaded",
            data={"training_cert_id": str(cert.id), "filename": file.filename, "storage_key": stored.key},
        )
    )
    db.commit()
    db.refresh(cert)

    logger.info(
        "training_cert_uploaded org_id=%s cert_id=%s storage_key=%s",
        context.org.id,
        cert.id,
        stored.key,
    )

    return {"training_cert": _serialize_cert(cert, download_url=stored.presigned_url), "download_url": stored.presigned_url}


@router.get("")
def list_training_certs(
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> list[dict]:
    certs = (
        db.query(TrainingCert)
        .filter(TrainingCert.org_id == context.org.id)
        .order_by(TrainingCert.created_at.desc())
        .all()
    )
    return [_serialize_cert(cert, download_url=None) for cert in certs]


@router.get("/{cert_id}/download")
def download_training_cert(
    cert_id: str,
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    try:
        cert_uuid = uuid.UUID(cert_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid training id") from exc

    cert = (
        db.query(TrainingCert)
        .filter(TrainingCert.id == cert_uuid, TrainingCert.org_id == context.org.id)
        .one_or_none()
    )
    if cert is None:
        raise HTTPException(status_code=404, detail="Training cert not found")

    key = _storage_key_from_url(cert.storage_url)
    if not key:
        raise HTTPException(status_code=404, detail="Training storage unavailable")

    candidate_keys = [key]
    if "/" not in key:
        candidate_keys.append(f"{context.org.id}/{key}")

    upload_event = (
        db.query(Event)
        .filter(Event.type == "training_cert_uploaded", Event.data["training_cert_id"].astext == str(cert.id))
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
        "training_download_candidates",
        extra={"training_cert_id": str(cert.id), "candidates": candidate_keys, "storage_url": cert.storage_url},
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
            "Failed to stream training cert",
            extra={"training_cert_id": cert.id, "storage_url": cert.storage_url},
            exc_info=True,
        )
        status = 404 if last_error and "NoSuchKey" in str(last_error) else 500
        detail = (
            "Training certificate not found. Expected keys: " + ", ".join(candidate_keys)
            if status == 404
            else "Unable to download training cert"
        )
        raise HTTPException(status_code=status, detail=detail) from last_error

    headers = {
        "Content-Disposition": f'attachment; filename="{cert.worker_name}.pdf"',
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
