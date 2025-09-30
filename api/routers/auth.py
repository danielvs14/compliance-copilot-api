from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies.auth import AuthContext, clear_session_cookie, finalize_login, issue_magic_link, require_auth
from ..dependencies.db import get_db
from ..models import Org, User
from ..services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


class MagicLinkRequest(BaseModel):
    email: EmailStr
    preferred_locale: str = Field(default="en", pattern="^[a-z]{2}(-[A-Z]{2})?$")
    redirect_path: str | None = Field(default="/dashboard")


class SessionPayload(BaseModel):
    user: dict
    org: dict
    redirect_path: str | None = None


class UpdateProfileRequest(BaseModel):
    preferred_locale: str = Field(..., pattern="^[a-z]{2}(-[A-Z]{2})?$")


def _serialize_user(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "preferred_locale": user.preferred_locale,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def _serialize_org(org: Org) -> dict:
    return {
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
    }


@router.post("/magic-link")
def send_magic_link(payload: MagicLinkRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    issue_magic_link(payload.email, payload.preferred_locale, request, db, payload.redirect_path)
    return {"status": "sent"}


@router.get("/callback")
def magic_link_callback(token: str, response: Response, db: Session = Depends(get_db)) -> SessionPayload:
    context = finalize_login(response, token, db)
    redirect_path = response.headers.get("x-redirect-path", "/dashboard")
    return SessionPayload(user=_serialize_user(context.user), org=_serialize_org(context.org), redirect_path=redirect_path)


@router.get("/me")
def get_current_user(context: AuthContext = Depends(require_auth)) -> SessionPayload:
    return SessionPayload(
        user=_serialize_user(context.user),
        org=_serialize_org(context.org),
        redirect_path=None,
    )


@router.patch("/me")
def update_current_user(
    payload: UpdateProfileRequest,
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> SessionPayload:
    context.user.preferred_locale = payload.preferred_locale
    db.add(context.user)
    db.commit()
    db.refresh(context.user)
    return SessionPayload(
        user=_serialize_user(context.user),
        org=_serialize_org(context.org),
        redirect_path=None,
    )


@router.post("/logout")
def logout_user(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    raw_token = request.cookies.get(settings.cookie_name)
    if raw_token:
        AuthService(db).revoke_session(raw_token)
    clear_session_cookie(response)
    return {"status": "logged_out"}
