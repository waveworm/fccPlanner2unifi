from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "schedule": {day: {"ranges": "", "doors": []} for day in DAYS},
}


def load_office_hours(file_path: str) -> dict[str, Any]:
    """Load office hours config from disk. Returns default config if file missing or invalid."""
    path = Path(file_path)
    if not path.exists():
        return json.loads(json.dumps(_DEFAULT_CONFIG))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return json.loads(json.dumps(_DEFAULT_CONFIG))


def save_office_hours(file_path: str, data: dict[str, Any]) -> None:
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def validate_office_hours(data: Any) -> str | None:
    """Return an error message if the payload is invalid, else None."""
    if not isinstance(data, dict):
        return "Payload must be a JSON object"
    if "enabled" not in data or not isinstance(data["enabled"], bool):
        return "'enabled' must be a boolean"
    schedule = data.get("schedule")
    if not isinstance(schedule, dict):
        return "'schedule' must be an object"
    for day in DAYS:
        if day not in schedule:
            return f"'schedule' is missing day: {day}"
        day_cfg = schedule[day]
        if not isinstance(day_cfg, dict):
            return f"'schedule.{day}' must be an object"
        if not isinstance(day_cfg.get("ranges", ""), str):
            return f"'schedule.{day}.ranges' must be a string"
        if not isinstance(day_cfg.get("doors", []), list):
            return f"'schedule.{day}.doors' must be an array"
    return None


def parse_time_ranges(text: str) -> list[tuple[str, str]]:
    """Parse a time range string into a list of (start, end) HH:MM tuples.

    Supported formats (comma- or semicolon-separated):
      - "9:00-17:00"
      - "8:00-12:00, 13:00-17:00"
      - "8-12"  (whole hours, no colon)
      - "8–12"  (en-dash)
    Invalid entries are silently skipped.
    """
    ranges: list[tuple[str, str]] = []
    for part in re.split(r"[,;]", text):
        part = part.strip()
        if not part:
            continue
        m = re.match(
            r"^(\d{1,2})(?::(\d{2}))?\s*[-\u2013]\s*(\d{1,2})(?::(\d{2}))?$", part
        )
        if not m:
            continue
        sh, sm, eh, em = m.groups()
        sh, sm, eh, em = int(sh), int(sm or 0), int(eh), int(em or 0)
        if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
            continue
        ranges.append((f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}"))
    return ranges


def load_cancelled_office_hours(file_path: str) -> set[str]:
    """Load the set of cancelled office hours date strings (YYYY-MM-DD)."""
    path = Path(file_path)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("dates") or [])
    except Exception:
        return set()


def _save_cancelled_office_hours(file_path: str, dates: set[str]) -> None:
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Auto-prune dates that are in the past
    today = datetime.now(timezone.utc).date().isoformat()
    pruned = {d for d in dates if d >= today}
    path.write_text(json.dumps({"dates": sorted(pruned)}, indent=2) + "\n", encoding="utf-8")


def add_cancelled_office_hours_date(file_path: str, date_str: str) -> None:
    dates = load_cancelled_office_hours(file_path)
    dates.add(date_str)
    _save_cancelled_office_hours(file_path, dates)


def remove_cancelled_office_hours_date(file_path: str, date_str: str) -> None:
    dates = load_cancelled_office_hours(file_path)
    dates.discard(date_str)
    _save_cancelled_office_hours(file_path, dates)


def get_office_hours_instances(
    config: dict[str, Any],
    from_dt: datetime,
    to_dt: datetime,
    local_tz: ZoneInfo,
    cancelled_dates: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return synthetic event-like dicts for each upcoming office hours day (for dashboard display)."""
    if not config.get("enabled"):
        return []
    cancelled = cancelled_dates or set()
    schedule = config.get("schedule") or {}
    instances = []

    current_date = from_dt.astimezone(local_tz).date()
    end_date = to_dt.astimezone(local_tz).date()

    while current_date <= end_date:
        date_str = current_date.isoformat()
        day_name = DAYS[current_date.weekday()]
        day_cfg = schedule.get(day_name) or {}
        ranges_text = (day_cfg.get("ranges") or "").strip()
        door_keys: list[str] = [str(d) for d in (day_cfg.get("doors") or []) if d]

        if ranges_text and door_keys and date_str not in cancelled:
            ranges = parse_time_ranges(ranges_text)
            if ranges:
                sh, sm = map(int, ranges[0][0].split(":"))
                eh, em = map(int, ranges[-1][1].split(":"))
                local_start = datetime(
                    current_date.year, current_date.month, current_date.day,
                    sh, sm, 0, tzinfo=local_tz,
                )
                local_end = datetime(
                    current_date.year, current_date.month, current_date.day,
                    eh, em, 0, tzinfo=local_tz,
                )
                instances.append({
                    "id": f"office-hours-{date_str}",
                    "name": "Office Hours",
                    "startAt": local_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "endAt": local_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "room": ranges_text,
                    "rooms": [],
                    "building": "",
                    "locationRaw": "",
                    "type": "office_hours",
                    "dateStr": date_str,
                    "timeRanges": ranges_text,
                    "doors": door_keys,
                })

        current_date += timedelta(days=1)

    return instances


def build_office_hours_windows(
    config: dict[str, Any],
    from_dt: datetime,
    to_dt: datetime,
    local_tz: ZoneInfo,
    doors_map: dict[str, Any],
    cancelled_dates: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Generate door-unlock windows for every date in [from_dt, to_dt] where office hours apply.

    Windows are returned in the same format as `doorWindows` in the desired schedule dict,
    ready to be merged into the PCO-event-based windows.
    """
    if not config.get("enabled"):
        return []

    cancelled = cancelled_dates or set()
    schedule = config.get("schedule") or {}
    windows: list[dict[str, Any]] = []

    current_date = from_dt.astimezone(local_tz).date()
    end_date = to_dt.astimezone(local_tz).date()

    while current_date <= end_date:
        day_name = DAYS[current_date.weekday()]
        day_cfg = schedule.get(day_name) or {}
        ranges_text = (day_cfg.get("ranges") or "").strip()
        door_keys: list[str] = [str(d) for d in (day_cfg.get("doors") or []) if d]

        if not ranges_text or not door_keys or current_date.isoformat() in cancelled:
            current_date += timedelta(days=1)
            continue

        for start_str, end_str in parse_time_ranges(ranges_text):
            sh, sm = map(int, start_str.split(":"))
            eh, em = map(int, end_str.split(":"))

            local_start = datetime(
                current_date.year, current_date.month, current_date.day,
                sh, sm, 0, tzinfo=local_tz,
            )
            local_end = datetime(
                current_date.year, current_date.month, current_date.day,
                eh, em, 0, tzinfo=local_tz,
            )
            start_utc = local_start.astimezone(timezone.utc)
            end_utc = local_end.astimezone(timezone.utc)

            for door_key in door_keys:
                door = doors_map.get(door_key)
                if not door:
                    continue
                windows.append({
                    "doorKey": door_key,
                    "doorLabel": door.get("label", door_key),
                    "unifiDoorIds": door.get("unifiDoorIds") or [],
                    "openStart": start_utc.isoformat().replace("+00:00", "Z"),
                    "openEnd": end_utc.isoformat().replace("+00:00", "Z"),
                    "sourceEventIds": ["office-hours"],
                    "sourceEventNames": ["Office Hours"],
                    "sourceRooms": ["Office Hours"],
                })

        current_date += timedelta(days=1)

    return windows


def merge_office_hours_into_desired(
    desired: dict[str, Any],
    office_windows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge office hours windows into the desired schedule's doorWindows list.

    Re-merges overlapping windows per door so the combined result stays clean.
    """
    if not office_windows:
        return desired

    from py_app.mapping import _merge_windows  # reuse existing merge logic

    all_by_door: dict[str, list[dict[str, Any]]] = {}
    for w in (desired.get("doorWindows") or []):
        dk = str(w.get("doorKey") or "").strip()
        if dk:
            all_by_door.setdefault(dk, []).append(w)
    for w in office_windows:
        dk = str(w.get("doorKey") or "").strip()
        if dk:
            all_by_door.setdefault(dk, []).append(w)

    merged: list[dict[str, Any]] = []
    for dk in sorted(all_by_door.keys()):
        merged.extend(_merge_windows(all_by_door[dk]))

    return {**desired, "doorWindows": merged}
