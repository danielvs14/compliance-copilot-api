from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..dependencies.auth import AuthContext, require_auth
from ..dependencies.db import get_db
from ..db.session import SessionLocal
from ..config import settings
from ..models.documents import Document
from ..models.events import Event
from ..models.requirements import (
    Requirement,
    RequirementAnchorTypeEnum,
    RequirementFrequencyEnum,
    RequirementStatusEnum,
)
from ..models.permits import Permit
from ..models.training_certs import TrainingCert
from ..services.classify import classify_document, get_override_for_hash, record_override_event
from ..services.extraction_pipeline import attach_translations, extract_requirement_drafts
from ..services.metrics import record_requirements_created
from ..services.parse_pdf import extract_text_from_pdf
from ..services.schedule import RecurrenceError, compute_next_due
from ..services.template_matching import (
    compute_fingerprint,
    instantiate_from_template,
    lookup_document_template,
)
from ..services.storage import get_storage_service

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

DocumentStatus = str


class DocumentProcessingError(Exception):
    """Raised when background processing fails in a recoverable way."""


def _storage_key_from_url(storage_url: str | None) -> str | None:
    if not storage_url:
        return None

    if storage_url.startswith("s3://"):
        _, _, remainder = storage_url.partition("s3://")
        bucket_sep = remainder.find("/")
        if bucket_sep == -1:
            return None
        return remainder[bucket_sep + 1 :]

    from urllib.parse import urlparse

    parsed_url = urlparse(storage_url)
    if parsed_url.scheme == "s3" and parsed_url.path:
        return parsed_url.path.lstrip("/")

    # Support raw "bucket/key" strings
    if "/" in storage_url:
        return storage_url.split("/", 1)[1]

    return storage_url


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


_GENERIC_DATE_PATTERN = r"(\d{4}[\-/]\d{1,2}[\-/]\d{1,2}|\d{1,2}[\-/]\d{1,2}[\-/]\d{2,4})"

_PERMIT_ISSUE_PATTERNS = (
    re.compile(rf"(?:issued|issue date|date issued|effective(?: date)?)[:\s,-]*{_GENERIC_DATE_PATTERN}", re.IGNORECASE),
    re.compile(rf"(?:start date|approval date|authorized on)[:\s,-]*{_GENERIC_DATE_PATTERN}", re.IGNORECASE),
)

_PERMIT_EXPIRY_PATTERNS = (
    re.compile(rf"(?:expires|expiration(?: date)?|expiry(?: date)?|valid until|valid thru|valid through)[:\s,-]*{_GENERIC_DATE_PATTERN}", re.IGNORECASE),
    re.compile(rf"(?:good thru|good through|expires on)[:\s,-]*{_GENERIC_DATE_PATTERN}", re.IGNORECASE),
)

_TRAINING_ISSUE_PATTERNS = (
    re.compile(rf"(?:completed on|completion date|issued(?: on)?|training date)[:\s,-]*{_GENERIC_DATE_PATTERN}", re.IGNORECASE),
)


def _parse_fuzzy_date(value: str | None) -> datetime | None:
    if not value:
        return None
    token = value.strip()
    match = re.search(_GENERIC_DATE_PATTERN, token)
    if not match:
        return None
    token = match.group(0)
    token = token.replace(".", "/").replace(" ", "")
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(token, fmt)
            if "%y" in fmt and parsed.year < 2000:
                parsed = parsed.replace(year=parsed.year + 2000)
            parsed = parsed.replace(hour=12, minute=0, second=0, microsecond=0)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_date_from_patterns(text: str, patterns: Tuple[re.Pattern[str], ...]) -> datetime | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            token = match.group(0)
            parsed = _parse_fuzzy_date(token)
            if parsed:
                return parsed
    return None


def _extract_permit_dates(text: str) -> tuple[datetime | None, datetime | None]:
    issued = _extract_date_from_patterns(text, _PERMIT_ISSUE_PATTERNS)
    expires = _extract_date_from_patterns(text, _PERMIT_EXPIRY_PATTERNS)
    return issued, expires


def _extract_training_dates(text: str) -> tuple[datetime | None, datetime | None]:
    issued = _extract_date_from_patterns(text, _TRAINING_ISSUE_PATTERNS)
    expires = _extract_date_from_patterns(text, _PERMIT_EXPIRY_PATTERNS)
    return issued, expires


def _serialize_document(
    document: Document,
    requirement_count: int,
    status: DocumentStatus,
    download_url: Optional[str],
    classification: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "id": str(document.id),
        "name": document.name,
        "storage_url": document.storage_url,
        "download_url": download_url,
        "download_path": f"/documents/{document.id}/download",
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "extracted_at": document.extracted_at.isoformat() if document.extracted_at else None,
        "requirement_count": int(requirement_count or 0),
        "status": status,
        "classification": classification,
    }


def _document_status(db: Session, document: Document) -> DocumentStatus:
    if document.extracted_at:
        return "READY"
    failure_event = (
        db.query(Event)
        .filter(Event.document_id == document.id, Event.type == "extraction_failed")
        .order_by(Event.at.desc())
        .first()
    )
    if failure_event:
        return "FAILED"
    return "PROCESSING"


def _document_classification(db: Session, document: Document) -> Optional[Dict[str, Any]]:
    event = (
        db.query(Event)
        .filter(Event.document_id == document.id, Event.type == "classified")
        .order_by(Event.at.desc())
        .first()
    )
    if not event or not isinstance(event.data, dict):
        return None
    label = event.data.get("label")
    confidence = event.data.get("confidence")
    if not isinstance(label, str):
        return None
    return {
        "label": label,
        "confidence": float(confidence) if isinstance(confidence, (int, float)) else None,
        "source": event.data.get("source", "auto"),
    }



def _get_document_file_hash(db: Session, document_id: uuid.UUID) -> Optional[str]:
    event = (
        db.query(Event)
        .filter(Event.document_id == document_id, Event.type == "upload")
        .order_by(Event.at.desc())
        .first()
    )
    if event and isinstance(event.data, dict):
        file_hash = event.data.get("file_hash")
        if isinstance(file_hash, str):
            return file_hash
    return None


@router.post("/documents/upload", status_code=202)
def upload_and_extract(
    background_tasks: BackgroundTasks,
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

    pdf_bytes = buffer.getvalue()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    file_hash = hashlib.sha256(pdf_bytes).hexdigest()

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
            data={
                "filename": sanitized_name,
                "bytes": total_bytes,
                "storage_key": stored_file.key,
                "file_hash": file_hash,
            },
        )
    )
    db.flush()

    try:
        validation_text = extract_text_from_pdf(io.BytesIO(pdf_bytes))
    except HTTPException:
        db.rollback()
        try:
            storage.delete(stored_file.key)
        except Exception:  # pragma: no cover
            logger.warning("Failed to delete stored file after validation error", exc_info=True)
        raise
    except Exception as exc:  # pragma: no cover - defensive
        db.rollback()
        try:
            storage.delete(stored_file.key)
        except Exception:  # pragma: no cover
            logger.warning("Failed to delete stored file after exception", exc_info=True)
        logger.exception("PDF validation failed")
        raise HTTPException(status_code=500, detail="Extraction failed.") from exc

    if not validation_text or len(validation_text) < 200:
        db.rollback()
        try:
            storage.delete(stored_file.key)
        except Exception:  # pragma: no cover
            logger.warning("Failed to delete stored file after short PDF", exc_info=True)
        raise HTTPException(status_code=400, detail="Not enough content.")

    db.commit()

    background_tasks.add_task(
        _process_document_background,
        str(doc.id),
        str(org_uuid),
        trade.lower(),
        sanitized_name,
        pdf_bytes,
        stored_file.key,
        file_hash,
        validation_text,
    )

    return {"id": str(doc.id), "status": "PROCESSING"}


@router.get("/documents")
def list_documents(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=5, ge=1, le=100),
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    base_query = (
        db.query(
            Document,
            func.count(Requirement.id).label("requirement_count"),
        )
        .outerjoin(Requirement, Requirement.document_id == Document.id)
        .filter(Document.org_id == context.org.id)
        .group_by(Document.id)
    )

    total = (
        db.query(func.count(Document.id))
        .filter(Document.org_id == context.org.id)
        .scalar()
        or 0
    )
    offset = (page - 1) * limit
    rows = (
        base_query.order_by(Document.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items: list[Dict[str, Any]] = []
    for document, requirement_count in rows:
        storage_key = _storage_key_from_url(document.storage_url)
        status = _document_status(db, document)
        classification = _document_classification(db, document)
        items.append(
            _serialize_document(document, requirement_count, status, None, classification)
        )

    return {
        "items": items,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": (total + limit - 1) // limit if total else 0,
        },
    }


@router.get("/documents/{doc_id}")
def get_document(
    doc_id: str,
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    try:
        document_uuid = uuid.UUID(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document id") from exc

    document = (
        db.query(Document)
        .filter(Document.id == document_uuid, Document.org_id == context.org.id)
        .one_or_none()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    requirement_count = (
        db.query(func.count(Requirement.id))
        .filter(Requirement.document_id == document.id)
        .scalar()
        or 0
    )

    status = _document_status(db, document)
    classification = _document_classification(db, document)
    return _serialize_document(document, requirement_count, status, None, classification)


@router.post("/documents/{doc_id}/move")
def move_document(
    doc_id: str,
    payload: Dict[str, str] = Body(...),
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    try:
        document_uuid = uuid.UUID(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document id") from exc

    document = (
        db.query(Document)
        .filter(Document.id == document_uuid, Document.org_id == context.org.id)
        .one_or_none()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    target = (payload or {}).get("target", "").lower()
    if target not in {"requirements", "permit", "training", "uncategorized"}:
        raise HTTPException(status_code=400, detail="Unsupported classification target")

    file_hash = _get_document_file_hash(db, document.id)
    if not file_hash:
        raise HTTPException(status_code=409, detail="Document hash unavailable")

    record_override_event(db, context.org.id, document.id, file_hash, target)

    db.add(
        Event(
            org_id=context.org.id,
            document_id=document.id,
            requirement_id=None,
            type="classified",
            data={
                "label": target,
                "confidence": 1.0,
                "source": "override",
                "file_hash": file_hash,
            },
        )
    )

    if target == "permit":
        issued_at, expires_at = _extract_permit_dates(document.text_excerpt or "")
        _ensure_permit_record(db, context.org.id, document, issued_at, expires_at)
    elif target == "training":
        issued_at, expires_at = _extract_training_dates(document.text_excerpt or "")
        _ensure_training_record(db, context.org.id, document, issued_at, expires_at)

    db.commit()

    requirement_count = (
        db.query(func.count(Requirement.id))
        .filter(Requirement.document_id == document.id)
        .scalar()
        or 0
    )

    status = _document_status(db, document)
    classification = _document_classification(db, document)
    return _serialize_document(document, requirement_count, status, None, classification)


@router.get("/documents/{doc_id}/download")
def download_document(
    doc_id: str,
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    try:
        document_uuid = uuid.UUID(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document id") from exc

    document = (
        db.query(Document)
        .filter(Document.id == document_uuid, Document.org_id == context.org.id)
        .one_or_none()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    storage_key = _storage_key_from_url(document.storage_url)
    if not storage_key:
        raise HTTPException(status_code=404, detail="Document storage unavailable")

    candidate_keys = [storage_key]
    if "/" not in storage_key:
        candidate_keys.append(f"{context.org.id}/{storage_key}")

    upload_event = (
        db.query(Event)
        .filter(Event.document_id == document.id, Event.type == "upload")
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
        "document_download_candidates",
        extra={"document_id": str(document.id), "candidates": candidate_keys, "storage_url": document.storage_url},
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
            "Failed to stream document", extra={"document_id": document.id, "storage_url": document.storage_url}, exc_info=True
        )
        status = 404 if last_error and "NoSuchKey" in str(last_error) else 500
        detail = (
            "Document not found. Expected keys: " + ", ".join(candidate_keys)
            if status == 404
            else "Unable to download document"
        )
        raise HTTPException(status_code=status, detail=detail) from last_error

    headers = {
        "Content-Disposition": f'attachment; filename="{document.name}"',
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


def _ensure_permit_record(
    db: Session,
    org_id: uuid.UUID,
    document: Document,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> Permit:
    existing = (
        db.query(Permit)
        .filter(Permit.org_id == org_id, Permit.storage_url == document.storage_url)
        .first()
    )
    if existing:
        updated = False
        if issued_at and not existing.issued_at:
            existing.issued_at = issued_at
            updated = True
        if expires_at and not existing.expires_at:
            existing.expires_at = expires_at
            updated = True
        if updated:
            db.add(existing)
        return existing
    permit = Permit(
        org_id=org_id,
        name=document.name,
        storage_url=document.storage_url,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    db.add(permit)
    return permit


def _ensure_training_record(
    db: Session,
    org_id: uuid.UUID,
    document: Document,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> TrainingCert:
    existing = (
        db.query(TrainingCert)
        .filter(TrainingCert.org_id == org_id, TrainingCert.storage_url == document.storage_url)
        .first()
    )
    if existing:
        updated = False
        if issued_at and not existing.issued_at:
            existing.issued_at = issued_at
            updated = True
        if expires_at and not existing.expires_at:
            existing.expires_at = expires_at
            updated = True
        if updated:
            db.add(existing)
        return existing
    worker_name = document.name.rsplit(".", 1)[0]
    cert = TrainingCert(
        org_id=org_id,
        worker_name=worker_name or "Uploaded Training",
        certification_type="Uploaded certification",
        storage_url=document.storage_url,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    db.add(cert)
    return cert


def _process_document_background(
    document_id: str,
    org_id: str,
    trade: str,
    filename: str,
    pdf_bytes: bytes,
    storage_key: str,
    file_hash: str,
    initial_text: str | None = None,
) -> None:
    try:
        document_uuid = uuid.UUID(document_id)
        org_uuid = uuid.UUID(org_id)
    except ValueError:
        logger.exception("Invalid UUID for document processing", extra={"document_id": document_id, "org_id": org_id})
        return

    db = SessionLocal()
    storage = get_storage_service()
    try:
        document = (
            db.query(Document)
            .filter(Document.id == document_uuid, Document.org_id == org_uuid)
            .one_or_none()
        )
        if document is None:
            logger.warning("Document not found for processing", extra={"document_id": document_uuid})
            return

        try:
            _run_document_pipeline(
                db,
                document,
                org_uuid,
                trade,
                filename,
                pdf_bytes,
                storage_key,
                file_hash,
                initial_text,
            )
            db.commit()
        except DocumentProcessingError as exc:
            db.rollback()
            logger.warning("Document processing failed", extra={"document_id": document_uuid, "reason": str(exc)})
            _mark_document_failed(db, document, org_uuid, storage_key, str(exc))
            db.commit()
            try:
                storage.delete(storage_key)
            except Exception:  # pragma: no cover - best effort cleanup
                logger.warning("Failed to delete stored file after processing error", exc_info=True)
        except Exception as exc:  # pragma: no cover - unexpected failure
            db.rollback()
            logger.exception("Unexpected document processing failure")
            _mark_document_failed(db, document, org_uuid, storage_key, "Unexpected failure")
            db.commit()
            try:
                storage.delete(storage_key)
            except Exception:  # pragma: no cover
                logger.warning("Failed to delete stored file after unexpected error", exc_info=True)
    finally:
        db.close()


def _run_document_pipeline(
    db: Session,
    document: Document,
    org_id: uuid.UUID,
    trade: str,
    filename: str,
    pdf_bytes: bytes,
    storage_key: str,
    file_hash: str,
    initial_text: str | None = None,
) -> None:
    text = initial_text or extract_text_from_pdf(io.BytesIO(pdf_bytes))
    if not text:
        raise DocumentProcessingError("Could not extract text from PDF.")
    if len(text) < 200:
        raise DocumentProcessingError("Not enough content.")

    document.text_excerpt = text[:1000]

    fingerprint = compute_fingerprint(text)
    template = lookup_document_template(db, fingerprint)

    if template and template.requirement_templates:
        requirements = list(
            instantiate_from_template(
                db,
                template=template,
                document=document,
            )
        )
        if not requirements:
            raise DocumentProcessingError("Known document template has no requirements configured.")

        db.flush()

        document.extracted_at = datetime.now(timezone.utc)

        record_requirements_created(db, org_id, len(requirements))

        requirement_ids = [str(req.id) for req in requirements]
        latency_ms = (
            int((document.extracted_at - document.created_at).total_seconds() * 1000)
            if document.created_at and document.extracted_at
            else 0
        )

        db.add(
            Event(
                org_id=org_id,
                document_id=document.id,
                type="template_matched",
                data={
                    "document_id": str(document.id),
                    "document_template_id": str(template.id),
                    "fingerprint": fingerprint,
                    "requirement_ids": requirement_ids,
                    "storage_key": storage_key,
                },
            )
        )
        db.add(
            Event(
                org_id=org_id,
                document_id=document.id,
                type="extracted",
                data={
                    "document_id": str(document.id),
                    "requirement_ids": requirement_ids,
                    "latency_ms": latency_ms,
                    "storage_key": storage_key,
                    "source": "template",
                },
            )
        )
        return

    classification = classify_document(text, filename=filename)
    override_label = get_override_for_hash(db, org_id, file_hash)

    final_label = override_label or classification.label
    final_confidence = 1.0 if override_label else classification.confidence

    db.add(
        Event(
            org_id=org_id,
            document_id=document.id,
            type="classified",
            data={
                "label": final_label,
                "confidence": final_confidence,
                "matches": classification.matches,
                "source": "override" if override_label else "auto",
                "file_hash": file_hash,
            },
        )
    )

    if final_label == "permit":
        issued_at, expires_at = _extract_permit_dates(text)
        _ensure_permit_record(db, org_id, document, issued_at, expires_at)
    elif final_label == "training":
        issued_at, expires_at = _extract_training_dates(text)
        _ensure_training_record(db, org_id, document, issued_at, expires_at)

    drafts = extract_requirement_drafts(text, trade=trade)
    if not drafts:
        raise DocumentProcessingError("No requirements extracted.")

    attach_translations(drafts)

    created_payload = []
    requirement_count = 0
    for draft in drafts:
        due_date = parse_due_date(draft.due_date)

        frequency = getattr(draft, "frequency", None)
        if isinstance(frequency, str):
            try:
                frequency = RequirementFrequencyEnum(frequency)
            except ValueError:
                frequency = RequirementFrequencyEnum(frequency.upper())

        anchor_type = getattr(draft, "anchor_type", None)
        if isinstance(anchor_type, str):
            try:
                anchor_type = RequirementAnchorTypeEnum(anchor_type)
            except ValueError:
                anchor_type = RequirementAnchorTypeEnum(anchor_type.upper())
        anchor_value = dict(getattr(draft, "anchor_value", {}) or {})

        if anchor_type is None:
            if due_date is not None:
                anchor_type = RequirementAnchorTypeEnum.CUSTOM_DATE
                anchor_value.setdefault("date", due_date.isoformat())
            else:
                created_reference = document.created_at or datetime.now(timezone.utc)
                anchor_type = RequirementAnchorTypeEnum.UPLOAD_DATE
                anchor_value.setdefault("date", created_reference.isoformat())

        reference_time = document.created_at or datetime.now(timezone.utc)
        try:
            next_due = compute_next_due(
                frequency,
                anchor_type,
                anchor_value,
                reference_time=reference_time,
            )
        except RecurrenceError:
            next_due = None

        if due_date is None:
            due_date = next_due

        triage_reasons = list(draft.attributes.get("triage_flags") or [])
        if draft.confidence < 0.5:
            triage_reasons.append("low_confidence")

        if (
            frequency
            and frequency not in {RequirementFrequencyEnum.BEFORE_EACH_USE, RequirementFrequencyEnum.ONE_TIME}
            and due_date is None
        ):
            triage_reasons.append("missing_due_date")

        status = (
            RequirementStatusEnum.PENDING_REVIEW
            if triage_reasons
            else RequirementStatusEnum.OPEN
        )

        requirement = Requirement(
            org_id=org_id,
            document_id=document.id,
            title_en=draft.title_en,
            title_es=draft.title_es or draft.title_en,
            description_en=draft.description_en,
            description_es=draft.description_es or draft.description_en,
            category=draft.category,
            frequency=frequency,
            anchor_type=anchor_type,
            anchor_value=anchor_value,
            due_date=due_date,
            next_due=next_due,
            status=status,
            source_ref=draft.source_ref,
            confidence=draft.confidence,
            trade=trade.lower(),
            attributes=draft.attributes | {
                "origin": draft.origin,
                **({"triage": {"reasons": triage_reasons}} if triage_reasons else {}),
            },
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
                "frequency": requirement.frequency.value if requirement.frequency else None,
                "due_date": requirement.due_date.isoformat() if requirement.due_date else None,
                "source_ref": requirement.source_ref,
            }
        )

    document.extracted_at = datetime.now(timezone.utc)

    record_requirements_created(db, org_id, requirement_count)

    latency_ms = int((document.extracted_at - document.created_at).total_seconds() * 1000) if document.created_at else 0
    db.add(
        Event(
            org_id=org_id,
            document_id=document.id,
            type="extracted",
            data={
                "document_id": str(document.id),
                "requirement_ids": [item["id"] for item in created_payload],
                "latency_ms": latency_ms,
                "storage_key": storage_key,
            },
        )
    )


def _mark_document_failed(
    db: Session,
    document: Document,
    org_id: uuid.UUID,
    storage_key: str,
    reason: str,
) -> None:
    db.add(
        Event(
            org_id=org_id,
            document_id=document.id,
            type="extraction_failed",
            data={
                "document_id": str(document.id),
                "storage_key": storage_key,
                "reason": reason,
            },
        )
    )
