from __future__ import annotations

import io
from types import SimpleNamespace
import uuid

import boto3
import pytest
from sqlalchemy import select

from api.config import settings
from api.db.session import SessionLocal
from api.models.requirements import (
    Requirement,
    RequirementAnchorTypeEnum,
    RequirementFrequencyEnum,
    RequirementStatusEnum,
)
from api.models.templates import DocumentTemplate, RequirementTemplate
from api.services.template_matching import compute_fingerprint


@pytest.fixture()
def mock_s3():
    from moto import mock_aws

    with mock_aws():
        s3 = boto3.client("s3", region_name=settings.aws.region)
        bucket = "test-compliance-bucket"
        s3.create_bucket(Bucket=bucket)
        previous_bucket = settings.aws.s3_bucket
        settings.aws.s3_bucket = bucket
        try:
            yield s3
        finally:
            settings.aws.s3_bucket = previous_bucket


def test_upload_rejects_non_pdf(client, auth_context, mock_s3):
    """Uploading a non-PDF should return HTTP 400 with 'PDF only.' message."""
    response = client.post(
        "/documents/upload",
        data={"trade": "electrical"},
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


def test_upload_rejects_short_pdf(client, auth_context, mock_s3, monkeypatch):
    """PDFs with <200 characters should respond with 400 and helpful message."""
    from api.routers import documents

    monkeypatch.setattr(documents, "extract_text_from_pdf", lambda _: "too short")

    response = client.post(
        "/documents/upload",
        data={"trade": "electrical"},
        files={"file": ("sparse.pdf", io.BytesIO(b"%PDF-1.7\n%%EOF"), "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Not enough content."


def test_upload_happy_path_creates_multiple_tasks(client, auth_context, mock_s3, monkeypatch):
    """Happy path should create ≥5 tasks and persist the S3 object."""
    from api.routers import documents

    monkeypatch.setattr(documents, "extract_text_from_pdf", lambda _: "a" * 500)

    drafts: list[SimpleNamespace] = []
    for idx, category in enumerate(["compliance", "permits", "training", "compliance", "training"], start=1):
        drafts.append(
            SimpleNamespace(
                title_en=f"Requirement {idx}",
                title_es=None,
                description_en="Stay compliant",
                description_es=None,
                category=category,
                frequency=RequirementFrequencyEnum.ANNUAL,
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
        data={"trade": "electrical"},
        files={"file": ("fixture.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "PROCESSING"
    assert "id" in payload

    document_api = client.get(f"/documents/{payload['id']}")
    assert document_api.status_code == 200
    document_details = document_api.json()
    assert document_details["status"] in {"READY", "PROCESSING"}

    storage_url = document_details["storage_url"]
    assert storage_url.startswith("s3://")
    bucket, key = storage_url.replace("s3://", "").split("/", 1)
    head = mock_s3.head_object(Bucket=bucket, Key=key)
    assert head["ResponseMetadata"]["HTTPStatusCode"] == 200

    with SessionLocal() as session:
        doc_id = uuid.UUID(payload["id"])
        categories = {
            row[0]
            for row in session.execute(
                select(Requirement.category).where(Requirement.document_id == doc_id)
            )
        }
        next_due_values = [
            row[0]
            for row in session.execute(
                select(Requirement.next_due).where(Requirement.document_id == doc_id)
            )
        ]
    assert categories.issuperset({"compliance", "permits", "training"})
    assert next_due_values
    assert all(next_due_values)

    listing = client.get("/documents")
    assert listing.status_code == 200
    listing_items = listing.json()["items"]
    assert any(item["id"] == payload["id"] for item in listing_items)


KNOWN_TEMPLATE_TEXT = (
    "Occupational Safety and Health Administration requires employers to summarize workplace "
    "injuries and illnesses annually. The OSHA Form 300A must be certified by a company executive "
    "and posted in a prominent location each year from February 1 through April 30. Employers must "
    "retain completed records for five years and make them available to workers upon request."
)


def test_upload_known_template_bypasses_llm(monkeypatch, client, auth_context):
    from api.routers import documents

    fingerprint = compute_fingerprint(KNOWN_TEMPLATE_TEXT)

    with SessionLocal() as session:
        template = DocumentTemplate(
            title="OSHA Form 3080",
            version="2024",
            trade="electrical",
            fingerprint=fingerprint,
            metadata_json={"seed_key": "test"},
        )
        template.requirement_templates.append(
            RequirementTemplate(
                title_en="Post OSHA 300A Summary",
                title_es="Publicar el resumen OSHA 300A",
                description_en="Post the OSHA Form 300A in a conspicuous location from February 1 to April 30 each year.",
                description_es="Publica el formulario OSHA 300A en un lugar visible del 1 de febrero al 30 de abril de cada año.",
                category="Recordkeeping",
                frequency=RequirementFrequencyEnum.ANNUAL,
                anchor_type=RequirementAnchorTypeEnum.UPLOAD_DATE,
                anchor_value={},
                attributes={"seed_key": "test"},
            )
        )
        session.add(template)
        session.commit()

    def fake_extract_text(_: io.BytesIO) -> str:  # type: ignore[override]
        return KNOWN_TEMPLATE_TEXT

    def explode_extract(*_args, **_kwargs):
        raise AssertionError("LLM extraction should not run for known templates")

    def explode_classify(*_args, **_kwargs):
        raise AssertionError("Classification should be skipped for template matches")

    monkeypatch.setattr(documents, "extract_text_from_pdf", fake_extract_text)
    monkeypatch.setattr(documents, "extract_requirement_drafts", explode_extract)
    monkeypatch.setattr(documents, "classify_document", explode_classify)

    pdf_bytes = b"%PDF-1.7\n" + b"A" * 1024 + b"\n%%EOF"
    response = client.post(
        "/documents/upload",
        data={"trade": "electrical"},
        files={"file": ("template.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "PROCESSING"

    with SessionLocal() as session:
        doc_id = uuid.UUID(payload["id"])
        stored_requirements = list(
            session.execute(
                select(Requirement).where(Requirement.document_id == doc_id)
            ).scalars()
        )
        assert len(stored_requirements) == 1
        requirement = stored_requirements[0]
        assert requirement.title_en == "Post OSHA 300A Summary"
        assert requirement.frequency == RequirementFrequencyEnum.ANNUAL
        assert requirement.anchor_type == RequirementAnchorTypeEnum.UPLOAD_DATE
        assert requirement.attributes.get("origin") == "template"
        assert requirement.status == RequirementStatusEnum.READY
