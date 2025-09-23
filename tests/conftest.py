from __future__ import annotations

import os
import pathlib
import sys
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from api.db.session import SessionLocal
from api.main import app


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


@pytest.fixture(autouse=True)
def cleanup_database() -> Iterator[None]:
    """Truncate core tables after every test to keep isolation."""
    yield
    with SessionLocal() as session:
        session.execute(
            text(
                "TRUNCATE TABLE events, requirements, documents, org_requirement_metrics RESTART IDENTITY CASCADE"
            )
        )
        session.commit()
