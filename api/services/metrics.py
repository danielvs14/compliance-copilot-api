from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Dict

from prometheus_client import Counter
from sqlalchemy.orm import Session

from ..models.org_metrics import OrgRequirementMetrics
from ..models.reminder_jobs import ReminderJob, ReminderStatusEnum
from ..models.requirements import Requirement


REQUIREMENTS_CREATED_COUNTER = Counter(
    "cc_requirements_created_total",
    "Total requirements created per org",
    ["org_id"],
)

REQUIREMENTS_COMPLETED_COUNTER = Counter(
    "cc_requirements_completed_total",
    "Total requirements completed per org",
    ["org_id"],
)

REMINDERS_SCHEDULED_COUNTER = Counter(
    "cc_reminders_scheduled_total",
    "Reminders scheduled per org",
    ["org_id"],
)

REMINDERS_SENT_COUNTER = Counter(
    "cc_reminders_sent_total",
    "Reminders successfully sent per org",
    ["org_id"],
)

REMINDERS_FAILED_COUNTER = Counter(
    "cc_reminders_failed_total",
    "Reminders failed per org",
    ["org_id"],
)

OVERDUE_COMPLETIONS_COUNTER = Counter(
    "cc_overdue_completions_total",
    "Requirements completed after due date per org",
    ["org_id"],
)

POST_REMINDER_COMPLETIONS_COUNTER = Counter(
    "cc_post_reminder_completions_total",
    "Requirements completed after reminder per org",
    ["org_id"],
)


def _org_label(org_id: uuid.UUID | None) -> str:
    return str(org_id) if org_id else "unknown"


def _get_metrics_row(db: Session, org_id: uuid.UUID) -> OrgRequirementMetrics:
    metrics = (
        db.query(OrgRequirementMetrics)
        .filter(OrgRequirementMetrics.org_id == org_id)
        .with_for_update(nowait=False)
        .one_or_none()
    )
    if metrics is None:
        metrics = OrgRequirementMetrics(org_id=org_id)
        db.add(metrics)
        db.flush()
    return metrics


def record_requirements_created(db: Session, org_id: uuid.UUID, count: int) -> None:
    if count <= 0:
        return
    metrics = _get_metrics_row(db, org_id)
    current = metrics.requirements_created_total or 0
    metrics.requirements_created_total = current + count
    REQUIREMENTS_CREATED_COUNTER.labels(org_id=_org_label(org_id)).inc(count)


def _bucket_for_delta(delta: timedelta) -> str:
    total_days = delta.total_seconds() / 86400
    if total_days < 1:
        return "<1d"
    if total_days < 7:
        return "1-7d"
    if total_days < 30:
        return "7-30d"
    return ">=30d"


def record_requirement_completed(db: Session, requirement: Requirement) -> None:
    if not requirement.completed_at or not requirement.created_at:
        return
    if requirement.org_id is None:
        return

    metrics = _get_metrics_row(db, requirement.org_id)
    metrics.requirements_completed_total = (metrics.requirements_completed_total or 0) + 1
    REQUIREMENTS_COMPLETED_COUNTER.labels(org_id=_org_label(requirement.org_id)).inc()

    histogram: Dict[str, int] = metrics.completion_time_histogram or {}
    bucket = _bucket_for_delta(requirement.completed_at - requirement.created_at)
    histogram[bucket] = histogram.get(bucket, 0) + 1
    metrics.completion_time_histogram = histogram


def record_reminder_scheduled(db: Session, org_id: uuid.UUID) -> None:
    metrics = _get_metrics_row(db, org_id)
    metrics.reminders_scheduled_total = (metrics.reminders_scheduled_total or 0) + 1
    REMINDERS_SCHEDULED_COUNTER.labels(org_id=_org_label(org_id)).inc()


def record_reminder_sent(db: Session, org_id: uuid.UUID) -> None:
    metrics = _get_metrics_row(db, org_id)
    metrics.reminders_sent_total = (metrics.reminders_sent_total or 0) + 1
    REMINDERS_SENT_COUNTER.labels(org_id=_org_label(org_id)).inc()


def record_reminder_failed(db: Session, org_id: uuid.UUID) -> None:
    metrics = _get_metrics_row(db, org_id)
    metrics.reminders_failed_total = (metrics.reminders_failed_total or 0) + 1
    REMINDERS_FAILED_COUNTER.labels(org_id=_org_label(org_id)).inc()


def record_overdue_completion(db: Session, requirement: Requirement) -> None:
    if not requirement.completed_at or not requirement.due_date or not requirement.org_id:
        return

    if requirement.completed_at <= requirement.due_date:
        return

    metrics = _get_metrics_row(db, requirement.org_id)
    metrics.overdue_completion_total = (metrics.overdue_completion_total or 0) + 1
    OVERDUE_COMPLETIONS_COUNTER.labels(org_id=_org_label(requirement.org_id)).inc()

    histogram: Dict[str, int] = metrics.overdue_completion_histogram or {}
    bucket = _bucket_for_delta(requirement.completed_at - requirement.due_date)
    histogram[bucket] = histogram.get(bucket, 0) + 1
    metrics.overdue_completion_histogram = histogram


def record_completion_after_reminder(db: Session, requirement: Requirement) -> None:
    if not requirement.completed_at or not requirement.org_id:
        return

    reminder = (
        db.query(ReminderJob)
        .filter(
            ReminderJob.target_type == "requirement",
            ReminderJob.target_id == requirement.id,
            ReminderJob.status == ReminderStatusEnum.SENT,
        )
        .order_by(ReminderJob.last_attempt_at.desc().nullslast(), ReminderJob.updated_at.desc())
        .first()
    )

    if not reminder:
        return

    sent_at: datetime | None = reminder.last_attempt_at or reminder.updated_at or reminder.created_at
    if not sent_at or sent_at > requirement.completed_at:
        return

    metrics = _get_metrics_row(db, requirement.org_id)
    metrics.post_reminder_completion_total = (metrics.post_reminder_completion_total or 0) + 1
    POST_REMINDER_COMPLETIONS_COUNTER.labels(org_id=_org_label(requirement.org_id)).inc()

    histogram: Dict[str, int] = metrics.post_reminder_completion_histogram or {}
    bucket = _bucket_for_delta(requirement.completed_at - sent_at)
    histogram[bucket] = histogram.get(bucket, 0) + 1
    metrics.post_reminder_completion_histogram = histogram
