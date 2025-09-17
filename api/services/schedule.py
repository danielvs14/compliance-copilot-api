from datetime import datetime, timedelta, timezone

def next_due_from_frequency(freq: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    if not freq: return None
    f = freq.lower().strip()
    if "before each use" in f: return now  # due now
    if "daily" in f: return now + timedelta(days=1)
    if "weekly" in f: return now + timedelta(days=7)
    if "monthly" in f: return now + timedelta(days=30)
    if "annual" in f or "yearly" in f: return now + timedelta(days=365)
    return None
