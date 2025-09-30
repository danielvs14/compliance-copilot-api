from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie

from api.config import settings
from api.db.session import SessionLocal
from api.models import LoginToken, UserSession
from api.services.auth import AuthService


def test_magic_link_request_endpoint(client, monkeypatch):
    called: dict[str, str] = {}

    def fake_request_magic_link(self, email: str, preferred_locale: str, **_) -> str:  # type: ignore[override]
        called["email"] = email
        called["locale"] = preferred_locale
        return "https://app.example.com/auth/callback?token=fake"

    monkeypatch.setattr(AuthService, "request_magic_link", fake_request_magic_link)

    response = client.post(
        "/auth/magic-link",
        json={"email": "owner@example.com", "preferred_locale": "es"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "sent"}
    assert called["email"] == "owner@example.com"
    assert called["locale"] == "es"


def test_magic_link_callback_and_me_endpoint(client):
    with SessionLocal() as session:
        service = AuthService(session)
        user = service.get_or_create_user("owner@example.com", "en")
        org = service.ensure_primary_membership(user)

        raw_token = "unit-test-token"
        login_token = LoginToken(
            user_id=user.id,
            token_hash=AuthService.hash_token(raw_token),
            email=user.email,
            purpose="login",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        session.add(login_token)
        session.commit()

        org_id = str(org.id)
        user_id = str(user.id)

        signed = service.serializer.dumps(
            {
                "token": raw_token,
                "user_id": user_id,
                "org_id": org_id,
                "login_token_id": str(login_token.id),
                "redirect": "/dashboard",
            }
        )

    response = client.get(f"/auth/callback?token={signed}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["email"] == "owner@example.com"
    assert payload["org"]["id"] == org_id
    assert settings.cookie_name in client.cookies

    me_response = client.get("/auth/me")
    assert me_response.status_code == 200
    me_data = me_response.json()
    assert me_data["user"]["id"] == payload["user"]["id"]
    assert me_data["org"]["id"] == payload["org"]["id"]

    patch_response = client.patch("/auth/me", json={"preferred_locale": "es"})
    assert patch_response.status_code == 200
    assert patch_response.json()["user"]["preferred_locale"] == "es"


def test_logout_revokes_session(client):
    token = "logout-session-token"
    with SessionLocal() as session:
        service = AuthService(session)
        user = service.get_or_create_user("logout@example.com", "en")
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

    response = client.post(
        "/auth/logout",
        cookies={settings.cookie_name: token},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "logged_out"}
    # Starlette's TestClient keeps the original cookie jar, so assert via response metadata.
    cookies = SimpleCookie()
    header = response.headers.get("set-cookie", "")
    cookies.load(header)
    morsel = cookies.get(settings.cookie_name)
    assert morsel is not None
    assert morsel.value == ""
    assert morsel["max-age"] == "0"

    with SessionLocal() as session:
        persisted = (
            session.query(UserSession)
            .filter(UserSession.session_token_hash == AuthService.hash_token(token))
            .one()
        )
        assert persisted.revoked_at is not None
