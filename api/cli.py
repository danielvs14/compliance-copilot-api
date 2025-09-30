from __future__ import annotations

import typer

from .config import settings
from .db.session import SessionLocal
from .models import Membership, MembershipRole, Org, User
from .services.auth import AuthService

app = typer.Typer(help="Compliance Copilot administrative CLI")


@app.command()
def create_org_user(
    email: str = typer.Argument(..., help="User email"),
    org_name: str = typer.Argument(..., help="Organization name"),
    full_name: str = typer.Option("", "--full-name", "-f", help="Optional full name"),
    locale: str = typer.Option("en", "--locale", "-l", show_default=True, help="Preferred locale (en/es)"),
) -> None:
    """Create a user, organization, and membership in one step."""
    db = SessionLocal()
    try:
        auth = AuthService(db)
        user = auth.get_or_create_user(email, locale)
        if full_name:
            user.full_name = full_name
        db.flush()

        existing_org = db.query(Org).filter(Org.name == org_name).one_or_none()
        if existing_org:
            org = existing_org
        else:
            org = Org(name=org_name)
            db.add(org)
            db.flush()

        membership = (
            db.query(Membership)
            .filter(Membership.user_id == user.id, Membership.org_id == org.id)
            .one_or_none()
        )
        if not membership:
            membership = Membership(user_id=user.id, org_id=org.id, role=MembershipRole.OWNER)
            db.add(membership)

        db.commit()
        typer.echo(f"Created user {user.email} linked to org {org.name} ({org.id})")
    finally:
        db.close()


@app.command()
def send_magic_link(email: str = typer.Argument(...)) -> None:
    """Send a login magic link to an email address."""
    db = SessionLocal()
    try:
        AuthService(db).request_magic_link(email)
        typer.echo(f"Magic link sent to {email}. Check {settings.app_url} callback in inbox")
    finally:
        db.close()


if __name__ == "__main__":
    app()
