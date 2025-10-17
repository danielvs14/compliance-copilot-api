from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.models.requirements import RequirementAnchorTypeEnum, RequirementFrequencyEnum
from api.services.schedule import RecurrenceError, compute_next_due

UTC = timezone.utc


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def test_one_time_before_and_after_completion() -> None:
    anchor = {"date": "2024-01-15T09:00:00Z"}
    due = compute_next_due(
        RequirementFrequencyEnum.ONE_TIME,
        RequirementAnchorTypeEnum.CUSTOM_DATE,
        anchor,
        reference_time=_dt("2024-01-01T00:00:00Z"),
    )
    assert due == _dt("2024-01-15T09:00:00Z")

    after_completion = compute_next_due(
        RequirementFrequencyEnum.ONE_TIME,
        RequirementAnchorTypeEnum.CUSTOM_DATE,
        anchor,
        last_completion=_dt("2024-01-16T12:00:00Z"),
    )
    assert after_completion is None


def test_before_each_use_rolls_to_next_day() -> None:
    now = _dt("2024-02-01T13:30:00Z")
    first_due = compute_next_due(
        RequirementFrequencyEnum.BEFORE_EACH_USE,
        RequirementAnchorTypeEnum.UPLOAD_DATE,
        {"date": now.isoformat()},
        reference_time=now,
    )
    assert first_due == datetime(2024, 2, 1, tzinfo=UTC)

    completion = _dt("2024-02-01T21:00:00Z")
    next_due = compute_next_due(
        RequirementFrequencyEnum.BEFORE_EACH_USE,
        RequirementAnchorTypeEnum.UPLOAD_DATE,
        {"date": now.isoformat()},
        last_completion=completion,
    )
    assert next_due == datetime(2024, 2, 2, tzinfo=UTC)


def test_monthly_anchor_advances_past_completion() -> None:
    anchor = {"date": "2023-12-15T08:00:00Z"}
    completion = _dt("2024-03-10T12:00:00Z")
    next_due = compute_next_due(
        RequirementFrequencyEnum.MONTHLY,
        RequirementAnchorTypeEnum.CUSTOM_DATE,
        anchor,
        last_completion=completion,
    )
    assert next_due == _dt("2024-03-15T08:00:00Z")


def test_weekly_anchor_skips_over_reference_time() -> None:
    anchor = {"date": "2024-01-01T10:00:00Z"}
    completion = _dt("2024-01-20T09:30:00Z")
    next_due = compute_next_due(
        RequirementFrequencyEnum.WEEKLY,
        RequirementAnchorTypeEnum.CUSTOM_DATE,
        anchor,
        last_completion=completion,
    )
    assert next_due == _dt("2024-01-22T10:00:00Z")


def test_every_n_days_requires_interval() -> None:
    anchor = {"date": "2024-05-01T00:00:00Z", "interval": 5}
    completion = _dt("2024-05-06T12:00:00Z")
    next_due = compute_next_due(
        RequirementFrequencyEnum.EVERY_N_DAYS,
        RequirementAnchorTypeEnum.CUSTOM_DATE,
        anchor,
        last_completion=completion,
    )
    assert next_due == _dt("2024-05-11T00:00:00Z")

    with pytest.raises(RecurrenceError):
        compute_next_due(
            RequirementFrequencyEnum.EVERY_N_DAYS,
            RequirementAnchorTypeEnum.CUSTOM_DATE,
            {"date": "2024-05-01T00:00:00Z"},
            last_completion=completion,
        )


def test_first_completion_anchor_sets_reference_when_missing() -> None:
    completion = _dt("2024-06-01T08:00:00Z")
    next_due = compute_next_due(
        RequirementFrequencyEnum.ANNUAL,
        RequirementAnchorTypeEnum.FIRST_COMPLETION,
        {},
        last_completion=completion,
    )
    assert next_due == _dt("2025-06-01T08:00:00Z")


def test_missing_anchor_uses_reference_time() -> None:
    now = _dt("2024-08-01T00:00:00Z")
    next_due = compute_next_due(
        RequirementFrequencyEnum.DAILY,
        None,
        {},
        reference_time=now,
    )
    assert next_due == now


def test_every_n_months_advances_month_boundary() -> None:
    anchor = {"date": "2024-01-31T00:00:00Z", "interval": 2}
    completion = _dt("2024-03-05T00:00:00Z")
    next_due = compute_next_due(
        RequirementFrequencyEnum.EVERY_N_MONTHS,
        RequirementAnchorTypeEnum.CUSTOM_DATE,
        anchor,
        last_completion=completion,
    )
    assert next_due == _dt("2024-03-31T00:00:00Z")


def test_invalid_interval_raises_error() -> None:
    with pytest.raises(RecurrenceError):
        compute_next_due(
            RequirementFrequencyEnum.EVERY_N_WEEKS,
            RequirementAnchorTypeEnum.CUSTOM_DATE,
            {"date": "2024-04-01T00:00:00Z", "interval": 0},
            last_completion=_dt("2024-04-01T00:00:00Z"),
        )


def test_last_completion_prior_to_anchor_uses_anchor_date() -> None:
    anchor = {"date": "2024-03-01T00:00:00Z"}
    last_completion = _dt("2024-02-20T00:00:00Z")
    due = compute_next_due(
        RequirementFrequencyEnum.MONTHLY,
        RequirementAnchorTypeEnum.CUSTOM_DATE,
        anchor,
        last_completion=last_completion,
    )
    assert due == _dt("2024-03-01T00:00:00Z")
