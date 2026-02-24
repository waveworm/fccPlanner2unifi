from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from py_app.utils import parse_iso


# ── Event Memory ──────────────────────────────────────────────────────────────

def load_event_memory(file_path: str) -> dict[str, Any]:
    """Load the event memory from disk. Returns empty memory if file missing or invalid."""
    path = Path(file_path)
    if not path.exists():
        return {"events": [], "updatedAt": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"events": [], "updatedAt": None}


def update_event_memory(file_path: str, events: list[dict[str, Any]], local_tz: ZoneInfo) -> None:
    """Update the rolling event memory with events from the current sync window.

    - lastSeenAt: most recent past startAt we've observed for this event name
    - nextAt: nearest future startAt from the most recent sync (cleared when it passes)
    - Prunes entries older than 60 days with no upcoming occurrence
    """
    memory = load_event_memory(file_path)
    now = datetime.now(timezone.utc)

    # Index existing entries by lowercase name.
    entries: dict[str, dict[str, Any]] = {}
    for e in memory.get("events") or []:
        key = (e.get("name") or "").strip().lower()
        if key:
            entries[key] = e

    # Step 1: Expire nextAt values that are now in the past.
    for entry in entries.values():
        next_dt = parse_iso(entry.get("nextAt"))
        if next_dt and next_dt < now:
            entry["nextAt"] = None

    # Step 2: Process incoming events.
    for evt in events:
        name = (evt.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        start_dt = parse_iso(evt.get("startAt"))
        if not start_dt:
            continue

        if key not in entries:
            entries[key] = {
                "name": name,
                "building": _extract_building(evt),
                "rooms": [],
                "lastSeenAt": None,
                "nextAt": None,
            }

        entry = entries[key]

        end_dt = parse_iso(evt.get("endAt"))

        # Update lastSeenAt/lastEndAt: most recent PAST startAt ever seen.
        if start_dt < now:
            last_seen = parse_iso(entry.get("lastSeenAt"))
            if last_seen is None or start_dt > last_seen:
                entry["lastSeenAt"] = _to_z(start_dt)
                entry["lastEndAt"] = _to_z(end_dt) if end_dt else None
            elif last_seen is not None and start_dt == last_seen and "lastEndAt" not in entry:
                entry["lastEndAt"] = _to_z(end_dt) if end_dt else None

        # Update nextAt/nextEndAt: nearest FUTURE occurrence.
        if start_dt >= now:
            next_dt = parse_iso(entry.get("nextAt"))
            if next_dt is None or start_dt < next_dt:
                entry["nextAt"] = _to_z(start_dt)
                entry["nextEndAt"] = _to_z(end_dt) if end_dt else None
            elif next_dt is not None and start_dt == next_dt and "nextEndAt" not in entry:
                entry["nextEndAt"] = _to_z(end_dt) if end_dt else None

        # Union rooms.
        evt_rooms: list[str] = []
        if isinstance(evt.get("rooms"), list):
            evt_rooms = [str(r) for r in evt["rooms"] if r]
        elif evt.get("room"):
            evt_rooms = [str(evt["room"])]
        existing_rooms: list[str] = list(entry.get("rooms") or [])
        for r in evt_rooms:
            if r not in existing_rooms:
                existing_rooms.append(r)
        entry["rooms"] = existing_rooms

        # Update building if missing.
        if not entry.get("building"):
            entry["building"] = _extract_building(evt)

    # Step 3: Prune stale entries.
    cutoff = now - timedelta(days=60)
    pruned: list[dict[str, Any]] = []
    for entry in entries.values():
        last_seen = parse_iso(entry.get("lastSeenAt"))
        next_dt = parse_iso(entry.get("nextAt"))
        # Keep if: has a future occurrence OR was seen recently
        if next_dt is not None:
            pruned.append(entry)
        elif last_seen is not None and last_seen >= cutoff:
            pruned.append(entry)
        # else: prune (old with no future occurrence, or ghost entry)

    # Sort: upcoming events first (soonest), then past by most recent.
    def _sort_key(e: dict[str, Any]) -> tuple:
        next_dt = parse_iso(e.get("nextAt"))
        last_seen = parse_iso(e.get("lastSeenAt"))
        has_next = next_dt is not None
        next_ts = next_dt.timestamp() if next_dt else float("inf")
        last_ts = -(last_seen.timestamp() if last_seen else 0)
        return (not has_next, next_ts, last_ts)

    pruned.sort(key=_sort_key)

    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"events": pruned, "updatedAt": _to_z(now)}, indent=2) + "\n",
        encoding="utf-8",
    )


def _to_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_building(evt: dict[str, Any]) -> str:
    """Extract building/campus name from an event dict."""
    # Prefer the pre-computed 'building' field from PCO client.
    building = str(evt.get("building") or "").strip()
    if building:
        return building
    # Fall back to locationRaw, splitting on " - " to get the campus name.
    loc = str(evt.get("locationRaw") or "").strip()
    if loc:
        return loc.split(" - ")[0].strip() if " - " in loc else loc
    return ""


# ── Event Overrides ───────────────────────────────────────────────────────────

def load_event_overrides(file_path: str) -> dict[str, Any]:
    """Load event overrides from disk. Returns empty overrides if file missing or invalid."""
    path = Path(file_path)
    if not path.exists():
        return {"overrides": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"overrides": {}}


def save_event_overrides(file_path: str, data: dict[str, Any]) -> None:
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def validate_event_overrides(data: Any) -> str | None:
    """Return an error message if the payload is invalid, else None."""
    if not isinstance(data, dict):
        return "Payload must be a JSON object"
    overrides = data.get("overrides")
    if not isinstance(overrides, dict):
        return "'overrides' must be an object"
    for event_name, event_cfg in overrides.items():
        if not isinstance(event_cfg, dict):
            return f"Override for '{event_name}' must be an object"
        door_overrides = event_cfg.get("doorOverrides")
        if not isinstance(door_overrides, dict):
            return f"'doorOverrides' for '{event_name}' must be an object"
        for door_key, door_cfg in door_overrides.items():
            if not isinstance(door_cfg, dict):
                return f"Door override for '{event_name}.{door_key}' must be an object"
            windows = door_cfg.get("windows")
            if not isinstance(windows, list):
                return f"'windows' for '{event_name}.{door_key}' must be an array (use [] to suppress this door)"
            # Empty array is valid — means suppress this door for this event.
            for i, win in enumerate(windows):
                if not isinstance(win, dict):
                    return f"Window {i + 1} for '{event_name}.{door_key}' must be an object"
                for field_name in ("openTime", "closeTime"):
                    val = win.get(field_name)
                    if not isinstance(val, str):
                        return f"'{field_name}' in window {i + 1} for '{event_name}.{door_key}' must be a string"
                    if not re.match(r"^\d{1,2}:\d{2}$", val):
                        return f"'{field_name}' in window {i + 1} for '{event_name}.{door_key}' must be HH:MM format"
    return None


# ── Cancelled Event Instances ──────────────────────────────────────────────────

def load_cancelled_events(file_path: str) -> dict[str, Any]:
    """Load the cancelled-events list from disk."""
    path = Path(file_path)
    if not path.exists():
        return {"instances": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"instances": []}


def add_cancelled_event(
    file_path: str, event_id: str, name: str, start_at: str, end_at: str
) -> None:
    """Add an event instance to the cancelled list and prune entries older than 24h past end."""
    data = load_cancelled_events(file_path)
    instances: list[dict[str, Any]] = data.get("instances") or []
    # Idempotent: remove any existing entry for this ID first.
    instances = [i for i in instances if i.get("id") != event_id]
    instances.append({
        "id": event_id,
        "name": name,
        "startAt": start_at,
        "endAt": end_at,
        "cancelledAt": _to_z(datetime.now(timezone.utc)),
    })
    instances = _prune_cancelled(instances)
    data["instances"] = instances
    _save_cancelled_events(file_path, data)


def remove_cancelled_event(file_path: str, event_id: str) -> None:
    """Remove a single event instance from the cancelled list."""
    data = load_cancelled_events(file_path)
    instances = [i for i in (data.get("instances") or []) if i.get("id") != event_id]
    data["instances"] = instances
    _save_cancelled_events(file_path, data)


def _save_cancelled_events(file_path: str, data: dict[str, Any]) -> None:
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _prune_cancelled(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove entries whose end time (or start time) is more than 24h in the past."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    kept = []
    for inst in instances:
        ref = parse_iso(inst.get("endAt")) or parse_iso(inst.get("startAt"))
        if ref is None or ref >= cutoff:
            kept.append(inst)
    return kept


def find_door_override(
    event_name: str, door_key: str, overrides: dict[str, Any]
) -> dict[str, Any] | None:
    """Case-insensitive lookup of event override for a specific door.

    Returns {'windows': [{'openTime': 'HH:MM', 'closeTime': 'HH:MM'}, ...]} or None if no override.
    """
    if not event_name or not overrides:
        return None
    name_lower = event_name.strip().lower()
    for key, event_cfg in overrides.items():
        if key.strip().lower() == name_lower:
            door_overrides = (event_cfg or {}).get("doorOverrides") or {}
            result = door_overrides.get(door_key)
            return result if isinstance(result, dict) else None
    return None
