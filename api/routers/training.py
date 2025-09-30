from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..dependencies.auth import AuthContext, require_auth
from ..dependencies.db import get_db
from ..models import Event, TrainingCert
from ..services.storage import get_storage_service

router = APIRouter(prefix="/training")

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


def _serialize_cert(cert: TrainingCert) -> dict:
    return {
        "id": str(cert.id),
        "org_id": str(cert.org_id),
        "worker_name": cert.worker_name,
        "certification_type": cert.certification_type,
        "authority": cert.authority,
        "issued_at": cert.issued_at.isoformat() if cert.issued_at else None,
        "expires_at": cert.expires_at.isoformat() if cert.expires_at else None,
        "storage_url": cert.storage_url,
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

    return {"training_cert": _serialize_cert(cert), "download_url": stored.presigned_url}


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
    return [_serialize_cert(cert) for cert in certs]
