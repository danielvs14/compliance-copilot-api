from __future__ import annotations

from datetime import datetime, timezone
import uuid

from api.db.session import SessionLocal
from api.models.documents import Document
from api.models.requirements import (
    Requirement,
    RequirementAnchorTypeEnum,
    RequirementFrequencyEnum,
    RequirementStatusEnum,
)


def _create_document(org_id: uuid.UUID) -> uuid.UUID:
    with SessionLocal() as session:
        document = Document(
            org_id=org_id,
            name="triage.pdf",
            storage_url="s3://triage/example.pdf",
        )
        session.add(document)
        session.commit()
        return document.id


def _seed_requirement(status: RequirementStatusEnum, org_id: uuid.UUID, document_id: uuid.UUID) -> uuid.UUID:
    with SessionLocal() as session:
        requirement = Requirement(
            org_id=org_id,
            document_id=document_id,
            title_en="Inspect harness",
            title_es="Inspeccionar arnés",
            description_en="Check before shift",
            description_es="Revisar antes del turno",
            category="safety",
            status=status,
            source_ref="manual",
            confidence=0.4,
            trade="electrical",
            attributes={},
        )
        session.add(requirement)
        session.commit()
        return requirement.id


def test_pending_requirements_excluded_from_default_list(client, auth_context):
    org_id = uuid.UUID(auth_context["org_id"])
    document_id = _create_document(org_id)

    pending_id = _seed_requirement(RequirementStatusEnum.PENDING_REVIEW, org_id, document_id)
    open_id = _seed_requirement(RequirementStatusEnum.OPEN, org_id, document_id)

    response = client.get("/requirements")
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(open_id) in ids
    assert str(pending_id) not in ids

    response_pending = client.get("/requirements", params={"status": "PENDING_REVIEW"})
    assert response_pending.status_code == 200
    pending_ids = {item["id"] for item in response_pending.json()["items"]}
    assert str(pending_id) in pending_ids


def test_bulk_triage_updates_requirements(client, auth_context):
    org_id = uuid.UUID(auth_context["org_id"])
    document_id = _create_document(org_id)
    requirement_id = _seed_requirement(RequirementStatusEnum.PENDING_REVIEW, org_id, document_id)

    due_date = datetime(2025, 1, 15, tzinfo=timezone.utc)

    payload = {
        "requirement_ids": [str(requirement_id)],
        "frequency": RequirementFrequencyEnum.ANNUAL.value,
        "anchor_type": RequirementAnchorTypeEnum.UPLOAD_DATE.value,
        "anchor_value": {"date": due_date.isoformat()},
        "due_date": due_date.isoformat(),
        "assignee": "triage@example.com",
        "status": RequirementStatusEnum.REVIEW.value,
    }

    response = client.post("/requirements/triage/bulk", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["updated"] == 1
    triaged = body["items"][0]
    assert triaged["status"] == RequirementStatusEnum.REVIEW.value
    assert triaged["frequency"] == RequirementFrequencyEnum.ANNUAL.value
    assert triaged["anchor_type"] == RequirementAnchorTypeEnum.UPLOAD_DATE.value
    assert triaged["due_date"] == due_date.isoformat()

    with SessionLocal() as session:
        refreshed = session.get(Requirement, requirement_id)
        assert refreshed is not None
        assert refreshed.status == RequirementStatusEnum.REVIEW
        assert refreshed.frequency == RequirementFrequencyEnum.ANNUAL
        assert refreshed.anchor_type == RequirementAnchorTypeEnum.UPLOAD_DATE
        assert refreshed.next_due is not None
        assert (refreshed.attributes or {}).get("assignee") == "triage@example.com"
        triage_meta = (refreshed.attributes or {}).get("triage")
        assert triage_meta is not None
        assert triage_meta.get("resolved_at") is not None
