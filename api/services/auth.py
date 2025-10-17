from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    LoginToken,
    Membership,
    MembershipRole,
    Org,
    OrgRequirementMetrics,
    User,
    UserSession,
)
from .email import EmailMessage, get_email_client

logger = logging.getLogger(__name__)


class AuthError(Exception):
    pass


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.serializer = URLSafeTimedSerializer(settings.magic_link_secret, salt="magic-link")
        self.email_client = get_email_client()

    # --- Magic link flow -------------------------------------------------
    def request_magic_link(
        self,
        email: str,
        preferred_locale: str = "en",
        request_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        redirect_path: Optional[str] = None,
    ) -> str:
        normalized_email = email.strip().lower()
        if not normalized_email:
            raise AuthError("Email required")

        user = self.get_or_create_user(normalized_email, preferred_locale)
        org = self.ensure_primary_membership(user)

        raw_token = secrets.token_urlsafe(32)
        token_hash = self.hash_token(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.magic_link_expiry_minutes)

        login_token = LoginToken(
            user_id=user.id,
            token_hash=token_hash,
            email=normalized_email,
            purpose="login",
            expires_at=expires_at,
        )
        self.db.add(login_token)
        self.db.flush()

        signed_token = self.serializer.dumps(
            {
                "token": raw_token,
                "user_id": str(user.id),
                "org_id": str(org.id),
                "login_token_id": str(login_token.id),
                "redirect": redirect_path or "/dashboard",
            }
        )

        magic_link = f"{settings.app_url}/auth/callback?token={signed_token}"
        text_body = (
            "Your Compliance Copilot login link is ready.\n\n"
            f"Click to sign in: {magic_link}\n\n"
            "This link expires in "
            f"{settings.magic_link_expiry_minutes} minutes. If you did not request it, you can ignore this message."
        )

        try:
            self.email_client.send(
                EmailMessage(
                    to=normalized_email,
                    subject="Your Compliance Copilot login link",
                    text_body=text_body,
                )
            )
        except Exception as exc:  # pragma: no cover - email errors
            logger.error("Failed to send magic link: email=%s error=%s", normalized_email, exc)
            raise AuthError("Could not send magic link") from exc

        logger.info(
            "magic_link_issued user_id=%s org_id=%s request_ip=%s user_agent=%s",
            user.id,
            org.id,
            request_ip,
            user_agent,
        )

        self.db.commit()
        return magic_link

    def redeem_magic_link(self, signed_token: str) -> tuple[User, Org, str, str]:
        try:
            payload = self.serializer.loads(
                signed_token,
                max_age=settings.magic_link_expiry_minutes * 60,
            )
        except SignatureExpired as exc:
            raise AuthError("Magic link expired") from exc
        except BadSignature as exc:
            raise AuthError("Invalid login token") from exc

        user_id = uuid.UUID(payload["user_id"])
        org_id = uuid.UUID(payload["org_id"])
        raw_token = payload["token"]
        login_token_id = uuid.UUID(payload.get("login_token_id"))
        redirect_path = payload.get("redirect", "/dashboard")

        login_token = (
            self.db.query(LoginToken)
            .filter(
                LoginToken.id == login_token_id,
                LoginToken.user_id == user_id,
                LoginToken.token_hash == self.hash_token(raw_token),
                LoginToken.consumed_at.is_(None),
                LoginToken.expires_at > datetime.now(timezone.utc),
            )
            .one_or_none()
        )
        if not login_token:
            raise AuthError("Login token not found or already used")

        login_token.consumed_at = datetime.now(timezone.utc)

        user = self.db.query(User).filter(User.id == user_id).one()
        org = self.db.query(Org).filter(Org.id == org_id).one()

        session_token = secrets.token_urlsafe(32)
        session_hash = self.hash_token(session_token)

        session = UserSession(
            user_id=user.id,
            org_id=org.id,
            session_token_hash=session_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours),
        )
        user.last_login_at = datetime.now(timezone.utc)
        self.db.add(session)
        self.db.add(login_token)
        self.db.commit()

        logger.info(
            "user_login user_id=%s org_id=%s login_token_id=%s",
            user.id,
            org.id,
            login_token.id,
        )

        return user, org, session_token, redirect_path

    # --- Session flow ----------------------------------------------------
    def session_from_token(self, raw_token: str) -> Optional[tuple[UserSession, User, Org]]:
        if not raw_token:
            return None
        hashed = self.hash_token(raw_token)
        now = datetime.now(timezone.utc)
        row = (
            self.db.query(UserSession, User, Org)
            .join(User, User.id == UserSession.user_id)
            .join(Org, Org.id == UserSession.org_id)
            .filter(
                UserSession.session_token_hash == hashed,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
                User.is_active.is_(True),
            )
            .one_or_none()
        )
        return row

    def revoke_session(self, raw_token: str) -> None:
        hashed = self.hash_token(raw_token)
        now = datetime.now(timezone.utc)
        updated = (
            self.db.query(UserSession)
            .filter(UserSession.session_token_hash == hashed, UserSession.revoked_at.is_(None))
            .update({"revoked_at": now})
        )
        if updated:
            logger.info("session_revoked hash=%s", hashed[:8])
        self.db.commit()

    # --- Helpers ---------------------------------------------------------
    @staticmethod
    def hash_token(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def get_or_create_user(self, email: str, preferred_locale: str) -> User:
        user = (
            self.db.query(User)
            .filter(func.lower(User.email) == email.lower())
            .one_or_none()
        )
        if user:
            return user

        user = User(email=email, preferred_locale=preferred_locale)
        self.db.add(user)
        self.db.flush()
        return user

    def ensure_primary_membership(self, user: User) -> Org:
        membership = (
            self.db.query(Membership)
            .filter(Membership.user_id == user.id)
            .order_by(Membership.created_at.asc())
            .first()
        )
        if membership:
            return self.db.query(Org).filter(Org.id == membership.org_id).one()

        # create new org for first-time user
        local_part = user.email.split("@")[0]
        default_name = f"{local_part.title()} Electrical"
        org = Org(name=default_name[:100], primary_trade="electrical")
        self.db.add(org)
        self.db.flush()

        membership = Membership(org_id=org.id, user_id=user.id, role=MembershipRole.OWNER)
        metrics = OrgRequirementMetrics(org_id=org.id)
        self.db.add_all([membership, metrics])
        self.db.flush()

        logger.info("org_created org_id=%s owner_user_id=%s", org.id, user.id)
        return org
