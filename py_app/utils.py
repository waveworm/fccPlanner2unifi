from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 string (with optional trailing Z) into a UTC-aware datetime.

    Returns None for missing, non-string, or unparseable values.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None
