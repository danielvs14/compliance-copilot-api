from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from api.config import settings
from api.db.session import SessionLocal
from api.models.documents import Document
from api.models.requirements import (
    Requirement,
    RequirementAnchorTypeEnum,
    RequirementFrequencyEnum,
    RequirementStatusEnum,
)
from api.models.user_sessions import UserSession
from api.services.auth import AuthService


def _seed_requirement() -> tuple[str, str, str]:
    with SessionLocal() as session:
        service = AuthService(session)
        user = service.get_or_create_user("archive@test.com", "en")
        org = service.ensure_primary_membership(user)

        document = Document(org_id=org.id, name="archive.pdf", storage_url="s3://bucket/archive.pdf")
        session.add(document)
        session.flush()

        now = datetime.now(timezone.utc)

        requirement = Requirement(
            org_id=org.id,
            document_id=document.id,
            title_en="Inspect ladder",
            title_es="Inspeccionar escalera",
            description_en="Inspect ladder before each use",
            description_es="Inspeccionar la escalera antes de cada uso",
            category="safety",
            frequency=RequirementFrequencyEnum.BEFORE_EACH_USE,
            anchor_type=RequirementAnchorTypeEnum.UPLOAD_DATE,
            anchor_value={"date": now.isoformat()},
            source_ref="Manual",
            confidence=0.8,
            trade="electrical",
            status=RequirementStatusEnum.OPEN,
            attributes={},
        )
        session.add(requirement)

        token = secrets.token_urlsafe(32)
        session.add(
            UserSession(
                user_id=user.id,
                org_id=org.id,
                session_token_hash=AuthService.hash_token(token),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
            )
        )
        session.commit()
        return str(requirement.id), token, str(org.id)


def test_archive_without_approval_excludes_from_list(client):
    requirement_id, token, _ = _seed_requirement()
    client.cookies.set(settings.cookie_name, token)

    response = client.post(
        f"/requirements/{requirement_id}/archive",
        json={"reason": "Duplicate"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["archive_state"] == "archived"
    assert payload["status"] == RequirementStatusEnum.ARCHIVED.value

    list_response = client.get("/requirements")
    assert list_response.status_code == 200
    assert list_response.json()["items"] == []

    archived_list = client.get("/requirements", params={"archived": True})
    assert archived_list.status_code == 200
    archived_items = archived_list.json()["items"]
    assert archived_items and archived_items[0]["archive_state"] == "archived"

    client.cookies.clear()


def test_archive_and_restore_flow(client):
    requirement_id, token, _ = _seed_requirement()
    client.cookies.set(settings.cookie_name, token)

    archived = client.post(
        f"/requirements/{requirement_id}/archive",
        json={"reason": "Obsolete"},
    )
    assert archived.status_code == 200
    archived_body = archived.json()
    assert archived_body["archive_state"] == "archived"
    assert archived_body["status"] == RequirementStatusEnum.ARCHIVED.value

    duplicate = client.post(
        f"/requirements/{requirement_id}/archive",
        json={"reason": "Another reason"},
    )
    assert duplicate.status_code == 409

    restored = client.post(
        f"/requirements/{requirement_id}/archive/restore",
        json={"note": "Needed for audit"},
    )
    assert restored.status_code == 200
    restored_body = restored.json()
    assert restored_body["archive_state"] == "restored"
    assert restored_body["status"] == RequirementStatusEnum.OPEN.value

    list_response = client.get("/requirements")
    assert list_response.status_code == 200
    assert any(item["id"] == requirement_id for item in list_response.json()["items"])

    client.cookies.clear()
