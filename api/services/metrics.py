from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Dict

from sqlalchemy.orm import Session

from ..models.org_metrics import OrgRequirementMetrics
from ..models.requirements import Requirement


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

    histogram: Dict[str, int] = metrics.completion_time_histogram or {}
    bucket = _bucket_for_delta(requirement.completed_at - requirement.created_at)
    histogram[bucket] = histogram.get(bucket, 0) + 1
    metrics.completion_time_histogram = histogram
