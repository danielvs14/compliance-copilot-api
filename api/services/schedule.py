from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
import calendar

from ..models.requirements import RequirementAnchorTypeEnum, RequirementFrequencyEnum

UTC = timezone.utc


class RecurrenceError(ValueError):
    """Raised when a recurrence configuration is invalid."""


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _start_of_day(dt: datetime) -> datetime:
    dt_utc = _ensure_utc(dt)
    return dt_utc.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_anchor_datetime(anchor_value: Mapping[str, Any] | None) -> datetime | None:
    if not anchor_value:
        return None

    value = (
        anchor_value.get("date")
        or anchor_value.get("start")
        or anchor_value.get("reference")
        or anchor_value.get("reference_date")
    )
    if value is None:
        return None

    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RecurrenceError(f"Invalid anchor datetime: {value!r}") from exc
        return _ensure_utc(parsed)

    raise RecurrenceError(f"Unsupported anchor datetime value: {value!r}")


def _derive_anchor_datetime(
    anchor_type: RequirementAnchorTypeEnum | None,
    anchor_value: Mapping[str, Any] | None,
    *,
    fallback: datetime,
    last_completion: datetime | None,
) -> datetime:
    candidate = _parse_anchor_datetime(anchor_value)

    if candidate is None:
        if anchor_type == RequirementAnchorTypeEnum.FIRST_COMPLETION and last_completion is not None:
            candidate = last_completion
        else:
            candidate = fallback

    return _ensure_utc(candidate)


def _read_interval(anchor_value: Mapping[str, Any] | None, *, key: str) -> int | None:
    if not anchor_value:
        return None
    raw = anchor_value.get(key) or anchor_value.get("interval")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RecurrenceError(f"Interval value must be an integer, received {raw!r}") from exc
    if value <= 0:
        raise RecurrenceError("Interval value must be positive")
    return value


def _add_months(base: datetime, months: int) -> datetime:
    if months <= 0:
        raise RecurrenceError("Month increment must be positive")

    base_utc = _ensure_utc(base)
    total_months = base_utc.year * 12 + (base_utc.month - 1) + months
    year = total_months // 12
    month = total_months % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(base_utc.day, last_day)
    return base_utc.replace(year=year, month=month, day=day)


def _advance_once(
    current: datetime,
    frequency: RequirementFrequencyEnum,
    anchor_value: Mapping[str, Any] | None,
) -> datetime:
    if frequency == RequirementFrequencyEnum.DAILY:
        return current + timedelta(days=1)
    if frequency == RequirementFrequencyEnum.WEEKLY:
        return current + timedelta(weeks=1)
    if frequency == RequirementFrequencyEnum.MONTHLY:
        return _add_months(current, 1)
    if frequency == RequirementFrequencyEnum.QUARTERLY:
        return _add_months(current, 3)
    if frequency == RequirementFrequencyEnum.ANNUAL:
        return _add_months(current, 12)
    if frequency == RequirementFrequencyEnum.EVERY_N_DAYS:
        interval = _read_interval(anchor_value, key="days")
        if interval is None:
            raise RecurrenceError("EVERY_N_DAYS requires an 'interval' value")
        return current + timedelta(days=interval)
    if frequency == RequirementFrequencyEnum.EVERY_N_WEEKS:
        interval = _read_interval(anchor_value, key="weeks")
        if interval is None:
            raise RecurrenceError("EVERY_N_WEEKS requires an 'interval' value")
        return current + timedelta(weeks=interval)
    if frequency == RequirementFrequencyEnum.EVERY_N_MONTHS:
        interval = _read_interval(anchor_value, key="months")
        if interval is None:
            raise RecurrenceError("EVERY_N_MONTHS requires an 'interval' value")
        return _add_months(current, interval)

    raise RecurrenceError(f"Unsupported frequency for advancement: {frequency}")


def _advance_until_after(
    start: datetime,
    reference: datetime,
    frequency: RequirementFrequencyEnum,
    anchor_value: Mapping[str, Any] | None,
) -> datetime:
    candidate = start
    loops = 0
    max_loops = 512
    while candidate <= reference and loops < max_loops:
        candidate = _advance_once(candidate, frequency, anchor_value)
        loops += 1

    if loops >= max_loops and candidate <= reference:
        raise RecurrenceError("Recurrence failed to advance beyond reference point")

    return candidate


def compute_next_due(
    frequency: RequirementFrequencyEnum | None,
    anchor_type: RequirementAnchorTypeEnum | None,
    anchor_value: Mapping[str, Any] | None,
    *,
    last_completion: datetime | None = None,
    reference_time: datetime | None = None,
) -> datetime | None:
    """Calculate the next due timestamp for a requirement.

    Args:
        frequency: The recurrence cadence to apply. ``None`` disables recurrence.
        anchor_type: The type of anchor that produced ``anchor_value``.
        anchor_value: JSON payload that contains anchor metadata. Expected keys:
            ``date``/``start``/``reference`` (ISO8601 string) and optional ``interval``
            for ``EVERY_N_*`` cadences.
        last_completion: Most recent completion timestamp (if any).
        reference_time: Time "now". Defaults to ``datetime.now(timezone.utc)``.

    Returns:
        The timestamp of the next due occurrence, or ``None`` when no future due date
        should be scheduled (e.g. ONE_TIME after completion).
    """

    if frequency is None:
        return None

    if isinstance(frequency, str):
        frequency = RequirementFrequencyEnum(frequency)

    now = _ensure_utc(reference_time or datetime.now(UTC))
    last_completion_utc = _ensure_utc(last_completion) if last_completion else None
    anchor_dict = dict(anchor_value or {})

    if frequency == RequirementFrequencyEnum.ONE_TIME:
        if last_completion_utc is not None:
            return None
        return _derive_anchor_datetime(anchor_type, anchor_dict, fallback=now, last_completion=None)

    if frequency == RequirementFrequencyEnum.BEFORE_EACH_USE:
        if last_completion_utc is None:
            return _start_of_day(now)
        return _start_of_day(last_completion_utc + timedelta(days=1))

    if last_completion_utc is None and not anchor_dict:
        return now

    anchor_dt = _derive_anchor_datetime(
        anchor_type,
        anchor_dict,
        fallback=now,
        last_completion=last_completion_utc,
    )

    reference = last_completion_utc or now
    if last_completion_utc is None and anchor_dt >= reference:
        return anchor_dt

    if last_completion_utc is not None and anchor_dt > last_completion_utc:
        return anchor_dt

    return _advance_until_after(anchor_dt, reference, frequency, anchor_dict)
