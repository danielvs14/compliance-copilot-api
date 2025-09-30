from __future__ import annotations

import io
import logging
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..dependencies.auth import AuthContext, require_auth
from ..dependencies.db import get_db
from ..models.documents import Document
from ..models.events import Event
from ..models.requirements import Requirement, RequirementStatusEnum
from ..services.extraction_pipeline import attach_translations, extract_requirement_drafts
from ..services.metrics import record_requirements_created
from ..services.parse_pdf import extract_text_from_pdf
from ..services.schedule import next_due_from_frequency
from ..services.storage import get_storage_service

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def sanitize_filename(filename: str) -> str:
    name = os.path.basename(filename or "document.pdf")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:100] or "document.pdf"


def parse_due_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


@router.post("/documents/upload")
def upload_and_extract(
    trade: str = Form("electrical"),
    file: UploadFile = File(...),
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    org_uuid = context.org.id

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename required")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail="PDF only.")

    sanitized_name = sanitize_filename(file.filename)
    total_bytes = 0

    buffer = io.BytesIO()

    try:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=400, detail="File too large.")
            buffer.write(chunk)
    finally:
        file.file.close()

    start = datetime.now(timezone.utc)

    pdf_bytes = buffer.getvalue()
    storage = get_storage_service()
    stored_file = storage.upload_fileobj(
        org_uuid,
        io.BytesIO(pdf_bytes),
        filename=sanitized_name,
        content_type=file.content_type or "application/pdf",
    )

    doc = Document(org_id=org_uuid, name=sanitized_name, storage_url=stored_file.storage_url)
    db.add(doc)
    db.flush()

    db.add(
        Event(
            org_id=org_uuid,
            document_id=doc.id,
            type="upload",
            data={"filename": sanitized_name, "bytes": total_bytes, "storage_key": stored_file.key},
        )
    )
    db.flush()

    try:
        text = extract_text_from_pdf(io.BytesIO(pdf_bytes))
        if not text:
            raise HTTPException(status_code=400, detail="Could not extract text from PDF.")
        if len(text) < 200:
            raise HTTPException(status_code=400, detail="Not enough content.")

        doc.text_excerpt = text[:1000]

        drafts = extract_requirement_drafts(text, trade=trade)
        if not drafts:
            raise HTTPException(status_code=500, detail="No requirements extracted.")

        attach_translations(drafts)

        created_payload = []
        requirement_count = 0
        for draft in drafts:
            due_date = parse_due_date(draft.due_date)
            if not due_date and draft.frequency:
                due_date = next_due_from_frequency(draft.frequency)

            status = (
                RequirementStatusEnum.REVIEW
                if draft.confidence < 0.5
                else RequirementStatusEnum.OPEN
            )

            requirement = Requirement(
                org_id=org_uuid,
                document_id=doc.id,
                title_en=draft.title_en,
                title_es=draft.title_es or draft.title_en,
                description_en=draft.description_en,
                description_es=draft.description_es or draft.description_en,
                category=draft.category,
                frequency=draft.frequency,
                due_date=due_date,
                status=status,
                source_ref=draft.source_ref,
                confidence=draft.confidence,
                trade=trade.lower(),
                attributes=draft.attributes | {"origin": draft.origin},
            )
            db.add(requirement)
            db.flush()
            requirement_count += 1

            created_payload.append(
                {
                    "id": str(requirement.id),
                    "title_en": requirement.title_en,
                    "title_es": requirement.title_es,
                    "status": requirement.status.value,
                    "confidence": requirement.confidence,
                    "frequency": requirement.frequency,
                    "due_date": requirement.due_date.isoformat() if requirement.due_date else None,
                    "source_ref": requirement.source_ref,
                }
            )

        doc.extracted_at = datetime.now(timezone.utc)

        record_requirements_created(db, org_uuid, requirement_count)

        latency_ms = int((doc.extracted_at - start).total_seconds() * 1000)
        logger.info(
            "document_extracted document_id=%s org_id=%s requirements=%s latency_ms=%s",
            doc.id,
            org_uuid,
            requirement_count,
            latency_ms,
        )

        db.add(
            Event(
                org_id=org_uuid,
                document_id=doc.id,
                type="extracted",
                data={
                    "document_id": str(doc.id),
                    "requirement_ids": [item["id"] for item in created_payload],
                    "latency_ms": latency_ms,
                },
            )
        )

        db.commit()

    except HTTPException:
        db.rollback()
        try:
            storage.delete(stored_file.key)
        except Exception:  # pragma: no cover - cleanup best effort
            logger.warning("Failed to delete stored file after HTTP error", exc_info=True)
        raise
    except Exception as exc:  # pragma: no cover - keeps API responses clean
        db.rollback()
        try:
            storage.delete(stored_file.key)
        except Exception:  # pragma: no cover - cleanup best effort
            logger.warning("Failed to delete stored file after exception", exc_info=True)
        logger.exception("Document extraction failed")
        raise HTTPException(status_code=500, detail="Extraction failed.") from exc

    return {
        "document_id": str(doc.id),
        "name": doc.name,
        "requirements": created_payload,
        "storage_url": stored_file.storage_url,
        "download_url": stored_file.presigned_url,
    }
