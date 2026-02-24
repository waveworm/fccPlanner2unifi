from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from py_app.approvals import approve_pending, deny_pending, filter_and_flag_events, load_pending_approvals
from py_app.event_overrides import load_cancelled_events, load_event_overrides, update_event_memory
from py_app.mapping import build_desired_schedule, load_room_door_mapping
from py_app.office_hours import build_office_hours_windows, load_office_hours, merge_office_hours_into_desired
from py_app.settings import Settings
from py_app.utils import parse_iso
from py_app.vendors.pco import PcoClient
from py_app.vendors.telegram import TelegramClient
from py_app.vendors.unifi_access import UnifiAccessClient


@dataclass
class SyncStatus:
    last_sync_at: str | None = None
    last_sync_result: str | None = None
    pco_status: str = "unknown"
    unifi_status: str = "unknown"
    recent_errors: list[str] = field(default_factory=list)


class SyncService:
    def __init__(self, settings: Settings, logger):
        self.settings = settings
        self.logger = logger
        self.pco = PcoClient(settings)
        self.unifi = UnifiAccessClient(settings)
        self.telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_ids)
        self.status = SyncStatus()

        # Apply mode: seed from env var, then override with persisted state if present.
        self._apply_to_unifi: bool = bool(getattr(settings, "apply_to_unifi", False))
        self._load_apply_state()

    def snapshot(self) -> dict:
        return {
            "lastSyncAt": self.status.last_sync_at,
            "lastSyncResult": self.status.last_sync_result,
            "pcoStatus": self.status.pco_status,
            "unifiStatus": self.status.unifi_status,
            "recentErrors": list(self.status.recent_errors),
            "applyToUnifi": self._apply_to_unifi,
            "pcoStats": self.pco.stats_snapshot(),
        }

    def get_apply_to_unifi(self) -> bool:
        return self._apply_to_unifi

    def set_apply_to_unifi(self, value: bool) -> None:
        self._apply_to_unifi = bool(value)
        self._save_apply_state()

    def _state_path(self) -> Path:
        return Path(self.settings.room_door_mapping_file).resolve().parent / "sync-state.json"

    def _load_apply_state(self) -> None:
        try:
            path = self._state_path()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                self._apply_to_unifi = bool(data.get("applyToUnifi", self._apply_to_unifi))
        except Exception:
            pass

    def _save_apply_state(self) -> None:
        try:
            path = self._state_path()
            path.write_text(json.dumps({"applyToUnifi": self._apply_to_unifi}, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

    def _push_error(self, msg: str) -> None:
        self.status.recent_errors = [msg, *self.status.recent_errors][:20]

    async def run_once(self) -> None:
        started_at = datetime.now(timezone.utc).isoformat()
        self.status.last_sync_at = started_at

        try:
            mapping = load_room_door_mapping(self.settings.room_door_mapping_file)

            now = datetime.now(timezone.utc)
            from_dt = now - timedelta(hours=int(self.settings.sync_lookbehind_hours))
            to_dt = now + timedelta(hours=int(self.settings.sync_lookahead_hours))

            # Fix 4: run connectivity checks concurrently instead of sequentially.
            pco_ok, unifi_ok = await asyncio.gather(
                self.pco.check_connectivity(),
                self.unifi.check_connectivity(),
            )
            self.status.pco_status = "ok" if pco_ok else "error"
            self.status.unifi_status = "ok" if unifi_ok else "error"

            events = await self.pco.get_events(from_iso=from_dt.isoformat(), to_iso=to_dt.isoformat())
            # Fix 5: apply the same location filter used by get_preview so behavior is consistent.
            events = self._filter_events_in_window(events, from_dt, to_dt)
            events = self._apply_mapping_exclusions(events, mapping)
            cancelled_data = load_cancelled_events(self.settings.cancelled_events_file)
            events = self._filter_cancelled_events(events, cancelled_data)

            # Always update event memory (regardless of apply mode).
            local_tz = ZoneInfo(self.settings.display_timezone)

            # After-hours approval gate.
            mapping_defaults = (mapping.get("defaults") or {})
            lead_min = int(mapping_defaults.get("unlockLeadMinutes") or 15)
            lag_min = int(mapping_defaults.get("unlockLagMinutes") or 15)
            events, newly_flagged = filter_and_flag_events(
                events, local_tz, lead_min, lag_min,
                self.settings.pending_approvals_file,
                self.settings.approved_event_names_file,
                self.settings.safe_hours_file,
            )
            if newly_flagged:
                await self.telegram.notify_flagged_events(newly_flagged)
            update_event_memory(self.settings.event_memory_file, events, local_tz)

            overrides_cfg = load_event_overrides(self.settings.event_overrides_file)
            desired = build_desired_schedule(
                events=events,
                mapping=mapping,
                now_iso=now.isoformat(),
                overrides=overrides_cfg.get("overrides") or {},
                local_tz=local_tz,
            )

            oh_config = load_office_hours(self.settings.office_hours_file)
            oh_windows = build_office_hours_windows(
                oh_config, from_dt, to_dt,
                ZoneInfo(self.settings.display_timezone),
                mapping.get("doors") or {},
            )
            desired = merge_office_hours_into_desired(desired, oh_windows)

            if self._apply_to_unifi:
                await self.unifi.apply_desired_schedule(desired)
                mode = "apply"
            else:
                # Dry run: compute only.
                mode = "dry-run"

            self.status.last_sync_result = (
                f"ok: mode={mode} events={len(events)} scheduleItems={len(desired.get('items', []))}"
            )
            self.logger.info("Sync complete")
        except Exception as e:
            msg = str(e)
            self.status.last_sync_result = f"error: {msg}"
            self._push_error(f"{datetime.now(timezone.utc).isoformat()} {msg}")
            self.logger.exception("Sync failed")
            raise

    def _apply_mapping_exclusions(self, events: list[dict[str, Any]], mapping: dict[str, Any]) -> list[dict[str, Any]]:
        """Drop events whose room field matches any pattern in rules.excludeEventsByRoomContains.

        Only checks e['room'] — the resource-booking room name, or the raw location string
        if the event has no resource bookings (e.g. all-day away events).
        locationRaw is NOT checked because it contains the campus address for every event.
        """
        patterns = [str(p).lower() for p in ((mapping.get("rules") or {}).get("excludeEventsByRoomContains") or [])]
        if not patterns:
            return events
        out = []
        for e in events:
            room = str(e.get("room") or "").lower()
            if any(pat in room for pat in patterns):
                continue
            out.append(e)
        return out

    def get_pending_approvals(self) -> list[dict]:
        data = load_pending_approvals(self.settings.pending_approvals_file)
        items = data.get("pending") or []
        return [i for i in items if str(i.get("status") or "pending") == "pending"]

    def approve_event(self, event_id: str) -> str | None:
        return approve_pending(
            self.settings.pending_approvals_file,
            self.settings.approved_event_names_file,
            event_id,
        )

    def deny_event(self, event_id: str) -> str | None:
        return deny_pending(self.settings.pending_approvals_file, event_id)

    def _filter_cancelled_events(self, events: list[dict[str, Any]], cancelled_data: dict[str, Any]) -> list[dict[str, Any]]:
        cancelled_ids = {str(i.get("id")) for i in (cancelled_data.get("instances") or []) if i.get("id")}
        if not cancelled_ids:
            return events
        return [e for e in events if str(e.get("id") or "") not in cancelled_ids]

    def _filter_events_in_window(self, events: list[dict[str, Any]], start_dt: datetime, end_dt: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        must_contain = (getattr(self.settings, "pco_location_must_contain", "") or "").strip().lower()
        for e in events:
            s = parse_iso(e.get("startAt"))
            if s is None:
                continue
            if s > end_dt:
                continue  # starts after the window
            if s < start_dt:
                # Started before the window — keep only if still in progress
                end = parse_iso(e.get("endAt"))
                if end is None or end <= start_dt:
                    continue

            if must_contain:
                hay = str(e.get("locationRaw") or e.get("room") or "").lower()
                if must_contain not in hay:
                    continue
            out.append(e)
        out.sort(key=lambda ev: parse_iso(ev.get("startAt")) or datetime.max.replace(tzinfo=timezone.utc))
        return out

    async def get_preview(self, *, start_dt: datetime, end_dt: datetime, limit: int = 200) -> dict:
        mapping = load_room_door_mapping(self.settings.room_door_mapping_file)
        now = datetime.now(timezone.utc)

        events = await self.pco.get_events(from_iso=start_dt.isoformat(), to_iso=end_dt.isoformat(), max_items=int(limit))
        events = self._filter_events_in_window(events, start_dt, end_dt)
        events = self._apply_mapping_exclusions(events, mapping)
        cancelled_data = load_cancelled_events(self.settings.cancelled_events_file)
        events = self._filter_cancelled_events(events, cancelled_data)

        local_tz = ZoneInfo(self.settings.display_timezone)
        mapping_defaults = (mapping.get("defaults") or {})
        lead_min = int(mapping_defaults.get("unlockLeadMinutes") or 15)
        lag_min = int(mapping_defaults.get("unlockLagMinutes") or 15)
        events, _ = filter_and_flag_events(
            events, local_tz, lead_min, lag_min,
            self.settings.pending_approvals_file,
            self.settings.approved_event_names_file,
            self.settings.safe_hours_file,
        )
        overrides_cfg = load_event_overrides(self.settings.event_overrides_file)
        desired = build_desired_schedule(
            events=events,
            mapping=mapping,
            now_iso=now.isoformat(),
            overrides=overrides_cfg.get("overrides") or {},
            local_tz=local_tz,
        )

        oh_config = load_office_hours(self.settings.office_hours_file)
        oh_windows = build_office_hours_windows(
            oh_config, start_dt, end_dt,
            ZoneInfo(self.settings.display_timezone),
            mapping.get("doors") or {},
        )
        desired = merge_office_hours_into_desired(desired, oh_windows)

        rooms: dict[str, int] = {}
        for e in events:
            room = e.get("room") or "(none)"
            rooms[room] = rooms.get(room, 0) + 1

        return {
            "now": now.isoformat(),
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "limit": int(limit),
            "rooms": rooms,
            "events": events,
            "schedule": desired,
        }

    async def get_upcoming_preview(self, *, limit: int = 50) -> dict:
        now = datetime.now(timezone.utc)
        # Use a fixed 24h lookback so PCO returns any event still in progress,
        # regardless of SYNC_LOOKBEHIND_HOURS (which may be short for sync purposes).
        fetch_from = now - timedelta(hours=24)
        end_dt = now + timedelta(hours=int(self.settings.sync_lookahead_hours))
        result = await self.get_preview(start_dt=fetch_from, end_dt=end_dt, limit=limit)

        # Keep only events still in progress or upcoming — drop anything already finished.
        result["events"] = [
            e for e in result["events"]
            if (lambda end: end is None or end >= now)(parse_iso(e.get("endAt")))
        ]

        # Recompute rooms from the filtered event list.
        rooms: dict[str, int] = {}
        for e in result["events"]:
            room = e.get("room") or "(none)"
            rooms[room] = rooms.get(room, 0) + 1
        result["rooms"] = rooms

        return result
