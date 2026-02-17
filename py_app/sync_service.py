from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from py_app.mapping import build_desired_schedule, load_room_door_mapping
from py_app.settings import Settings
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

        # In-memory toggle. Default comes from env.
        self._apply_to_unifi: bool = bool(getattr(settings, "apply_to_unifi", False))

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

            pco_ok, unifi_ok = await self.pco.check_connectivity(), await self.unifi.check_connectivity()
            self.status.pco_status = "ok" if pco_ok else "error"
            self.status.unifi_status = "ok" if unifi_ok else "error"

            events = await self.pco.get_events(from_iso=from_dt.isoformat(), to_iso=to_dt.isoformat())
            desired = build_desired_schedule(events=events, mapping=mapping, now_iso=now.isoformat())

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

    def _parse_iso(self, value: Any) -> datetime | None:
        if not value or not isinstance(value, str):
            return None
        try:
            # PCO uses Z; datetime.fromisoformat needs +00:00.
            v = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(v)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _filter_events_in_window(self, events: list[dict[str, Any]], start_dt: datetime, end_dt: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        must_contain = (getattr(self.settings, "pco_location_must_contain", "") or "").strip().lower()
        for e in events:
            s = self._parse_iso(e.get("startAt"))
            if s is None:
                continue
            if s < start_dt or s > end_dt:
                continue

            if must_contain:
                hay = str(e.get("locationRaw") or e.get("room") or "").lower()
                if must_contain not in hay:
                    continue
            out.append(e)
        out.sort(key=lambda ev: self._parse_iso(ev.get("startAt")) or datetime.max.replace(tzinfo=timezone.utc))
        return out

    async def get_preview(self, *, start_dt: datetime, end_dt: datetime, limit: int = 200) -> dict:
        mapping = load_room_door_mapping(self.settings.room_door_mapping_file)
        now = datetime.now(timezone.utc)

        events = await self.pco.get_events(from_iso=start_dt.isoformat(), to_iso=end_dt.isoformat(), max_items=int(limit))
        events = self._filter_events_in_window(events, start_dt, end_dt)

        desired = build_desired_schedule(events=events, mapping=mapping, now_iso=now.isoformat())

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
