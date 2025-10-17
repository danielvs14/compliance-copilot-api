from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException, Request, Response, status

from ..config import settings
from ..models import Org, User, UserSession
from ..services.auth import AuthError, AuthService
from ..db.session import SessionLocal


@dataclass
class AuthContext:
    user: User
    org: Org
    session: UserSession


def require_auth(request: Request) -> AuthContext:
    raw_token = request.cookies.get(settings.cookie_name)
    with SessionLocal() as db:
        service = AuthService(db)
        row = service.session_from_token(raw_token or "")
        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

        session, user, org = row
        request.state.user_id = str(user.id)
        request.state.org_id = str(org.id)
        # detach objects before session closes
        db.expunge_all()
        return AuthContext(user=user, org=org, session=session)


def issue_magic_link(
    email: str,
    locale: str,
    request: Request,
    db: Session,
    redirect_path: str | None = None,
) -> str:
    service = AuthService(db)
    try:
        return service.request_magic_link(
            email=email,
            preferred_locale=locale,
            request_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            redirect_path=redirect_path,
        )
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def finalize_login(response: Response, signed_token: str, db: Session) -> AuthContext:
    service = AuthService(db)
    try:
        user, org, session_token, redirect_path = service.redeem_magic_link(signed_token)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    attach_session_cookie(response, session_token)
    row = service.session_from_token(session_token)
    assert row is not None
    session, _, _ = row
    response.headers["x-redirect-path"] = redirect_path
    return AuthContext(user=user, org=org, session=session)


def attach_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=raw_token,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        domain=settings.cookie_domain,
        path="/",
        max_age=int(timedelta(hours=settings.session_ttl_hours).total_seconds()),
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.cookie_name,
        domain=settings.cookie_domain,
        path="/",
    )
