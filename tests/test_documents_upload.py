from __future__ import annotations

import io
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from api.db.session import SessionLocal
from api.models.requirements import Requirement


ORG_ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def override_upload_dir(monkeypatch, tmp_path):
    from api.routers import documents

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    monkeypatch.setattr(documents, "UPLOAD_DIR", uploads_dir)
    yield


def test_upload_rejects_non_pdf(client):
    """Uploading a non-PDF should return HTTP 400 with 'PDF only.' message."""
    response = client.post(
        "/documents/upload",
        data={"org_id": ORG_ID, "trade": "electrical"},
        files={
            "file": (
                "notes.docx",
                io.BytesIO(b"dummy"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "PDF only."


def test_upload_rejects_short_pdf(client, monkeypatch):
    """PDFs with <200 characters should respond with 400 and helpful message."""
    from api.routers import documents

    monkeypatch.setattr(documents, "extract_text_from_pdf", lambda _: "too short")

    response = client.post(
        "/documents/upload",
        data={"org_id": ORG_ID, "trade": "electrical"},
        files={"file": ("sparse.pdf", io.BytesIO(b"%PDF-1.7\n%%EOF"), "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Not enough content."


def test_upload_happy_path_creates_multiple_tasks(client, monkeypatch):
    """Happy path should create ≥5 tasks across compliance, permits, and training categories."""
    from api.routers import documents

    monkeypatch.setattr(documents, "extract_text_from_pdf", lambda _: "a" * 500)

    drafts = []
    for idx, category in enumerate(["compliance", "permits", "training", "compliance", "training"], start=1):
        drafts.append(
            SimpleNamespace(
                title_en=f"Requirement {idx}",
                title_es=None,
                description_en="Stay compliant",
                description_es=None,
                category=category,
                frequency="annual",
                due_date=None,
                source_ref="Sec. 1",
                confidence=0.9,
                origin="llm",
                attributes={"seed": idx},
            )
        )

    def _mock_extract(text: str, trade: str = "electrical"):
        return drafts

    def _mock_attach_translations(draft_list):
        for draft in draft_list:
            draft.title_es = f"{draft.title_en} (es)"
            draft.description_es = f"{draft.description_en} (es)"
        return draft_list

    monkeypatch.setattr(documents, "extract_requirement_drafts", _mock_extract)
    monkeypatch.setattr(documents, "attach_translations", _mock_attach_translations)

    pdf_bytes = b"%PDF-1.7\n" + b"A" * 1024 + b"\n%%EOF"
    response = client.post(
        "/documents/upload",
        data={"org_id": ORG_ID, "trade": "electrical"},
        files={"file": ("fixture.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["requirements"]) >= 5

    with SessionLocal() as session:
        categories = {
            row[0]
            for row in session.execute(
                select(Requirement.category).where(Requirement.document_id == uuid.UUID(payload["document_id"]))
            )
        }
    assert categories.issuperset({"compliance", "permits", "training"})

    assert all(item["title_es"].endswith("(es)") for item in payload["requirements"])
