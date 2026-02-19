from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from py_app.mapping import build_desired_schedule, load_room_door_mapping
from py_app.office_hours import build_office_hours_windows, load_office_hours, merge_office_hours_into_desired
from py_app.settings import Settings
from py_app.utils import parse_iso
from py_app.vendors.pco import PcoClient
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
            desired = build_desired_schedule(events=events, mapping=mapping, now_iso=now.isoformat())

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

    def _filter_events_in_window(self, events: list[dict[str, Any]], start_dt: datetime, end_dt: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        must_contain = (getattr(self.settings, "pco_location_must_contain", "") or "").strip().lower()
        for e in events:
            s = parse_iso(e.get("startAt"))
            if s is None:
                continue
            if s < start_dt or s > end_dt:
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

        desired = build_desired_schedule(events=events, mapping=mapping, now_iso=now.isoformat())

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
        end_dt = now + timedelta(hours=int(self.settings.sync_lookahead_hours))
        return await self.get_preview(start_dt=now, end_dt=end_dt, limit=limit)
