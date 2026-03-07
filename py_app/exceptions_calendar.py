from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from py_app.utils import parse_iso

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def load_exception_calendar(file_path: str) -> dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return {"entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": []}
    return data if isinstance(data, dict) else {"entries": []}


def save_exception_calendar(file_path: str, data: dict[str, Any]) -> None:
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def list_exception_entries(file_path: str) -> list[dict[str, Any]]:
    data = load_exception_calendar(file_path)
    entries = _prune_entries(data.get("entries") or [])
    if len(entries) != len(data.get("entries") or []):
        save_exception_calendar(file_path, {"entries": entries})
    return entries


def create_exception_entry(
    file_path: str,
    *,
    kind: str,
    from_date_str: str,
    to_date_str: str,
    door_keys: list[str],
    label: str,
    note: str = "",
    start_time: str = "",
    end_time: str = "",
) -> dict[str, Any]:
    entries = list_exception_entries(file_path)
    entry = {
        "id": uuid4().hex,
        "kind": str(kind).strip(),
        "date": str(from_date_str).strip(),
        "fromDate": str(from_date_str).strip(),
        "toDate": str(to_date_str).strip(),
        "doorKeys": [str(k).strip() for k in door_keys if str(k).strip()],
        "label": str(label).strip(),
        "note": str(note or "").strip(),
        "startTime": str(start_time or "").strip(),
        "endTime": str(end_time or "").strip(),
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    entries.append(entry)
    entries.sort(key=lambda row: (_sort_date_key(row), str(row.get("startTime") or ""), str(row.get("label") or "")))
    save_exception_calendar(file_path, {"entries": entries})
    return entry


def delete_exception_entry(file_path: str, entry_id: str) -> dict[str, Any] | None:
    entries = list_exception_entries(file_path)
    removed: dict[str, Any] | None = None
    kept: list[dict[str, Any]] = []
    for entry in entries:
        if removed is None and str(entry.get("id") or "") == entry_id:
            removed = entry
            continue
        kept.append(entry)
    save_exception_calendar(file_path, {"entries": kept})
    return removed


def validate_exception_entry(
    *,
    kind: str,
    from_date_str: str,
    to_date_str: str,
    door_keys: list[str],
    label: str,
    start_time: str = "",
    end_time: str = "",
) -> str | None:
    normalized_kind = str(kind).strip()
    if normalized_kind not in {"closure", "special_open"}:
        return "Type must be closure or special_open"
    try:
        start_date = date.fromisoformat(str(from_date_str).strip())
    except Exception:
        return "From date must be YYYY-MM-DD"
    try:
        end_date = date.fromisoformat(str(to_date_str or from_date_str).strip())
    except Exception:
        return "To date must be YYYY-MM-DD"
    if end_date < start_date:
        return "To date must be the same as or after from date"
    if not str(label).strip():
        return "Title is required"
    if normalized_kind == "special_open":
        if not _TIME_RE.match(str(start_time).strip()) or not _TIME_RE.match(str(end_time).strip()):
            return "Extra office-hours start and end times must be HH:MM"
        if _parse_time(str(end_time).strip()) <= _parse_time(str(start_time).strip()):
            return "Extra office-hours end time must be after start time"
    else:
        if start_time or end_time:
            return "Closure entries do not use start/end times"
    return None


def build_exception_instances(
    entries: list[dict[str, Any]],
    *,
    from_dt: datetime,
    to_dt: datetime,
    local_tz: ZoneInfo,
    doors_map: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in entries:
        kind = str(entry.get("kind") or "")
        target_keys = _resolved_door_keys(entry, doors_map)
        for entry_date in _entry_dates(entry):
            if kind == "closure":
                local_start = datetime.combine(entry_date, time(0, 0), tzinfo=local_tz)
                local_end = local_start + timedelta(days=1)
                if local_end.astimezone(timezone.utc) <= from_dt or local_start.astimezone(timezone.utc) >= to_dt:
                    continue
                out.append({
                    "id": f"exception-{str(entry.get('id') or '')}-{entry_date.isoformat()}",
                    "name": str(entry.get("label") or "Closure"),
                    "startAt": local_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "endAt": local_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "room": str(entry.get("note") or "All-day closure"),
                    "rooms": [],
                    "building": "",
                    "locationRaw": "",
                    "type": "exception_closure",
                    "dateStr": entry_date.isoformat(),
                    "doors": target_keys,
                    "note": str(entry.get("note") or ""),
                })
                continue

            if kind == "special_open":
                start_local = _entry_local_datetime(entry_date, str(entry.get("startTime") or ""), local_tz)
                end_local = _entry_local_datetime(entry_date, str(entry.get("endTime") or ""), local_tz)
                if start_local is None or end_local is None:
                    continue
                start_utc = start_local.astimezone(timezone.utc)
                end_utc = end_local.astimezone(timezone.utc)
                if end_utc <= from_dt or start_utc >= to_dt:
                    continue
                out.append({
                    "id": f"exception-{str(entry.get('id') or '')}-{entry_date.isoformat()}",
                    "name": str(entry.get("label") or "Extra Office Hours"),
                    "startAt": start_utc.isoformat().replace("+00:00", "Z"),
                    "endAt": end_utc.isoformat().replace("+00:00", "Z"),
                    "room": str(entry.get("note") or "Office Hours Calendar"),
                    "rooms": [],
                    "building": "",
                    "locationRaw": "",
                    "type": "exception_open",
                    "dateStr": entry_date.isoformat(),
                    "doors": target_keys,
                    "note": str(entry.get("note") or ""),
                })
    out.sort(key=lambda row: str(row.get("startAt") or ""))
    return out


def apply_office_hours_exceptions_to_instances(
    instances: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    *,
    local_tz: ZoneInfo,
    doors_map: dict[str, Any],
) -> list[dict[str, Any]]:
    closures_by_date: dict[str, set[str]] = {}
    for entry in entries:
        if str(entry.get("kind") or "") != "closure":
            continue
        for entry_date in _entry_dates(entry):
            closures_by_date.setdefault(entry_date.isoformat(), set()).update(
                _resolved_door_keys(entry, doors_map)
            )

    if not closures_by_date:
        return instances

    filtered: list[dict[str, Any]] = []
    for instance in instances:
        date_str = str(instance.get("dateStr") or "").strip()
        if not date_str:
            start_dt = parse_iso(instance.get("startAt"))
            if start_dt is not None:
                date_str = start_dt.astimezone(local_tz).date().isoformat()
        blocked = closures_by_date.get(date_str) or set()
        if not blocked:
            filtered.append(instance)
            continue
        doors = [str(dk).strip() for dk in (instance.get("doors") or []) if str(dk).strip()]
        remaining = [dk for dk in doors if dk not in blocked]
        if not remaining:
            continue
        filtered.append({**instance, "doors": remaining})
    return filtered


def build_special_open_windows(
    entries: list[dict[str, Any]],
    *,
    from_dt: datetime,
    to_dt: datetime,
    local_tz: ZoneInfo,
    doors_map: dict[str, Any],
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for entry in entries:
        if str(entry.get("kind") or "") != "special_open":
            continue
        title = str(entry.get("label") or "Extra Office Hours").strip()
        for entry_date in _entry_dates(entry):
            start_local = _entry_local_datetime(entry_date, str(entry.get("startTime") or ""), local_tz)
            end_local = _entry_local_datetime(entry_date, str(entry.get("endTime") or ""), local_tz)
            if start_local is None or end_local is None or end_local <= start_local:
                continue
            start_utc = start_local.astimezone(timezone.utc)
            end_utc = end_local.astimezone(timezone.utc)
            if end_utc <= from_dt or start_utc >= to_dt:
                continue
            for door_key in _resolved_door_keys(entry, doors_map):
                door = doors_map.get(door_key)
                if not door:
                    continue
                windows.append({
                    "doorKey": door_key,
                    "doorLabel": door.get("label", door_key),
                    "unifiDoorIds": door.get("unifiDoorIds") or [],
                    "openStart": max(start_utc, from_dt).isoformat().replace("+00:00", "Z"),
                    "openEnd": min(end_utc, to_dt).isoformat().replace("+00:00", "Z"),
                    "sourceEventIds": [f"exception-open:{str(entry.get('id') or '')}:{entry_date.isoformat()}"],
                    "sourceEventNames": [f"Exception: {title}"],
                    "sourceRooms": ["Office Hours Calendar"],
                })
    return windows


def apply_closure_exceptions(
    desired: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    from_dt: datetime,
    to_dt: datetime,
    local_tz: ZoneInfo,
    doors_map: dict[str, Any],
) -> dict[str, Any]:
    closures_by_door: dict[str, list[tuple[datetime, datetime]]] = {}
    for entry in entries:
        if str(entry.get("kind") or "") != "closure":
            continue
        for entry_date in _entry_dates(entry):
            local_start = datetime.combine(entry_date, time(0, 0), tzinfo=local_tz)
            local_end = local_start + timedelta(days=1)
            start_utc = local_start.astimezone(timezone.utc)
            end_utc = local_end.astimezone(timezone.utc)
            if end_utc <= from_dt or start_utc >= to_dt:
                continue
            for door_key in _resolved_door_keys(entry, doors_map):
                closures_by_door.setdefault(door_key, []).append((start_utc, end_utc))

    if not closures_by_door:
        return desired

    new_windows: list[dict[str, Any]] = []
    for window in desired.get("doorWindows") or []:
        door_key = str(window.get("doorKey") or "")
        intervals = closures_by_door.get(door_key) or []
        if not intervals:
            new_windows.append(window)
            continue
        segments = [window]
        for cut_start, cut_end in sorted(intervals, key=lambda pair: pair[0]):
            next_segments: list[dict[str, Any]] = []
            for segment in segments:
                next_segments.extend(_subtract_interval(segment, cut_start, cut_end))
            segments = next_segments
            if not segments:
                break
        new_windows.extend(segments)

    return {**desired, "doorWindows": new_windows}


def _subtract_interval(window: dict[str, Any], cut_start: datetime, cut_end: datetime) -> list[dict[str, Any]]:
    start_dt = parse_iso(window.get("openStart"))
    end_dt = parse_iso(window.get("openEnd"))
    if start_dt is None or end_dt is None or cut_end <= start_dt or cut_start >= end_dt:
        return [window]

    kept: list[dict[str, Any]] = []
    if cut_start > start_dt:
        kept.append({
            **window,
            "openStart": start_dt.isoformat().replace("+00:00", "Z"),
            "openEnd": min(cut_start, end_dt).isoformat().replace("+00:00", "Z"),
        })
    if cut_end < end_dt:
        kept.append({
            **window,
            "openStart": max(cut_end, start_dt).isoformat().replace("+00:00", "Z"),
            "openEnd": end_dt.isoformat().replace("+00:00", "Z"),
        })
    return [segment for segment in kept if parse_iso(segment.get("openEnd")) > parse_iso(segment.get("openStart"))]


def _resolved_door_keys(entry: dict[str, Any], doors_map: dict[str, Any]) -> list[str]:
    raw_keys = [str(k).strip() for k in (entry.get("doorKeys") or []) if str(k).strip()]
    if raw_keys:
        return [k for k in raw_keys if k in doors_map]
    return list(doors_map.keys())


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip())
    except Exception:
        return None


def _entry_start_date(entry: dict[str, Any]) -> date | None:
    return _parse_date(entry.get("fromDate") or entry.get("date"))


def _entry_end_date(entry: dict[str, Any]) -> date | None:
    return _parse_date(entry.get("toDate") or entry.get("fromDate") or entry.get("date"))


def _entry_dates(entry: dict[str, Any]) -> list[date]:
    start_date = _entry_start_date(entry)
    end_date = _entry_end_date(entry)
    if start_date is None or end_date is None or end_date < start_date:
        return []
    dates: list[date] = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)
    return dates


def _parse_time(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _entry_local_datetime(entry_date: date, hhmm: str, local_tz: ZoneInfo) -> datetime | None:
    if not _TIME_RE.match(str(hhmm).strip()):
        return None
    hours, minutes = map(int, str(hhmm).strip().split(":"))
    return datetime.combine(entry_date, time(hours, minutes), tzinfo=local_tz)


def _prune_entries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
    kept: list[dict[str, Any]] = []
    for entry in items:
        end_date = _entry_end_date(entry)
        if end_date is None or end_date >= cutoff:
            kept.append(entry)
    kept.sort(key=lambda row: (_sort_date_key(row), str(row.get("startTime") or ""), str(row.get("label") or "")))
    return kept


def _sort_date_key(entry: dict[str, Any]) -> str:
    return str(entry.get("fromDate") or entry.get("date") or "")
