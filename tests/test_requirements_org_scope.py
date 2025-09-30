from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from api.config import settings
from api.db.session import SessionLocal
from api.models.documents import Document
from api.models.requirements import Requirement, RequirementStatusEnum
from api.models.user_sessions import UserSession
from api.services.auth import AuthService


def test_requirements_list_is_scoped_by_org(client):
    """Ensure GET /requirements only returns rows for the authenticated org."""
    with SessionLocal() as session:
        service = AuthService(session)
        user_a = service.get_or_create_user("org-a@example.com", "en")
        org_a = service.ensure_primary_membership(user_a)
        user_b = service.get_or_create_user("org-b@example.com", "en")
        org_b = service.ensure_primary_membership(user_b)

        doc_a = Document(org_id=org_a.id, name="compliance.pdf", storage_url="s3://bucket/a.pdf")
        doc_b = Document(org_id=org_b.id, name="permits.pdf", storage_url="s3://bucket/b.pdf")
        session.add_all([doc_a, doc_b])
        session.flush()

        session.add_all(
            [
                Requirement(
                    org_id=org_a.id,
                    document_id=doc_a.id,
                    title_en="Arc flash training",
                    title_es="Capacitación de arco eléctrico",
                    description_en="Complete annual training",
                    description_es="Completar capacitación anual",
                    category="training",
                    frequency="annual",
                    source_ref="Sec. 1",
                    confidence=0.9,
                    trade="electrical",
                    status=RequirementStatusEnum.OPEN,
                    attributes={},
                ),
                Requirement(
                    org_id=org_b.id,
                    document_id=doc_b.id,
                    title_en="Permit filing",
                    title_es="Presentación de permisos",
                    description_en="Submit permit by due date",
                    description_es="Presentar permiso antes de la fecha límite",
                    category="permits",
                    frequency=None,
                    source_ref="Sec. 2",
                    confidence=0.8,
                    trade="electrical",
                    status=RequirementStatusEnum.OPEN,
                    attributes={},
                ),
            ]
        )

        token_a = secrets.token_urlsafe(32)
        token_b = secrets.token_urlsafe(32)
        expiry = datetime.now(timezone.utc) + timedelta(hours=4)
        session.add_all(
            [
                UserSession(
                    user_id=user_a.id,
                    org_id=org_a.id,
                    session_token_hash=AuthService.hash_token(token_a),
                    expires_at=expiry,
                ),
                UserSession(
                    user_id=user_b.id,
                    org_id=org_b.id,
                    session_token_hash=AuthService.hash_token(token_b),
                    expires_at=expiry,
                ),
            ]
        )
        session.commit()

        org_a_id = str(org_a.id)
        org_b_id = str(org_b.id)

    client.cookies.set(settings.cookie_name, token_a)
    response = client.get("/requirements", params={"status": "OPEN"})
    client.cookies.clear()

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["org_id"] == org_a_id

    client.cookies.set(settings.cookie_name, token_b)
    response_other_org = client.get("/requirements", params={"status": "OPEN"})
    client.cookies.clear()
    assert response_other_org.status_code == 200
    other_data = response_other_org.json()
    assert len(other_data) == 1
    assert other_data[0]["org_id"] == org_b_id
