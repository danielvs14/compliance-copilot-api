from __future__ import annotations

import os
import pathlib
import sys
from typing import Iterator
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from api.config import settings
from api.db.session import SessionLocal
from api.models import UserSession
from api.main import app
from api.services.auth import AuthService


DEFAULT_DB_URL = "postgresql+psycopg2://compliance:compliance@localhost:5433/compliance_copilot"


@pytest.fixture(scope="session")
def database_url() -> str:
    """Expose DATABASE_URL used for integration tests."""
    return os.getenv("DATABASE_URL", DEFAULT_DB_URL)


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    """Provide a FastAPI TestClient instance."""
    with TestClient(app) as _client:
        yield _client


@pytest.fixture()
def auth_context(client: TestClient) -> Iterator[dict[str, str]]:
    """Create an authenticated session and attach cookie to the client."""
    token = secrets.token_urlsafe(32)
    with SessionLocal() as session:
        service = AuthService(session)
        user = service.get_or_create_user("owner@example.com", "en")
        org = service.ensure_primary_membership(user)

        session.add(
            UserSession(
                user_id=user.id,
                org_id=org.id,
                session_token_hash=AuthService.hash_token(token),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
            )
        )
        session.commit()

        user_id = str(user.id)
        org_id = str(org.id)

    client.cookies.set(settings.cookie_name, token)
    try:
        yield {"user_id": user_id, "org_id": org_id, "token": token}
    finally:
        client.cookies.clear()


@pytest.fixture(autouse=True)
def cleanup_database() -> Iterator[None]:
    """Truncate core tables after every test to keep isolation."""
    yield
    with SessionLocal() as session:
        session.execute(
            text(
                "TRUNCATE TABLE login_tokens, user_sessions, memberships, requirements, reminder_jobs, documents, events, permits, training_certs, org_requirement_metrics, users, orgs RESTART IDENTITY CASCADE"
            )
        )
        session.commit()
