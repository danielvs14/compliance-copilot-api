from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from ..db.session import SessionLocal
from ..models.requirements import Requirement
from ..models.events import Event
from datetime import datetime, timezone

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/documents/{doc_id}/requirements")
def list_requirements(doc_id: str, db: Session = Depends(get_db)):
    q = db.query(Requirement).filter(Requirement.document_id == doc_id).all()
    return [
        {
            "id": str(r.id),
            "title": r.title,
            "category": r.category,
            "frequency": r.frequency,
            "due_date": r.due_date.isoformat() if r.due_date else None,
            "status": r.status,
            "confidence": r.confidence,
            "source_ref": r.source_ref
        }
        for r in q
    ]

@router.post("/requirements/{req_id}/complete")
def complete_requirement(req_id: str, db: Session = Depends(get_db)):
    r = db.query(Requirement).get(req_id)
    if not r:
        raise HTTPException(404, "Not found")
    r.status = "DONE"
    r.completed_at = datetime.now(timezone.utc)
    db.add(r); db.commit()
    db.add(Event(org_id=None, requirement_id=r.id, type="completed", metadata="")); db.commit()
    return {"ok": True}
