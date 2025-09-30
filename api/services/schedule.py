from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable


def _frequency_keywords() -> Iterable[tuple[str, timedelta]]:
    return (
        ("before each use", timedelta(hours=0)),
        ("daily", timedelta(days=1)),
        ("weekly", timedelta(days=7)),
        ("monthly", timedelta(days=30)),
        ("annual", timedelta(days=365)),
        ("yearly", timedelta(days=365)),
    )


def frequency_to_timedelta(freq: str | None) -> timedelta | None:
    if not freq:
        return None

    f = freq.lower().strip()
    for keyword, delta in _frequency_keywords():
        if keyword in f:
            return delta
    return None


def next_due_from_frequency(freq: str | None, *, base_time: datetime | None = None) -> datetime | None:
    delta = frequency_to_timedelta(freq)
    if delta is None:
        return None

    now = base_time or datetime.now(timezone.utc)
    return now + delta


def advance_due_by_frequency(current_due: datetime, freq: str | None, *, base_time: datetime | None = None) -> datetime | None:
    delta = frequency_to_timedelta(freq)
    if delta is None:
        return None

    base = base_time or datetime.now(timezone.utc)
    if delta <= timedelta(0):
        return base

    candidate = current_due
    safety_counter = 0
    while candidate <= base and safety_counter < 1024:
        candidate = candidate + delta
        safety_counter += 1
    if safety_counter >= 1024 and candidate <= base:
        return base
    return candidate


def compute_next_due(
    due_date: datetime | None,
    frequency: str | None,
    *,
    base_time: datetime | None = None,
) -> datetime | None:
    base = base_time or datetime.now(timezone.utc)

    if due_date and due_date >= base:
        return due_date

    if due_date and frequency:
        advanced = advance_due_by_frequency(due_date, frequency, base_time=base)
        if advanced:
            return advanced

    return next_due_from_frequency(frequency, base_time=base)
