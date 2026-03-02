from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from py_app.utils import parse_iso


def load_manual_access(file_path: str) -> dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return {"windows": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"windows": []}
    return data if isinstance(data, dict) else {"windows": []}


def save_manual_access(file_path: str, data: dict[str, Any]) -> None:
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def list_manual_access(file_path: str) -> list[dict[str, Any]]:
    data = load_manual_access(file_path)
    windows = _prune_windows(data.get("windows") or [])
    if len(windows) != len(data.get("windows") or []):
        save_manual_access(file_path, {"windows": windows})
    return windows


def create_manual_access_entry(
    file_path: str,
    *,
    door_keys: list[str],
    start_at: str,
    end_at: str,
    note: str = "",
) -> dict[str, Any]:
    windows = list_manual_access(file_path)
    entry = {
        "id": uuid4().hex,
        "doorKeys": [str(k).strip() for k in door_keys if str(k).strip()],
        "startAt": str(start_at).strip(),
        "endAt": str(end_at).strip(),
        "note": str(note or "").strip(),
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    windows.append(entry)
    windows.sort(key=lambda row: parse_iso(row.get("startAt")) or datetime.max.replace(tzinfo=timezone.utc))
    save_manual_access(file_path, {"windows": windows})
    return entry


def cancel_manual_access_entry(file_path: str, entry_id: str) -> dict[str, Any] | None:
    windows = list_manual_access(file_path)
    removed: dict[str, Any] | None = None
    kept: list[dict[str, Any]] = []
    for row in windows:
        if removed is None and str(row.get("id") or "") == entry_id:
            removed = row
            continue
        kept.append(row)
    save_manual_access(file_path, {"windows": kept})
    return removed


def build_manual_access_windows(
    entries: list[dict[str, Any]],
    *,
    from_dt: datetime,
    to_dt: datetime,
    doors_map: dict[str, Any],
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for entry in entries:
        start_dt = parse_iso(entry.get("startAt"))
        end_dt = parse_iso(entry.get("endAt"))
        if start_dt is None or end_dt is None or end_dt <= start_dt:
            continue
        if end_dt <= from_dt or start_dt >= to_dt:
            continue

        open_start = max(start_dt, from_dt)
        open_end = min(end_dt, to_dt)
        note = str(entry.get("note") or "").strip()
        label = f"Manual Access: {note}" if note else "Manual Access"
        entry_id = str(entry.get("id") or "")
        for door_key in [str(k).strip() for k in (entry.get("doorKeys") or []) if str(k).strip()]:
            door = doors_map.get(door_key)
            if not door:
                continue
            windows.append({
                "doorKey": door_key,
                "doorLabel": door.get("label", door_key),
                "unifiDoorIds": door.get("unifiDoorIds") or [],
                "openStart": open_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "openEnd": open_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "sourceEventIds": [f"manual-access:{entry_id}"],
                "sourceEventNames": [label],
                "sourceRooms": ["Manual Access"],
            })
    return windows


def validate_manual_access_window(
    *,
    start_at: str,
    end_at: str,
    door_keys: list[str],
) -> str | None:
    if not door_keys:
        return "Select at least one door"
    start_dt = parse_iso(start_at)
    end_dt = parse_iso(end_at)
    if start_dt is None or end_dt is None:
        return "Start and end times are required"
    if end_dt <= start_dt:
        return "End time must be after start time"
    if (end_dt - start_dt).total_seconds() > 24 * 3600:
        return "Manual access windows must be 24 hours or less"
    return None


def _prune_windows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc)
    kept: list[dict[str, Any]] = []
    for row in items:
        end_dt = parse_iso(row.get("endAt"))
        if end_dt is None or end_dt > cutoff:
            kept.append(row)
    kept.sort(key=lambda row: parse_iso(row.get("startAt")) or datetime.max.replace(tzinfo=timezone.utc))
    return kept
