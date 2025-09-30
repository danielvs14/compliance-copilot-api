from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models.events import Event
from ..models.memberships import Membership
from ..models.orgs import Org
from ..models.permits import Permit
from ..models.reminder_jobs import ReminderJob, ReminderStatusEnum
from ..models.requirements import Requirement, RequirementStatusEnum
from ..models.training_certs import TrainingCert
from ..models.users import User
from .email import EmailMessage, EmailClient, get_email_client
from .metrics import (
    record_completion_after_reminder,
    record_overdue_completion,
    record_reminder_failed,
    record_reminder_scheduled,
    record_reminder_sent,
)
from .schedule import compute_next_due


logger = logging.getLogger(__name__)


REMINDER_OFFSETS = (30, 7, 1)


@dataclass(frozen=True)
class Recipient:
    email: str
    locale: str


def _collect_recipients(db: Session, org_id) -> Sequence[Recipient]:
    rows = (
        db.execute(
            select(User.email, User.preferred_locale)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == org_id, User.is_active.is_(True))
        )
        .unique()
        .all()
    )
    return [Recipient(email=row[0], locale=row[1] or "en") for row in rows]


def _remove_stale_jobs(db: Session, target_type: str, target_id, keep_due_at: datetime | None) -> None:
    stmt = (
        delete(ReminderJob)
        .where(
            ReminderJob.target_type == target_type,
            ReminderJob.target_id == target_id,
            ReminderJob.status == ReminderStatusEnum.PENDING,
        )
    )
    if keep_due_at is not None:
        stmt = stmt.where(ReminderJob.target_due_at != keep_due_at)
    db.execute(stmt)


def _upsert_job(
    db: Session,
    *,
    org_id,
    target_type: str,
    target_id,
    due_at: datetime,
    offset_days: int,
    recipient: Recipient,
    payload: dict,
    now: datetime,
) -> ReminderJob | None:
    run_at = due_at - timedelta(days=offset_days)
    if run_at < now:
        if now - run_at <= timedelta(hours=1):
            run_at = now
        else:
            return None

    existing = db.execute(
        select(ReminderJob)
        .where(
            ReminderJob.target_type == target_type,
            ReminderJob.target_id == target_id,
            ReminderJob.target_due_at == due_at,
            ReminderJob.reminder_offset_days == offset_days,
            ReminderJob.recipient_email == recipient.email,
        )
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()

    if existing:
        if existing.status == ReminderStatusEnum.FAILED:
            existing.status = ReminderStatusEnum.PENDING
            existing.attempts = 0
            existing.last_error = None
        existing.run_at = run_at
        existing.payload = payload
        existing.recipient_locale = recipient.locale
        return None

    job = ReminderJob(
        org_id=org_id,
        target_type=target_type,
        target_id=target_id,
        target_due_at=due_at,
        reminder_offset_days=offset_days,
        run_at=run_at,
        recipient_email=recipient.email,
        recipient_locale=recipient.locale,
        payload=payload,
    )
    db.add(job)
    return job


def queue_reminders(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    stats = defaultdict(int)

    org_cache: dict = {}

    def recipients_for(org_id):
        if org_id not in org_cache:
            org_cache[org_id] = _collect_recipients(db, org_id)
        return org_cache[org_id]

    requirements = (
        db.query(Requirement)
        .filter(Requirement.status == RequirementStatusEnum.OPEN)
        .all()
    )

    for req in requirements:
        next_due = compute_next_due(req.next_due or req.due_date, req.frequency, base_time=now)
        req.next_due = next_due
        if not next_due:
            continue

        recipients = recipients_for(req.org_id)
        if not recipients:
            continue

        _remove_stale_jobs(db, "requirement", req.id, keep_due_at=next_due)

        payload = {
            "title_en": req.title_en,
            "title_es": req.title_es,
            "description_en": req.description_en,
            "description_es": req.description_es,
            "due_at": next_due.isoformat(),
        }

        for recipient in recipients:
            for offset in REMINDER_OFFSETS:
                job = _upsert_job(
                    db,
                    org_id=req.org_id,
                    target_type="requirement",
                    target_id=req.id,
                    due_at=next_due,
                    offset_days=offset,
                    recipient=recipient,
                    payload=payload,
                    now=now,
                )
                if job:
                    stats["scheduled"] += 1
                    db.add(
                        Event(
                            org_id=req.org_id,
                            document_id=req.document_id,
                            requirement_id=req.id,
                            type="reminder_scheduled",
                            data={
                                "target_type": "requirement",
                                "target_id": str(req.id),
                                "recipient": recipient.email,
                                "offset_days": offset,
                                "run_at": job.run_at.isoformat(),
                                "due_at": next_due.isoformat(),
                            },
                        )
                    )
                    record_reminder_scheduled(db, req.org_id)

    permits = db.query(Permit).filter(Permit.expires_at.isnot(None)).all()
    for permit in permits:
        due = permit.expires_at
        if not due or due <= now:
            continue

        recipients = recipients_for(permit.org_id)
        if not recipients:
            continue

        _remove_stale_jobs(db, "permit", permit.id, keep_due_at=due)

        payload = {
            "name": permit.name,
            "permit_type": permit.permit_type,
            "expires_at": due.isoformat(),
        }

        for recipient in recipients:
            for offset in REMINDER_OFFSETS:
                job = _upsert_job(
                    db,
                    org_id=permit.org_id,
                    target_type="permit",
                    target_id=permit.id,
                    due_at=due,
                    offset_days=offset,
                    recipient=recipient,
                    payload=payload,
                    now=now,
                )
                if job:
                    stats["scheduled"] += 1
                    db.add(
                        Event(
                            org_id=permit.org_id,
                            type="reminder_scheduled",
                            data={
                                "target_type": "permit",
                                "target_id": str(permit.id),
                                "recipient": recipient.email,
                                "offset_days": offset,
                                "run_at": job.run_at.isoformat(),
                                "expires_at": due.isoformat(),
                            },
                        )
                    )
                    record_reminder_scheduled(db, permit.org_id)

    certs = db.query(TrainingCert).filter(TrainingCert.expires_at.isnot(None)).all()
    for cert in certs:
        due = cert.expires_at
        if not due or due <= now:
            continue

        recipients = recipients_for(cert.org_id)
        if not recipients:
            continue

        _remove_stale_jobs(db, "training_cert", cert.id, keep_due_at=due)

        payload = {
            "worker_name": cert.worker_name,
            "certification_type": cert.certification_type,
            "expires_at": due.isoformat(),
        }

        for recipient in recipients:
            for offset in REMINDER_OFFSETS:
                job = _upsert_job(
                    db,
                    org_id=cert.org_id,
                    target_type="training_cert",
                    target_id=cert.id,
                    due_at=due,
                    offset_days=offset,
                    recipient=recipient,
                    payload=payload,
                    now=now,
                )
                if job:
                    stats["scheduled"] += 1
                    db.add(
                        Event(
                            org_id=cert.org_id,
                            type="reminder_scheduled",
                            data={
                                "target_type": "training_cert",
                                "target_id": str(cert.id),
                                "recipient": recipient.email,
                                "offset_days": offset,
                                "run_at": job.run_at.isoformat(),
                                "expires_at": due.isoformat(),
                            },
                        )
                    )
                    record_reminder_scheduled(db, cert.org_id)

    return dict(stats)


def _render_subject(target_type: str, payload: dict, locale: str, days_out: int) -> str:
    due_en = {
        "requirement": "Compliance task",
        "permit": "Permit",
        "training_cert": "Training certification",
    }
    due_es = {
        "requirement": "Tarea de cumplimiento",
        "permit": "Permiso",
        "training_cert": "Certificación de formación",
    }
    label = due_es.get(target_type, "Recordatorio") if locale.startswith("es") else due_en.get(target_type, "Reminder")
    if days_out == 0:
        if locale.startswith("es"):
            return f"{label} vence hoy"
        return f"{label} due today"
    if locale.startswith("es"):
        return f"{label} vence en {days_out} día{'s' if days_out != 1 else ''}"
    return f"{label} due in {days_out} day{'s' if days_out != 1 else ''}"


def _render_body(
    *,
    org: Org,
    job: ReminderJob,
    payload: dict,
    locale: str,
    now: datetime,
) -> tuple[str, str | None]:
    app_url = settings.app_url.rstrip("/")
    due_at = job.target_due_at
    due_str = due_at.astimezone(timezone.utc).strftime("%Y-%m-%d") if due_at else ""
    days_out = max((due_at - now).days if due_at else 0, 0)

    if job.target_type == "requirement":
        title = payload.get("title_es" if locale.startswith("es") else "title_en")
        description = payload.get("description_es" if locale.startswith("es") else "description_en")
        if locale.startswith("es"):
            text = (
                f"Equipo de {org.name},\n\n"
                f"La tarea '{title}' vence el {due_str}.\n"
                f"Descripción: {description}\n\n"
                f"Revisa y actualiza el estado en {app_url}/requirements/{job.target_id}."
            )
        else:
            text = (
                f"{org.name} team,\n\n"
                f"The task '{title}' is due on {due_str}.\n"
                f"Details: {description}\n\n"
                f"Review and update status at {app_url}/requirements/{job.target_id}."
            )
    elif job.target_type == "permit":
        name = payload.get("name")
        permit_type = payload.get("permit_type")
        if locale.startswith("es"):
            text = (
                f"Equipo de {org.name},\n\n"
                f"El permiso '{name}' expira el {due_str}.\n"
                f"Tipo: {permit_type or 'Permiso'}.\n\n"
                f"Haz seguimiento en {app_url}/permits."
            )
        else:
            text = (
                f"{org.name} team,\n\n"
                f"Permit '{name}' expires on {due_str}.\n"
                f"Type: {permit_type or 'Permit'}.\n\n"
                f"Review details at {app_url}/permits."
            )
    else:  # training cert
        worker = payload.get("worker_name")
        cert_type = payload.get("certification_type")
        if locale.startswith("es"):
            text = (
                f"Equipo de {org.name},\n\n"
                f"La certificación '{cert_type}' de {worker} vence el {due_str}.\n"
                f"Actualiza los registros en {app_url}/training."
            )
        else:
            text = (
                f"{org.name} team,\n\n"
                f"{worker}'s '{cert_type}' certification expires on {due_str}.\n"
                f"Update records at {app_url}/training."
            )

    html = None
    return text, html


def _get_org(db: Session, org_id) -> Org:
    return db.execute(select(Org).where(Org.id == org_id)).scalar_one()


def dispatch_reminders(
    db: Session,
    *,
    now: datetime | None = None,
    email_client: EmailClient | None = None,
    batch_size: int = 50,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    client = email_client or get_email_client()
    stats = defaultdict(int)

    jobs = (
        db.query(ReminderJob)
        .filter(
            ReminderJob.status == ReminderStatusEnum.PENDING,
            ReminderJob.run_at <= now,
        )
        .order_by(ReminderJob.run_at.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .all()
    )

    if not jobs:
        return stats

    org_cache: dict = {}

    def load_org(org_id):
        if org_id not in org_cache:
            org_cache[org_id] = _get_org(db, org_id)
        return org_cache[org_id]

    for job in jobs:
        org = load_org(job.org_id)
        locale = job.recipient_locale or "en"
        target = None
        document_id = None
        if job.target_type == "requirement":
            target = db.get(Requirement, job.target_id)
            if target:
                document_id = target.document_id
        elif job.target_type == "permit":
            target = db.get(Permit, job.target_id)
        else:
            target = db.get(TrainingCert, job.target_id)

        if target is None:
            logger.warning(
                "Reminder target missing: %s %s", job.target_type, job.target_id
            )
            job.status = ReminderStatusEnum.FAILED
            job.last_attempt_at = now
            job.last_error = "Target missing"
            record_reminder_failed(db, job.org_id)
            stats["failed"] += 1
            continue

        days_out = max((job.target_due_at - now).days if job.target_due_at else 0, 0)
        subject = _render_subject(job.target_type, job.payload or {}, locale, days_out)
        text_body, html_body = _render_body(
            org=org,
            job=job,
            payload=job.payload or {},
            locale=locale,
            now=now,
        )

        message = EmailMessage(
            to=job.recipient_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )

        try:
            client.send(message)
            job.status = ReminderStatusEnum.SENT
            job.attempts = (job.attempts or 0) + 1
            job.last_attempt_at = now
            job.last_error = None
            stats["sent"] += 1
            record_reminder_sent(db, job.org_id)
            db.add(
                Event(
                    org_id=job.org_id,
                    document_id=document_id,
                    requirement_id=job.target_id if job.target_type == "requirement" else None,
                    type="reminder_sent",
                    data={
                        "target_type": job.target_type,
                        "target_id": str(job.target_id),
                        "recipient": job.recipient_email,
                        "offset_days": job.reminder_offset_days,
                        "sent_at": now.isoformat(),
                        "due_at": job.target_due_at.isoformat() if job.target_due_at else None,
                    },
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Reminder send failed: job=%s", job.id)
            job.attempts = (job.attempts or 0) + 1
            job.last_attempt_at = now
            job.last_error = str(exc)
            if job.attempts >= 3:
                job.status = ReminderStatusEnum.FAILED
                record_reminder_failed(db, job.org_id)
            else:
                job.run_at = now + timedelta(minutes=5)
            stats["failed"] += 1

    return dict(stats)


def handle_completion_metrics(db: Session, requirement: Requirement) -> None:
    record_overdue_completion(db, requirement)
    record_completion_after_reminder(db, requirement)
