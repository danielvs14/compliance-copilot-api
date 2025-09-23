from __future__ import annotations

import uuid

from api.db.session import SessionLocal
from api.models.documents import Document
from api.models.requirements import Requirement, RequirementStatusEnum


def test_requirements_list_is_scoped_by_org(client):
    """Ensure GET /requirements only returns rows for the requesting org."""
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()

    with SessionLocal() as session:
        doc_a = Document(org_id=org_a, name="compliance.pdf", storage_url="/tmp/a.pdf")
        doc_b = Document(org_id=org_b, name="permits.pdf", storage_url="/tmp/b.pdf")
        session.add_all([doc_a, doc_b])
        session.flush()

        session.add_all(
            [
                Requirement(
                    org_id=org_a,
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
                    org_id=org_b,
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

        session.commit()

    response = client.get(
        "/requirements",
        params={"org_id": str(org_a), "status": "OPEN"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["org_id"] == str(org_a)

    response_other_org = client.get(
        "/requirements",
        params={"org_id": str(org_b), "status": "OPEN"},
    )
    assert response_other_org.status_code == 200
    other_data = response_other_org.json()
    assert len(other_data) == 1
    assert other_data[0]["org_id"] == str(org_b)
