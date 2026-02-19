from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

import httpx

from py_app.settings import Settings


class UnifiAccessClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _auth_headers(self) -> dict[str, str]:
        if self.settings.unifi_access_auth_type == "none":
            return {}

        if self.settings.unifi_access_auth_type == "api_token":
            if not self.settings.unifi_access_api_token:
                raise RuntimeError("UNIFI_ACCESS_API_TOKEN is required when UNIFI_ACCESS_AUTH_TYPE=api_token")
            header_name = self.settings.unifi_access_api_key_header
            token_value = self.settings.unifi_access_api_token
            if header_name.lower() == "authorization" and not token_value.lower().startswith("bearer "):
                token_value = f"Bearer {token_value}"
            return {header_name: token_value}

        raise RuntimeError(f"Unsupported UNIFI_ACCESS_AUTH_TYPE: {self.settings.unifi_access_auth_type}")

    async def check_connectivity(self) -> bool:
        try:
            async with httpx.AsyncClient(
                base_url=str(self.settings.unifi_access_base_url),
                timeout=15.0,
                verify=self.settings.unifi_access_verify_tls,
            ) as client:
                await client.get("/", headers=self._auth_headers())
            return True
        except Exception:
            return False

    async def list_doors(self) -> dict[str, Any]:
        """Attempt to list doors. UniFi Access has multiple API surfaces depending on version.

        We probe a small set of common endpoints and return the first successful JSON response.
        """

        candidate_paths = [
            "/api/v1/developer/doors",
            "/api/v1/developer/door",
            "/api/v1/doors",
            "/api/v1/door",
            "/api/doors",
            "/doors",
        ]

        last_error: str | None = None

        async with httpx.AsyncClient(
            base_url=str(self.settings.unifi_access_base_url),
            timeout=20.0,
            verify=self.settings.unifi_access_verify_tls,
        ) as client:
            for path in candidate_paths:
                try:
                    resp = await client.get(path, headers=self._auth_headers())
                    if resp.status_code >= 200 and resp.status_code < 300:
                        try:
                            return {
                                "path": path,
                                "status": resp.status_code,
                                "data": resp.json(),
                            }
                        except Exception:
                            return {
                                "path": path,
                                "status": resp.status_code,
                                "data": resp.text,
                            }

                    body_snip = (resp.text or "")[:500]
                    last_error = f"{path} -> HTTP {resp.status_code}: {body_snip}"
                except Exception as e:
                    last_error = f"{path} -> {type(e).__name__}: {str(e)}"

        raise RuntimeError(f"Unable to list doors from UniFi Access. Last error: {last_error}")

    async def _api_get(self, client: httpx.AsyncClient, path: str) -> dict[str, Any]:
        resp = await client.get(path, headers=self._auth_headers())
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("code") not in (None, "SUCCESS"):
            raise RuntimeError(f"UniFi GET {path} failed: {payload.get('code')} {payload.get('msg')}")
        return payload if isinstance(payload, dict) else {"data": payload}

    async def _api_post(self, client: httpx.AsyncClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = await client.post(path, headers=self._auth_headers(), json=body)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("code") not in (None, "SUCCESS"):
            raise RuntimeError(f"UniFi POST {path} failed: {payload.get('code')} {payload.get('msg')}")
        return payload if isinstance(payload, dict) else {"data": payload}

    async def _api_put(self, client: httpx.AsyncClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = await client.put(path, headers=self._auth_headers(), json=body)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("code") not in (None, "SUCCESS"):
            raise RuntimeError(f"UniFi PUT {path} failed: {payload.get('code')} {payload.get('msg')}")
        return payload if isinstance(payload, dict) else {"data": payload}

    async def _api_delete(self, client: httpx.AsyncClient, path: str) -> dict[str, Any]:
        resp = await client.delete(path, headers=self._auth_headers())
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("code") not in (None, "SUCCESS"):
            raise RuntimeError(f"UniFi DELETE {path} failed: {payload.get('code')} {payload.get('msg')}")
        return payload if isinstance(payload, dict) else {"data": payload}

    async def _list_access_schedules(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        payload = await self._api_get(client, "/api/v1/developer/access_policies/schedules")
        rows = payload.get("data") or []
        return rows if isinstance(rows, list) else []

    async def _list_access_policies(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        payload = await self._api_get(client, "/api/v1/developer/access_policies?page_num=1&page_size=200")
        rows = payload.get("data") or []
        return rows if isinstance(rows, list) else []

    @staticmethod
    def _normalize_resources(resources: Any) -> set[tuple[str, str]]:
        if not isinstance(resources, list):
            return set()
        out: set[tuple[str, str]] = set()
        for row in resources:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or "").strip()
            rtype = str(row.get("type") or "").strip()
            if rid and rtype:
                out.add((rid, rtype))
        return out

    @staticmethod
    def _normalize_weekly(weekly: Any) -> dict[str, list[tuple[str, str]]]:
        days = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
        out: dict[str, list[tuple[str, str]]] = {d: [] for d in days}
        if not isinstance(weekly, dict):
            return out

        for day in days:
            ranges = weekly.get(day) or []
            if not isinstance(ranges, list):
                continue
            normalized_day: list[tuple[str, str]] = []
            for row in ranges:
                if not isinstance(row, dict):
                    continue
                start = str(row.get("start_time") or "").strip()
                end = str(row.get("end_time") or "").strip()
                if start and end:
                    normalized_day.append((start, end))
            out[day] = sorted(normalized_day)
        return out

    async def _default_holiday_group_id(self, client: httpx.AsyncClient, schedules: list[dict[str, Any]]) -> str | None:
        for row in schedules:
            if bool(row.get("is_default")) and row.get("holiday_group_id"):
                return str(row.get("holiday_group_id"))
        for row in schedules:
            sid = row.get("id")
            if not sid:
                continue
            payload = await self._api_get(client, f"/api/v1/developer/access_policies/schedules/{sid}")
            detail = payload.get("data") or {}
            hid = detail.get("holiday_group_id")
            if hid:
                return str(hid)
        return None

    def _build_week_schedule(self, windows: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
        week_schedule: dict[str, list[dict[str, str]]] = {
            "sunday": [],
            "monday": [],
            "tuesday": [],
            "wednesday": [],
            "thursday": [],
            "friday": [],
            "saturday": [],
        }
        local_tz = ZoneInfo(self.settings.display_timezone)
        weekday_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

        ranges_by_day: dict[str, list[tuple[str, str]]] = {k: [] for k in week_schedule.keys()}
        for w in windows:
            start_raw = w.get("openStart")
            end_raw = w.get("openEnd")
            if not start_raw or not end_raw:
                continue
            try:
                start_dt = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00")).astimezone(local_tz)
                end_dt = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00")).astimezone(local_tz)
            except Exception:
                continue

            day = weekday_names[start_dt.weekday()]
            start_s = start_dt.strftime("%H:%M:%S")
            end_s = end_dt.strftime("%H:%M:%S")
            ranges_by_day[day].append((start_s, end_s))

        # Merge overlapping intervals per day for clean payloads.
        for day, ranges in ranges_by_day.items():
            if not ranges:
                continue
            ranges.sort(key=lambda x: x[0])
            merged: list[tuple[str, str]] = []
            for s, e in ranges:
                if not merged:
                    merged.append((s, e))
                    continue
                last_s, last_e = merged[-1]
                if s <= last_e:
                    merged[-1] = (last_s, max(last_e, e))
                else:
                    merged.append((s, e))

            week_schedule[day] = [{"start_time": s, "end_time": e} for s, e in merged]

        return week_schedule

    async def apply_desired_schedule(self, desired: dict[str, Any]) -> None:
        door_windows = desired.get("doorWindows") or []
        if not isinstance(door_windows, list) or not door_windows:
            return

        by_door: dict[str, list[dict[str, Any]]] = {}
        door_ids_by_door: dict[str, set[str]] = {}
        for w in door_windows:
            dk = str(w.get("doorKey") or "").strip()
            if not dk:
                continue
            by_door.setdefault(dk, []).append(w)
            for raw_id in (w.get("unifiDoorIds") or []):
                rid = str(raw_id).strip()
                if rid:
                    door_ids_by_door.setdefault(dk, set()).add(rid)

        if not by_door:
            return

        async with httpx.AsyncClient(
            base_url=str(self.settings.unifi_access_base_url),
            timeout=20.0,
            verify=self.settings.unifi_access_verify_tls,
        ) as client:
            existing_schedules = await self._list_access_schedules(client)
            existing_policies = await self._list_access_policies(client)
            schedules_by_name = {str(r.get("name") or ""): r for r in existing_schedules}
            policies_by_name = {str(r.get("name") or ""): r for r in existing_policies}

            for door_key in sorted(by_door.keys()):
                # Use pre-created schedules from UniFi UI and never auto-create schedules here.
                schedule_name_candidates = [
                    f"PCO Sync {door_key}",
                    f"PCO Sync | {door_key}",
                ]
                schedule_id = ""
                schedule_row: dict[str, Any] | None = None
                for candidate in schedule_name_candidates:
                    row = schedules_by_name.get(candidate)
                    if row and row.get("id"):
                        schedule_id = str(row.get("id"))
                        schedule_row = row
                        break

                if not schedule_id:
                    raise RuntimeError(
                        f"Missing UniFi schedule for door group '{door_key}'. "
                        f"Expected one of: {', '.join(schedule_name_candidates)}"
                    )

                door_ids = sorted(door_ids_by_door.get(door_key) or [])
                if not door_ids:
                    continue

                desired_weekly = self._build_week_schedule(by_door.get(door_key) or [])
                detail_payload = await self._api_get(
                    client,
                    f"/api/v1/developer/access_policies/schedules/{schedule_id}",
                )
                detail = detail_payload.get("data") or {}
                existing_weekly = detail.get("weekly") or {}
                if self._normalize_weekly(existing_weekly) != self._normalize_weekly(desired_weekly):
                    schedule_payload = {
                        "name": str(detail.get("name") or (schedule_row or {}).get("name") or f"PCO Sync {door_key}"),
                        "week_schedule": desired_weekly,
                        "holiday_group_id": str(
                            detail.get("holiday_group_id")
                            or (schedule_row or {}).get("holiday_group_id")
                            or ""
                        ),
                        "holiday_schedule": detail.get("holiday_schedule") or [],
                    }
                    await self._api_put(
                        client,
                        f"/api/v1/developer/access_policies/schedules/{schedule_id}",
                        schedule_payload,
                    )

                policy_name = f"PCO Sync Policy {door_key}"
                desired_resource = [{"id": rid, "type": "door"} for rid in door_ids]
                desired_resource_norm = self._normalize_resources(desired_resource)
                existing_policy = policies_by_name.get(policy_name)

                if existing_policy:
                    existing_schedule_id = str(existing_policy.get("schedule_id") or "")
                    existing_resource_norm = self._normalize_resources(existing_policy.get("resources"))
                    if existing_schedule_id == schedule_id and existing_resource_norm == desired_resource_norm:
                        continue

                    policy_id = str(existing_policy.get("id") or "").strip()
                    if policy_id:
                        await self._api_delete(client, f"/api/v1/developer/access_policies/{policy_id}")

                payload = {
                    "name": policy_name,
                    # UniFi expects `resource` for create payload; `resources` is in read responses.
                    "resource": desired_resource,
                    "schedule_id": schedule_id,
                }
                created = await self._api_post(client, "/api/v1/developer/access_policies", payload)
                data = created.get("data") if isinstance(created, dict) else None
                if isinstance(data, dict):
                    policies_by_name[policy_name] = data
