from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from py_app.settings import Settings


class PcoClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._events_cache: dict[tuple[str, str, int], tuple[datetime, list[dict[str, Any]]]] = {}
        self._last_fetch_by_key: dict[tuple[str, str, int], datetime] = {}
        self._room_names_cache: dict[str, tuple[datetime, list[str]]] = {}
        self._cache_lock = asyncio.Lock()
        self._stats: dict[str, Any] = {
            "cacheHitReturns": 0,
            "roomCacheHitReturns": 0,
            "minIntervalCacheReturns": 0,
            "liveWindowFetches": 0,
            "eventInstanceRequests": 0,
            "resourceBookingRequests": 0,
            "pco429FallbackReturns": 0,
            "pco429RetryCount": 0,
            "lastLiveFetchAt": None,
            "lastCacheHitAt": None,
            "last429FallbackAt": None,
        }

    def stats_snapshot(self) -> dict[str, Any]:
        return {
            **self._stats,
            "cacheKeys": len(self._events_cache),
        }

    @staticmethod
    def _normalize_window_key(*, from_iso: str, to_iso: str, max_items: int | None) -> tuple[str, str, int]:
        """Normalize cache keys so repeated calls within the same minute can reuse cached results."""
        try:
            from_dt = datetime.fromisoformat(from_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
            to_dt = datetime.fromisoformat(to_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
            from_key = from_dt.replace(second=0, microsecond=0).isoformat()
            to_key = to_dt.replace(second=0, microsecond=0).isoformat()
        except Exception:
            from_key = from_iso
            to_key = to_iso
        return (from_key, to_key, int(max_items or 0))

    def _event_instances_path(self) -> str:
        cal_id = (self.settings.pco_calendar_id or "").strip()
        if cal_id:
            return f"/calendar/v2/calendars/{cal_id}/event_instances"
        return "/calendar/v2/event_instances"

    @staticmethod
    def _parse_retry_after_seconds(response: httpx.Response, *, default_seconds: float = 1.0) -> float:
        raw = str(response.headers.get("Retry-After") or "").strip()
        if not raw:
            return default_seconds
        try:
            return max(float(raw), 0.0)
        except Exception:
            pass
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = (dt.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
            return max(delta, 0.0)
        except Exception:
            return default_seconds

    async def _get_with_429_retry(
        self,
        client: httpx.AsyncClient,
        *,
        path: str,
        params: dict[str, Any] | None = None,
        attempts: int = 3,
    ) -> httpx.Response:
        last_resp: httpx.Response | None = None
        for idx in range(max(1, attempts)):
            resp = await client.get(path, headers=self._auth_headers(), params=params)
            last_resp = resp
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp
            if idx >= attempts - 1:
                break
            wait_s = self._parse_retry_after_seconds(resp, default_seconds=float(2 ** idx))
            self._stats["pco429RetryCount"] = int(self._stats.get("pco429RetryCount") or 0) + 1
            await asyncio.sleep(min(wait_s, 20.0))
        assert last_resp is not None
        last_resp.raise_for_status()
        return last_resp

    async def _get_cached_events_fallback(self, cache_key: tuple[str, str, int]) -> list[dict[str, Any]] | None:
        """Return cached events for exact key, else newest cache with same max_items bucket."""
        async with self._cache_lock:
            cached = self._events_cache.get(cache_key)
            if cached:
                self._stats["pco429FallbackReturns"] = int(self._stats.get("pco429FallbackReturns") or 0) + 1
                self._stats["last429FallbackAt"] = datetime.now(timezone.utc).isoformat()
                return list(cached[1])

            wanted_max_items = int(cache_key[2] or 0)
            best_ts: datetime | None = None
            best_items: list[dict[str, Any]] | None = None
            for key, (cached_at, cached_items) in self._events_cache.items():
                if int(key[2] or 0) != wanted_max_items:
                    continue
                if best_ts is None or cached_at > best_ts:
                    best_ts = cached_at
                    best_items = cached_items
            if best_items is not None:
                self._stats["pco429FallbackReturns"] = int(self._stats.get("pco429FallbackReturns") or 0) + 1
                self._stats["last429FallbackAt"] = datetime.now(timezone.utc).isoformat()
                return list(best_items)
        return None

    async def _get_instance_room_names(self, client: httpx.AsyncClient, instance_id: str) -> list[str]:
        """Return room resource names booked for an event instance.

        Uses the documented event_instance resource_bookings link and includes resource objects
        so we can extract room names like 'Gym', 'Sanctuary', etc.
        """
        now = datetime.now(timezone.utc)
        # Keep a short in-memory cache to reduce repeated per-instance calls across frequent sync/preview requests.
        room_cache_ttl_seconds = 15 * 60
        cached = self._room_names_cache.get(instance_id)
        if cached:
            cached_at, cached_rooms = cached
            if (now - cached_at).total_seconds() <= room_cache_ttl_seconds:
                self._stats["roomCacheHitReturns"] = int(self._stats.get("roomCacheHitReturns") or 0) + 1
                return list(cached_rooms)

        try:
            self._stats["resourceBookingRequests"] = int(self._stats.get("resourceBookingRequests") or 0) + 1
            resp = await self._get_with_429_retry(
                client,
                path=f"/calendar/v2/event_instances/{instance_id}/resource_bookings",
                params={"per_page": 100, "include": "resource"},
            )
            payload = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 429 and cached:
                # Return stale room data instead of dropping mapped-room resolution.
                self._stats["pco429FallbackReturns"] = int(self._stats.get("pco429FallbackReturns") or 0) + 1
                self._stats["last429FallbackAt"] = datetime.now(timezone.utc).isoformat()
                return list(cached[1])
            return []
        except Exception:
            return []

        included = payload.get("included") or []
        resources_by_id: dict[str, dict[str, Any]] = {}
        for inc in included:
            if not isinstance(inc, dict):
                continue
            if inc.get("type") != "Resource":
                continue
            rid = str(inc.get("id"))
            resources_by_id[rid] = inc

        room_names: list[str] = []
        for rb in payload.get("data") or []:
            rel = (rb.get("relationships") or {}).get("resource") or {}
            rdata = rel.get("data") or {}
            rid = str(rdata.get("id")) if rdata else ""
            if not rid:
                continue
            res = resources_by_id.get(rid)
            if not res:
                continue
            attrs = res.get("attributes") or {}
            if str(attrs.get("kind") or "") != "Room":
                continue
            name = str(attrs.get("name") or "").strip()
            if name and name not in room_names:
                room_names.append(name)

        self._room_names_cache[instance_id] = (datetime.now(timezone.utc), list(room_names))
        return room_names

    def _auth_headers(self) -> dict[str, str]:
        if self.settings.pco_auth_type == "personal_access_token":
            if not self.settings.pco_app_id or not self.settings.pco_secret:
                raise RuntimeError("PCO_APP_ID and PCO_SECRET are required for personal_access_token auth")
            token = base64.b64encode(f"{self.settings.pco_app_id}:{self.settings.pco_secret}".encode("utf-8")).decode(
                "ascii"
            )
            return {"Authorization": f"Basic {token}"}

        if self.settings.pco_auth_type == "oauth":
            if not self.settings.pco_access_token:
                raise RuntimeError("PCO_ACCESS_TOKEN is required for oauth auth")
            return {"Authorization": f"Bearer {self.settings.pco_access_token}"}

        raise RuntimeError(f"Unsupported PCO_AUTH_TYPE: {self.settings.pco_auth_type}")

    async def check_connectivity(self) -> bool:
        try:
            async with httpx.AsyncClient(base_url=str(self.settings.pco_base_url), timeout=15.0) as client:
                await client.get("/people/v2/people", headers=self._auth_headers(), params={"per_page": 1})
            return True
        except Exception:
            return False

    async def list_calendars(self, *, per_page: int = 100) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(base_url=str(self.settings.pco_base_url), timeout=30.0) as client:
            resp = await client.get(
                "/calendar/v2/calendars",
                headers=self._auth_headers(),
                params={"per_page": int(per_page)},
            )
            resp.raise_for_status()
            payload = resp.json()
            return payload.get("data") or []

    async def raw_event_instances_sample(self, *, per_page: int = 5) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=str(self.settings.pco_base_url), timeout=30.0) as client:
            resp = await client.get(
                self._event_instances_path(),
                headers=self._auth_headers(),
                params={"per_page": int(per_page), "order": "starts_at"},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_events(self, *, from_iso: str, to_iso: str, max_items: int | None = None) -> list[dict[str, Any]]:
        start_dt = datetime.fromisoformat(from_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        end_dt = datetime.fromisoformat(to_iso.replace("Z", "+00:00")).astimezone(timezone.utc)

        cache_key = self._normalize_window_key(from_iso=from_iso, to_iso=to_iso, max_items=max_items)
        now = datetime.now(timezone.utc)
        cache_seconds = max(0, int(self.settings.pco_events_cache_seconds))
        min_fetch_interval = max(0, int(self.settings.pco_min_fetch_interval_seconds))

        async with self._cache_lock:
            cached = self._events_cache.get(cache_key)
            if cached:
                cached_at, cached_items = cached
                age_s = (now - cached_at).total_seconds()
                if age_s <= cache_seconds:
                    self._stats["cacheHitReturns"] = int(self._stats.get("cacheHitReturns") or 0) + 1
                    self._stats["lastCacheHitAt"] = now.isoformat()
                    return list(cached_items)

                last_fetch = self._last_fetch_by_key.get(cache_key)
                if last_fetch and (now - last_fetch).total_seconds() < min_fetch_interval:
                    self._stats["cacheHitReturns"] = int(self._stats.get("cacheHitReturns") or 0) + 1
                    self._stats["minIntervalCacheReturns"] = int(self._stats.get("minIntervalCacheReturns") or 0) + 1
                    self._stats["lastCacheHitAt"] = now.isoformat()
                    return list(cached_items)

        items: list[dict[str, Any]] = []
        offset = 0
        pages = 0
        per_page = max(1, int(self.settings.pco_per_page))
        max_pages = max(1, int(self.settings.pco_max_pages))
        must_contain = (self.settings.pco_location_must_contain or "").strip().lower()
        start_s = start_dt.isoformat().replace("+00:00", "Z")
        end_s = end_dt.isoformat().replace("+00:00", "Z")
        self._stats["liveWindowFetches"] = int(self._stats.get("liveWindowFetches") or 0) + 1
        self._stats["lastLiveFetchAt"] = datetime.now(timezone.utc).isoformat()

        async with httpx.AsyncClient(base_url=str(self.settings.pco_base_url), timeout=30.0) as client:
            try:
                while True:
                    pages += 1
                    if pages > max_pages:
                        break

                    params = {
                        "per_page": per_page,
                        "offset": offset,
                        "order": "starts_at",
                        "where[starts_at][gte]": start_s,
                        "where[starts_at][lte]": end_s,
                    }
                    self._stats["eventInstanceRequests"] = int(self._stats.get("eventInstanceRequests") or 0) + 1
                    resp = await self._get_with_429_retry(
                        client,
                        path=self._event_instances_path(),
                        params=params,
                    )
                    payload = resp.json()

                    data = payload.get("data") or []
                    if not data:
                        break

                    for row in data:
                        attrs = row.get("attributes") or {}
                        starts_at = attrs.get("starts_at")
                        if not starts_at:
                            continue

                        try:
                            row_dt = datetime.fromisoformat(str(starts_at).replace("Z", "+00:00")).astimezone(timezone.utc)
                        except Exception:
                            continue

                        if row_dt < start_dt or row_dt > end_dt:
                            continue

                        raw_location = attrs.get("location")
                        raw_location_text = str(raw_location or "").strip()
                        raw_location_lc = raw_location_text.lower()

                        # Only enforce the location substring when PCO provided a non-empty location.
                        # Many events use resource bookings without populating the top-level location field.
                        if must_contain and raw_location_text and must_contain not in raw_location_lc:
                            continue

                        building = None
                        address = None
                        room = None

                        if isinstance(raw_location, str) and " - " in raw_location:
                            parts = [p.strip() for p in raw_location.split(" - ") if p.strip()]
                            if len(parts) >= 2:
                                building = parts[0]
                                # Heuristic: many entries look like "Campus - street address".
                                # If there are 3+ parts we treat the last part as a potential room.
                                if len(parts) >= 3:
                                    room = parts[-1]
                                    address = " - ".join(parts[1:-1])
                                else:
                                    address = parts[1]

                        instance_id = str(row.get("id"))
                        room_names = await self._get_instance_room_names(client, instance_id)
                        if room_names:
                            room = room_names[0]

                        items.append(
                            {
                                "id": instance_id,
                                "name": attrs.get("name"),
                                "startAt": attrs.get("starts_at"),
                                "endAt": attrs.get("ends_at"),
                                # Backwards-compatible key used by the rest of the app.
                                # Prefer a parsed room if present, otherwise fall back to raw location.
                                "room": room or raw_location,
                                "rooms": room_names,
                                "locationRaw": raw_location,
                                "building": building,
                                "address": address,
                                "roomSource": "resource_booking" if room_names else "location",
                            }
                        )

                        if max_items is not None and len(items) >= max_items:
                            break

                    if max_items is not None and len(items) >= max_items:
                        break

                    links = payload.get("links") or {}
                    if not links.get("next"):
                        break

                    offset += len(data)
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 429:
                    fallback = await self._get_cached_events_fallback(cache_key)
                    if fallback is not None:
                        return fallback
                raise

        async with self._cache_lock:
            now_done = datetime.now(timezone.utc)
            self._events_cache[cache_key] = (now_done, list(items))
            self._last_fetch_by_key[cache_key] = now_done

        return items
