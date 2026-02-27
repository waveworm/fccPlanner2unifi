from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from py_app.utils import parse_iso
from py_app.event_overrides import find_door_override


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_room_door_mapping(mapping_file: str) -> dict[str, Any]:
    path = Path(mapping_file).resolve()
    raw = path.read_text(encoding="utf-8")
    return json.loads(raw)


def _is_door_excluded_for_event(*, evt: dict[str, Any], door_key: str, mapping: dict[str, Any]) -> bool:
    rules = mapping.get("rules") or {}
    rows = rules.get("excludeDoorKeysByEventName") or []
    if not isinstance(rows, list):
        return False

    event_name = str(evt.get("name") or "").strip().lower()
    if not event_name:
        return False

    for row in rows:
        if not isinstance(row, dict):
            continue
        needle = str(row.get("eventNameContains") or "").strip().lower()
        if not needle or needle not in event_name:
            continue

        keys = row.get("doorKeys") or []
        if not isinstance(keys, list):
            continue
        for raw in keys:
            if str(raw).strip() == door_key:
                return True

    return False


def _merge_windows(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not windows:
        return []

    sorted_windows = sorted(windows, key=lambda w: parse_iso(w.get("openStart")) or datetime.max.replace(tzinfo=timezone.utc))
    merged: list[dict[str, Any]] = []

    for w in sorted_windows:
        start = parse_iso(w.get("openStart"))
        end = parse_iso(w.get("openEnd"))
        if not start or not end:
            continue

        if not merged:
            merged.append({
                "doorKey": w.get("doorKey"),
                "doorLabel": w.get("doorLabel"),
                "unifiDoorIds": w.get("unifiDoorIds") or [],
                "openStart": _to_iso(start),
                "openEnd": _to_iso(end),
                "sourceEventIds": list(w.get("sourceEventIds") or []),
                "sourceEventNames": list(w.get("sourceEventNames") or []),
                "sourceRooms": list(w.get("sourceRooms") or []),
            })
            continue

        last = merged[-1]
        last_end = parse_iso(last.get("openEnd"))
        if not last_end:
            continue

        # If windows overlap or touch, merge into one continuous open period.
        if start <= last_end:
            if end > last_end:
                last["openEnd"] = _to_iso(end)
            last["sourceEventIds"] = list(dict.fromkeys((last.get("sourceEventIds") or []) + (w.get("sourceEventIds") or [])))
            last["sourceEventNames"] = list(dict.fromkeys((last.get("sourceEventNames") or []) + (w.get("sourceEventNames") or [])))
            last["sourceRooms"] = list(dict.fromkeys((last.get("sourceRooms") or []) + (w.get("sourceRooms") or [])))
        else:
            merged.append({
                "doorKey": w.get("doorKey"),
                "doorLabel": w.get("doorLabel"),
                "unifiDoorIds": w.get("unifiDoorIds") or [],
                "openStart": _to_iso(start),
                "openEnd": _to_iso(end),
                "sourceEventIds": list(w.get("sourceEventIds") or []),
                "sourceEventNames": list(w.get("sourceEventNames") or []),
                "sourceRooms": list(w.get("sourceRooms") or []),
            })

    return merged


def build_desired_schedule(
    *,
    events: list[dict[str, Any]],
    mapping: dict[str, Any],
    now_iso: str,
    overrides: dict[str, Any] | None = None,
    local_tz: ZoneInfo | None = None,
) -> dict[str, Any]:
    defaults = mapping.get("defaults") or {"unlockLeadMinutes": 15, "unlockLagMinutes": 15}
    items: list[dict[str, Any]] = []
    windows_by_door: dict[str, list[dict[str, Any]]] = {}

    rooms_map = mapping.get("rooms") or {}
    doors_map = mapping.get("doors") or {}

    for evt in events:
        room_candidates = []
        evt_rooms = evt.get("rooms")
        if isinstance(evt_rooms, list):
            for r in evt_rooms:
                if r:
                    room_candidates.append(str(r))

        if not room_candidates and evt.get("room"):
            room_candidates.append(str(evt.get("room")))

        # Preserve order but avoid duplicates.
        seen: set[str] = set()
        normalized_rooms: list[str] = []
        for r in room_candidates:
            if r in seen:
                continue
            seen.add(r)
            normalized_rooms.append(r)

        for room_name in normalized_rooms:
            door_keys = rooms_map.get(room_name)
            if not door_keys:
                continue

            for door_key in door_keys:
                if _is_door_excluded_for_event(evt=evt, door_key=door_key, mapping=mapping):
                    continue
                door = doors_map.get(door_key)
                if not door:
                    continue
                items.append(
                    {
                        "sourceEventId": str(evt.get("id", "")),
                        "room": room_name,
                        "doorKey": door_key,
                        "doorLabel": door.get("label", door_key),
                        "unifiDoorIds": door.get("unifiDoorIds") or [],
                        "startAt": evt.get("startAt"),
                        "endAt": evt.get("endAt"),
                        "unlockLeadMinutes": int(defaults.get("unlockLeadMinutes", 15)),
                        "unlockLagMinutes": int(defaults.get("unlockLagMinutes", 15)),
                    }
                )

                start_dt = parse_iso(evt.get("startAt"))
                end_dt = parse_iso(evt.get("endAt"))
                if start_dt and end_dt:
                    door_override = find_door_override(
                        str(evt.get("name") or ""), door_key, overrides or {}
                    )
                    # Build list of (open_start, open_end) UTC pairs for this door+event.
                    pairs: list[tuple[datetime, datetime]] = []
                    if door_override is not None and local_tz:
                        # Override found. windows=[] means suppress this door for this event.
                        event_date = start_dt.astimezone(local_tz).date()
                        for win_cfg in (door_override.get("windows") or []):
                            oh, om = map(int, win_cfg["openTime"].split(":"))
                            ch, cm = map(int, win_cfg["closeTime"].split(":"))
                            pairs.append((
                                datetime(event_date.year, event_date.month, event_date.day,
                                         oh, om, tzinfo=local_tz).astimezone(timezone.utc),
                                datetime(event_date.year, event_date.month, event_date.day,
                                         ch, cm, tzinfo=local_tz).astimezone(timezone.utc),
                            ))
                    else:
                        # No override (or no local_tz) — use default lead/lag.
                        lead = int(defaults.get("unlockLeadMinutes", 15))
                        lag = int(defaults.get("unlockLagMinutes", 15))
                        pairs.append((start_dt - timedelta(minutes=lead), end_dt + timedelta(minutes=lag)))

                    door_windows = windows_by_door.setdefault(door_key, [])
                    for open_start, open_end in pairs:
                        door_windows.append(
                            {
                                "doorKey": door_key,
                                "doorLabel": door.get("label", door_key),
                                "unifiDoorIds": door.get("unifiDoorIds") or [],
                                "openStart": _to_iso(open_start),
                                "openEnd": _to_iso(open_end),
                                "sourceEventIds": [str(evt.get("id", ""))],
                                "sourceEventNames": [str(evt.get("name", ""))],
                                "sourceRooms": [room_name],
                            }
                        )

    merged_door_windows: list[dict[str, Any]] = []
    for _door_key, windows in windows_by_door.items():
        merged_door_windows.extend(_merge_windows(windows))

    return {"generatedAt": now_iso, "items": items, "doorWindows": merged_door_windows}
