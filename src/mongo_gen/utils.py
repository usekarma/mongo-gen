from __future__ import annotations
from datetime import datetime, timedelta, timezone
import re

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def parse_iso_utc(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp must be timezone-aware (got naive): {s}")
    return dt.astimezone(timezone.utc)

_DURATION_RE = re.compile(r"^(\d+)(ms|s|m|h|d)$", re.IGNORECASE)

def parse_duration(s: str) -> timedelta:
    m = _DURATION_RE.match(s.strip())
    if not m:
        raise ValueError(f"Invalid duration '{s}'. Use like 500ms, 10s, 5m, 3h, 1d")
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "ms":
        return timedelta(milliseconds=n)
    if unit == "s":
        return timedelta(seconds=n)
    if unit == "m":
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)
    if unit == "d":
        return timedelta(days=n)
    raise ValueError(f"Unknown duration unit: {unit}")

def iso_z(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
