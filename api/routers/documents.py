from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlalchemy.orm import Session
from ..db.session import SessionLocal
from ..models.documents import Document
from ..models.requirements import Requirement
from ..models.events import Event
from ..services.parse_pdf import extract_text_from_pdf
from ..services.llm_extract import extract_requirements_from_text
from ..services.schedule import next_due_from_frequency
import os, uuid, shutil
from datetime import datetime, timezone

router = APIRouter()
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/documents/upload")
def upload_and_extract(file: UploadFile = File(...), db: Session = Depends(get_db)):
    # 1. Save file locally (MVP — swap to S3 later)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".pdf", ".txt"]:
        raise HTTPException(400, "Only PDF or TXT files are supported")

    dest = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}{ext}")
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)

    # 2. Create document row
    doc = Document(name=file.filename, storage_url=dest)
    db.add(doc)
    db.commit()
    db.refresh(doc)

    db.add(Event(org_id=None, requirement_id=None, type="upload", metadata=str(doc.id)))
    db.commit()

    # 3. Extract text
    if ext == ".pdf":
        text = extract_text_from_pdf(dest)
    elif ext == ".txt":
        with open(dest, "r") as f:
            text = f.read()
    else:
        text = None

    if not text or len(text) < 40:
        raise HTTPException(400, "Could not extract usable text from document")

    # 4. Call LLM for requirements (use first ~4000 chars for MVP)
    excerpt = text[:4000]
    items = extract_requirements_from_text(excerpt)

    # 5. Save requirements to DB
    created = []
    for it in items:
        due = None
        if it.due_date:
            try:
                due = datetime.fromisoformat(it.due_date.replace("Z", "+00:00"))
            except Exception:
                due = None
        if not due and it.frequency:
            due = next_due_from_frequency(it.frequency)

        req = Requirement(
            org_id=None,
            document_id=doc.id,
            title=it.title,
            category=it.category or None,
            frequency=it.frequency or None,
            due_date=due,
            source_ref=it.source_ref,
            confidence=it.confidence,
        )
        db.add(req)
        db.flush()
        created.append({
            "id": str(req.id),
            "title": req.title,
            "due_date": req.due_date.isoformat() if req.due_date else None,
            "frequency": req.frequency,
            "source_ref": req.source_ref,
            "confidence": req.confidence,
        })

    db.commit()

    db.add(Event(org_id=None, requirement_id=None, type="extracted", metadata=str(doc.id)))
    db.commit()

    # 6. Return results (doc + requirements)
    return {
        "document_id": str(doc.id),
        "name": doc.name,
        "requirements": created,
    }