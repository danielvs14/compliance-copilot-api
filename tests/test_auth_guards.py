from __future__ import annotations

import io

import pytest


@pytest.mark.integration
def test_documents_list_requires_auth(client):
    response = client.get("/documents")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


@pytest.mark.integration
def test_documents_upload_requires_auth(client):
    response = client.post(
        "/documents/upload",
        data={"trade": "electrical"},
        files={"file": ("sample.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
