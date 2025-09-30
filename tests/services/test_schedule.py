from datetime import datetime, timedelta, timezone

from api.services.schedule import (
    advance_due_by_frequency,
    compute_next_due,
    frequency_to_timedelta,
    next_due_from_frequency,
)


def test_frequency_to_timedelta_known_keywords() -> None:
    assert frequency_to_timedelta("daily") == timedelta(days=1)
    assert frequency_to_timedelta("Weekly inspection") == timedelta(days=7)
    assert frequency_to_timedelta("Annual check") == timedelta(days=365)


def test_frequency_to_timedelta_unknown_keyword_returns_none() -> None:
    assert frequency_to_timedelta("every full moon") is None


def test_next_due_from_frequency_uses_base_time() -> None:
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    result = next_due_from_frequency("monthly", base_time=base)
    assert result == base + timedelta(days=30)


def test_advance_due_by_frequency_rolls_forward_and_guards_loops() -> None:
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    current_due = base - timedelta(days=60)

    # Regular progression
    rolled = advance_due_by_frequency(current_due, "monthly", base_time=base)
    assert rolled > base

    # Zero/negative delta should return the base time instead of looping forever
    zero_roll = advance_due_by_frequency(current_due, "before each use", base_time=base)
    assert zero_roll == base


def test_compute_next_due_prefers_existing_future_due_date() -> None:
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    due = base + timedelta(days=10)

    assert compute_next_due(due, "weekly", base_time=base) == due


def test_compute_next_due_uses_frequency_when_due_in_past() -> None:
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    past_due = base - timedelta(days=14)

    result = compute_next_due(past_due, "weekly", base_time=base)
    assert result > base
