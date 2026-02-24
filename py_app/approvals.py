"""After-hours approval gate.

Safe hours are loaded from config/safe-hours.json (editable via the Settings page).
Defaults if the file does not exist:
  Monday – Thursday, Saturday, Sunday: 05:00 – 23:00 (11 PM)
  Friday:                              05:00 – 23:30 (11:30 PM)

Any event whose effective door-open window (startAt − lead, endAt + lag) falls
outside those hours is held from applying to UniFi until a human approves it.

Auto-approval: if the event's name appears in approved-event-names.json (added
when someone approves via the dashboard), all future occurrences of that event
name pass through without needing individual approval.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from py_app.utils import parse_iso

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_SAFE_HOURS_DEFAULTS: dict[str, Any] = {
    "safeStartMonday":    "05:00",
    "safeStartTuesday":   "05:00",
    "safeStartWednesday": "05:00",
    "safeStartThursday":  "05:00",
    "safeStartFriday":    "05:00",
    "safeStartSaturday":  "05:00",
    "safeStartSunday":    "05:00",
    "safeEndMonday":    "23:00",
    "safeEndTuesday":   "23:00",
    "safeEndWednesday": "23:00",
    "safeEndThursday":  "23:00",
    "safeEndFriday":    "23:30",
    "safeEndSaturday":  "23:00",
    "safeEndSunday":    "23:00",
}


# ── Safe-hours config file ──────────────────────────────────────────────────

def load_safe_hours(file_path: str) -> dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return dict(_SAFE_HOURS_DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        result = dict(_SAFE_HOURS_DEFAULTS)
        # Backward compat: propagate old single-field values to per-day keys
        # so existing configs aren't silently reset to hardcoded defaults.
        old_start = data.get("safeStartTime")
        if old_start:
            for day in _DAY_NAMES:
                result[f"safeStart{day}"] = old_start
        old_end_default = data.get("safeEndDefault")
        if old_end_default:
            for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Saturday", "Sunday"]:
                result[f"safeEnd{day}"] = old_end_default
        # Apply all values from the file (per-day keys override the backward-compat fill).
        for k, v in data.items():
            if v:
                result[k] = v
        return result
    except Exception:
        return dict(_SAFE_HOURS_DEFAULTS)


def save_safe_hours(file_path: str, data: dict[str, Any]) -> None:
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _parse_hhmm(t: str) -> int:
    """Parse 'HH:MM' string → total minutes since midnight."""
    try:
        h, m = str(t).strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def _minutes(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _fmt_hhmm(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    period = "PM" if h >= 12 else "AM"
    display = h - 12 if h > 12 else (12 if h == 0 else h)
    return f"{display}:{m:02d} {period}"


# ── Outside-safe-hours check ────────────────────────────────────────────────

def is_outside_safe_hours(
    start_utc: datetime,
    end_utc: datetime,
    local_tz: ZoneInfo,
    lead_minutes: int,
    lag_minutes: int,
    safe_hours: dict[str, Any],
) -> tuple[bool, str]:
    """Return (True, reason) if effective window falls outside safe hours, else (False, '')."""
    eff_start = (start_utc - timedelta(minutes=lead_minutes)).astimezone(local_tz)
    eff_end = (end_utc + timedelta(minutes=lag_minutes)).astimezone(local_tz)

    day_name = _DAY_NAMES[eff_start.weekday()]
    safe_start = _parse_hhmm(
        safe_hours.get(f"safeStart{day_name}") or safe_hours.get("safeStartTime") or "05:00"
    )
    cutoff = _parse_hhmm(
        safe_hours.get(f"safeEnd{day_name}") or safe_hours.get("safeEndDefault") or "23:00"
    )

    # Before safe start?
    if _minutes(eff_start) < safe_start:
        return True, (
            f"Doors would open at {_fmt_hhmm(_minutes(eff_start))}"
            f" (before {_fmt_hhmm(safe_start)} safe-hours start on {day_name})"
        )

    # Spans midnight?
    if eff_end.date() > eff_start.date():
        return True, "Event extends past midnight"

    # After cutoff for that day?
    if _minutes(eff_end) > cutoff:
        return True, (
            f"Doors would remain open until {_fmt_hhmm(_minutes(eff_end))}"
            f" (past {_fmt_hhmm(cutoff)} cutoff on {day_name})"
        )

    return False, ""


# ── Pending approvals file ──────────────────────────────────────────────────

def load_pending_approvals(file_path: str) -> dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return {"pending": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"pending": []}


def _save_pending_approvals(file_path: str, data: dict[str, Any]) -> None:
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _prune_pending(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove entries whose event has already ended (plus 2h grace)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    return [
        i for i in items
        if (lambda e: e is None or e >= cutoff)(parse_iso(i.get("endAt")))
    ]


# ── Approved event names file ───────────────────────────────────────────────

def load_approved_event_names(file_path: str) -> set[str]:
    path = Path(file_path)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(e.get("name") or "").lower() for e in (data.get("names") or []) if e.get("name")}
    except Exception:
        return set()


def save_approved_event_name(file_path: str, name: str) -> None:
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"names": []}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    names: list[dict[str, Any]] = data.get("names") or []
    if not any(str(e.get("name") or "").lower() == name.lower() for e in names):
        names.append({"name": name, "approvedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    data["names"] = names
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ── Main gate function ──────────────────────────────────────────────────────

def filter_and_flag_events(
    events: list[dict[str, Any]],
    local_tz: ZoneInfo,
    lead_minutes: int,
    lag_minutes: int,
    pending_file: str,
    approved_names_file: str,
    safe_hours_file: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate events into (allowed, newly_flagged)."""
    safe_hours = load_safe_hours(safe_hours_file)
    approved_names = load_approved_event_names(approved_names_file)
    pending_data = load_pending_approvals(pending_file)
    pending_items: list[dict[str, Any]] = pending_data.get("pending") or []
    pending_by_id: dict[str, dict[str, Any]] = {str(p.get("id") or ""): p for p in pending_items}

    allowed: list[dict[str, Any]] = []
    newly_flagged: list[dict[str, Any]] = []
    changed = False

    def _clear_pending(event_id: str) -> None:
        """Remove a pending entry by event_id (safe hours changed, event no longer outside)."""
        nonlocal pending_items, changed
        if event_id in pending_by_id:
            pending_items = [p for p in pending_items if str(p.get("id") or "") != event_id]
            del pending_by_id[event_id]
            changed = True

    for e in events:
        start_utc = parse_iso(e.get("startAt"))
        end_utc = parse_iso(e.get("endAt"))
        if not start_utc or not end_utc:
            allowed.append(e)
            continue

        event_id = str(e.get("id") or "")

        outside, reason = is_outside_safe_hours(
            start_utc, end_utc, local_tz, lead_minutes, lag_minutes, safe_hours
        )
        if not outside:
            # Clear any stale pending entry — event is now within safe hours.
            _clear_pending(event_id)
            allowed.append(e)
            continue

        # Auto-approval by event name.
        name_lower = str(e.get("name") or "").lower()
        if name_lower in approved_names:
            _clear_pending(event_id)
            allowed.append(e)
            continue

        existing = pending_by_id.get(event_id)
        if existing:
            status = str(existing.get("status") or "pending")
            if status == "approved":
                allowed.append(e)
            # pending or denied → hold
            continue

        # New flagged event — add to pending.
        entry: dict[str, Any] = {
            "id": event_id,
            "name": str(e.get("name") or ""),
            "startAt": str(e.get("startAt") or ""),
            "endAt": str(e.get("endAt") or ""),
            "reason": reason,
            "flaggedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "pending",
        }
        pending_items.append(entry)
        pending_by_id[event_id] = entry
        newly_flagged.append(entry)
        changed = True

    pending_items = _prune_pending(pending_items)
    if changed or len(pending_items) != len(pending_data.get("pending") or []):
        pending_data["pending"] = pending_items
        _save_pending_approvals(pending_file, pending_data)

    return allowed, newly_flagged


# ── Approve / Deny actions ──────────────────────────────────────────────────

def approve_pending(pending_file: str, approved_names_file: str, event_id: str) -> str | None:
    data = load_pending_approvals(pending_file)
    items: list[dict[str, Any]] = data.get("pending") or []
    name = None
    for item in items:
        if str(item.get("id") or "") == event_id:
            item["status"] = "approved"
            item["decidedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            name = str(item.get("name") or "")
            break
    data["pending"] = items
    _save_pending_approvals(pending_file, data)
    if name:
        save_approved_event_name(approved_names_file, name)
    return name


def deny_pending(pending_file: str, event_id: str) -> str | None:
    data = load_pending_approvals(pending_file)
    items: list[dict[str, Any]] = data.get("pending") or []
    name = None
    for item in items:
        if str(item.get("id") or "") == event_id:
            item["status"] = "denied"
            item["decidedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            name = str(item.get("name") or "")
            break
    data["pending"] = items
    _save_pending_approvals(pending_file, data)
    return name
