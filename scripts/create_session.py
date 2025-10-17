#!/usr/bin/env python
"""Utility to mint a local session cookie for manual testing."""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from api.config import settings
from api.db.session import SessionLocal
from api.models import UserSession
from api.services.auth import AuthService


def create_session(email: str, locale: str) -> str:
    with SessionLocal() as db:
        service = AuthService(db)
        user = service.get_or_create_user(email.strip().lower(), locale)
        org = service.ensure_primary_membership(user)

        raw_token = secrets.token_urlsafe(32)
        session_hash = service.hash_token(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)

        session = UserSession(
            user_id=user.id,
            org_id=org.id,
            session_token_hash=session_hash,
            expires_at=expires_at,
        )
        db.add(session)
        db.commit()

        print("User:", user.email)
        print("Org:", org.id)
        print("Session expires:", expires_at.isoformat())
        print("\nPaste this cookie into your browser's dev tools:")
        print(f"{settings.cookie_name}={raw_token}")
        return raw_token


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a session cookie for local testing")
    parser.add_argument("email", help="User email to authenticate as")
    parser.add_argument("--locale", default="en", help="Preferred locale (default: en)")
    args = parser.parse_args()

    create_session(args.email, args.locale)


if __name__ == "__main__":
    main()
