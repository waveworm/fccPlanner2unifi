from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import httpx

from py_app.settings import Settings


class UnifiAccessClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _raise_for_status_with_body(resp: httpx.Response, method: str, path: str) -> None:
        if resp.status_code < 400:
            return
        body = (resp.text or "").strip()
        body_snip = body[:500] if body else "(empty body)"
        raise RuntimeError(f"UniFi {method} {path} failed with HTTP {resp.status_code}: {body_snip}")

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
        self._raise_for_status_with_body(resp, "GET", path)
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("code") not in (None, "SUCCESS"):
            raise RuntimeError(f"UniFi GET {path} failed: {payload.get('code')} {payload.get('msg')}")
        return payload if isinstance(payload, dict) else {"data": payload}

    async def _api_post(self, client: httpx.AsyncClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = await client.post(path, headers=self._auth_headers(), json=body)
        self._raise_for_status_with_body(resp, "POST", path)
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("code") not in (None, "SUCCESS"):
            raise RuntimeError(f"UniFi POST {path} failed: {payload.get('code')} {payload.get('msg')}")
        return payload if isinstance(payload, dict) else {"data": payload}

    async def _api_put(self, client: httpx.AsyncClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = await client.put(path, headers=self._auth_headers(), json=body)
        self._raise_for_status_with_body(resp, "PUT", path)
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("code") not in (None, "SUCCESS"):
            raise RuntimeError(f"UniFi PUT {path} failed: {payload.get('code')} {payload.get('msg')}")
        return payload if isinstance(payload, dict) else {"data": payload}

    async def _api_delete(self, client: httpx.AsyncClient, path: str) -> dict[str, Any]:
        resp = await client.delete(path, headers=self._auth_headers())
        self._raise_for_status_with_body(resp, "DELETE", path)
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
            if end_dt <= start_dt:
                continue

            segment_start = start_dt
            while segment_start < end_dt:
                next_midnight = datetime.combine(
                    segment_start.date() + timedelta(days=1),
                    time.min,
                    tzinfo=local_tz,
                )
                segment_end = min(end_dt, next_midnight)
                day = weekday_names[segment_start.weekday()]
                start_s = segment_start.strftime("%H:%M:%S")
                # UniFi day ranges cannot cross midnight, so cap any carryover segment at the
                # last second of the current day and continue the remainder on the next day.
                end_s = "23:59:59" if segment_end == next_midnight and segment_end < end_dt else segment_end.strftime("%H:%M:%S")
                ranges_by_day[day].append((start_s, end_s))
                segment_start = segment_end

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

    async def get_door_statuses(self, door_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Return {door_id: {status, name, position}} for each requested UniFi door ID.

        status   : "LOCKED" | "UNLOCKED" | "UNKNOWN"
        position : "OPEN"   | "CLOSED"   | "UNKNOWN"   (physical door sensor)
        name     : human-readable label from UniFi
        """
        result: dict[str, dict[str, Any]] = {
            d: {"status": "UNKNOWN", "name": d, "position": "UNKNOWN"} for d in door_ids
        }
        if not door_ids:
            return result
        try:
            async with httpx.AsyncClient(
                base_url=str(self.settings.unifi_access_base_url),
                timeout=10.0,
                verify=self.settings.unifi_access_verify_tls,
            ) as client:
                resp = await client.get(
                    "/api/v1/developer/doors", headers=self._auth_headers()
                )
                resp.raise_for_status()
                payload = resp.json()
                doors = payload.get("data") or []
                if isinstance(doors, list):
                    for door in doors:
                        did = str(door.get("id") or "")
                        if did not in result:
                            continue
                        raw_status = str(door.get("door_lock_relay_status") or "UNKNOWN").upper()
                        raw_pos    = str(door.get("door_position_status")    or "UNKNOWN").upper()
                        result[did] = {
                            # Normalise short forms returned by some firmware versions
                            "status": {"LOCK": "LOCKED", "UNLOCK": "UNLOCKED"}.get(raw_status, raw_status),
                            "name": str(door.get("name") or door.get("full_name") or did),
                            "position": {"CLOSE": "CLOSED"}.get(raw_pos, raw_pos),
                        }
        except Exception:
            pass  # Return UNKNOWN for all on any error
        return result

    async def lock_door(self, door_id: str) -> None:
        """Force-lock a door immediately, overriding any active unlock schedule.

        Tries multiple candidate API shapes since the exact endpoint varies by
        UniFi Access firmware version.
        """
        async with httpx.AsyncClient(
            base_url=str(self.settings.unifi_access_base_url),
            timeout=10.0,
            verify=self.settings.unifi_access_verify_tls,
        ) as client:
            candidates = [
                # Firmware 2.x / developer API
                ("PUT",  f"/api/v1/developer/doors/{door_id}", {"door_guard": "KEEP_LOCK"}),
                # Alternative field name seen in some versions
                ("PUT",  f"/api/v1/developer/doors/{door_id}", {"keep_door_locked": True}),
                # Action-style endpoint
                ("POST", f"/api/v1/developer/doors/{door_id}/lock", {}),
                # Older style
                ("PUT",  f"/api/v1/developer/doors/{door_id}/overrides",
                 {"door_position_status": "LOCK"}),
            ]
            last_err = "No candidates tried"
            for method, path, body in candidates:
                try:
                    if method == "PUT":
                        resp = await client.put(
                            path, headers=self._auth_headers(), json=body
                        )
                    else:
                        resp = await client.post(
                            path, headers=self._auth_headers(), json=body
                        )
                    if resp.status_code < 300:
                        return
                    last_err = (
                        f"{method} {path} → HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                except Exception as e:
                    last_err = f"{method} {path} → {type(e).__name__}: {e}"
            raise RuntimeError(
                f"Could not lock door {door_id}. Last error: {last_err}"
            )

    async def get_door_policy_bindings(self, door_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Return policy/schedule bindings per door id from UniFi Access policies.

        Result shape:
          {
            "<door_id>": {
              "policyNames": [...],
              "scheduleNames": [...],
            },
            ...
          }
        """
        out: dict[str, dict[str, Any]] = {
            str(d): {"policyNames": [], "scheduleNames": []}
            for d in door_ids
            if str(d).strip()
        }
        if not out:
            return out

        did_set = set(out.keys())
        try:
            async with httpx.AsyncClient(
                base_url=str(self.settings.unifi_access_base_url),
                timeout=20.0,
                verify=self.settings.unifi_access_verify_tls,
            ) as client:
                schedules = await self._list_access_schedules(client)
                schedule_name_by_id = {
                    str(s.get("id") or ""): str(s.get("name") or "").strip()
                    for s in schedules
                    if s.get("id")
                }
                policies = await self._list_access_policies(client)

                policy_sets: dict[str, set[str]] = {did: set() for did in did_set}
                sched_sets: dict[str, set[str]] = {did: set() for did in did_set}

                for policy in policies:
                    if not isinstance(policy, dict):
                        continue
                    policy_name = str(policy.get("name") or "").strip()
                    schedule_name = schedule_name_by_id.get(str(policy.get("schedule_id") or ""), "").strip()
                    resources = policy.get("resources") or []
                    if not isinstance(resources, list):
                        continue
                    for res in resources:
                        if not isinstance(res, dict):
                            continue
                        if str(res.get("type") or "").strip().lower() != "door":
                            continue
                        did = str(res.get("id") or "").strip()
                        if did not in did_set:
                            continue
                        if policy_name:
                            policy_sets[did].add(policy_name)
                        if schedule_name:
                            sched_sets[did].add(schedule_name)

                for did in did_set:
                    out[did] = {
                        "policyNames": sorted(policy_sets.get(did) or set()),
                        "scheduleNames": sorted(sched_sets.get(did) or set()),
                    }
        except Exception:
            pass
        return out

    @staticmethod
    def _extract_unlock_schedule(payload: Any) -> tuple[bool, str, str]:
        """Best-effort parser for door-level unlock schedule fields."""
        known = False
        schedule_id = ""
        schedule_name = ""

        def _set_id_name(sid: Any, sname: Any) -> None:
            nonlocal schedule_id, schedule_name
            sid_s = str(sid or "").strip()
            sname_s = str(sname or "").strip()
            if sid_s and not schedule_id:
                schedule_id = sid_s
            if sname_s and not schedule_name:
                schedule_name = sname_s

        def _walk(node: Any, unlock_ctx: bool = False) -> None:
            nonlocal known
            if isinstance(node, dict):
                lowered = {str(k).lower(): k for k in node.keys()}
                local_unlock_ctx = unlock_ctx
                if any(("unlock" in lk and "schedule" in lk) for lk in lowered.keys()):
                    local_unlock_ctx = True
                    known = True
                if "enable_unlock_schedule" in lowered or "unlock_schedule_enabled" in lowered:
                    local_unlock_ctx = True
                    known = True

                # Common object shapes.
                for lk, orig in lowered.items():
                    if lk in ("unlock_schedule", "unlockschedule"):
                        known = True
                        value = node.get(orig)
                        if isinstance(value, dict):
                            _set_id_name(
                                value.get("id") or value.get("schedule_id") or value.get("unlock_schedule_id"),
                                value.get("name") or value.get("schedule_name") or value.get("unlock_schedule_name"),
                            )
                        elif isinstance(value, str):
                            _set_id_name("", value)

                if local_unlock_ctx:
                    _set_id_name(
                        node.get("unlock_schedule_id")
                        or node.get("unlockScheduleId")
                        or node.get("schedule_id")
                        or node.get("scheduleId"),
                        node.get("unlock_schedule_name")
                        or node.get("unlockScheduleName")
                        or node.get("schedule_name")
                        or node.get("scheduleName"),
                    )

                for lk, orig in lowered.items():
                    value = node.get(orig)
                    child_unlock_ctx = local_unlock_ctx or ("unlock" in lk and "schedule" in lk)
                    _walk(value, child_unlock_ctx)
                return

            if isinstance(node, list):
                for item in node:
                    _walk(item, unlock_ctx)

        _walk(payload, False)
        return known, schedule_id, schedule_name

    async def get_door_unlock_schedule_bindings(self, door_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Return door-level unlock schedule assignment per door id.

        Result shape:
          {
            "<door_id>": {
              "known": bool,                  # endpoint exposes unlock schedule config
              "unlockScheduleId": str,        # may be empty
              "unlockScheduleName": str,      # may be empty
              "source": str,                  # endpoint that provided the value
            }
          }
        """
        out: dict[str, dict[str, Any]] = {
            str(d): {
                "known": False,
                "unlockScheduleId": "",
                "unlockScheduleName": "",
                "source": "unknown",
            }
            for d in door_ids
            if str(d).strip()
        }
        if not out:
            return out

        did_set = set(out.keys())
        try:
            async with httpx.AsyncClient(
                base_url=str(self.settings.unifi_access_base_url),
                timeout=20.0,
                verify=self.settings.unifi_access_verify_tls,
            ) as client:
                # First pass: some firmware may include unlock schedule directly on door objects.
                payload = await self._api_get(client, "/api/v1/developer/doors")
                doors = payload.get("data") or []
                if isinstance(doors, list):
                    for row in doors:
                        if not isinstance(row, dict):
                            continue
                        did = str(row.get("id") or "").strip()
                        if did not in did_set:
                            continue
                        known, sid, sname = self._extract_unlock_schedule(row)
                        if known:
                            out[did] = {
                                "known": True,
                                "unlockScheduleId": sid,
                                "unlockScheduleName": sname,
                                "source": "GET /api/v1/developer/doors",
                            }

                # Second pass: probe per-door endpoints used by some controller versions.
                per_door_paths = [
                    ("/api/v1/developer/doors/{did}", False),
                    ("/api/v1/developer/doors/{did}/settings", False),
                    ("/api/v1/developer/doors/{did}/setting", False),
                    ("/api/v1/developer/doors/{did}/config", False),
                    ("/api/v1/developer/doors/{did}/unlock_schedule", True),
                    ("/api/v1/developer/doors/{did}/unlock-schedule", True),
                    ("/api/v1/developer/door_unlock_schedules/{did}", True),
                    ("/api/v1/developer/door_unlock_schedules?door_id={did}", True),
                ]
                for did in did_set:
                    if bool(out.get(did, {}).get("known")):
                        continue
                    for path_tpl, force_unlock_context in per_door_paths:
                        path = path_tpl.format(did=did)
                        try:
                            payload = await self._api_get(client, path)
                        except Exception:
                            continue
                        data = payload.get("data")
                        if force_unlock_context:
                            known, sid, sname = self._extract_unlock_schedule({"unlock_schedule": data})
                        else:
                            known, sid, sname = self._extract_unlock_schedule(data)
                        if known:
                            out[did] = {
                                "known": True,
                                "unlockScheduleId": sid,
                                "unlockScheduleName": sname,
                                "source": f"GET {path}",
                            }
                            break

                # If we have ids but not names, resolve via schedule list.
                unresolved = [
                    did for did, row in out.items()
                    if row.get("known") and row.get("unlockScheduleId") and not row.get("unlockScheduleName")
                ]
                if unresolved:
                    schedules = await self._list_access_schedules(client)
                    by_id = {
                        str(row.get("id") or "").strip(): str(row.get("name") or "").strip()
                        for row in schedules
                        if isinstance(row, dict) and row.get("id")
                    }
                    for did in unresolved:
                        sid = str(out[did].get("unlockScheduleId") or "").strip()
                        if sid and by_id.get(sid):
                            out[did]["unlockScheduleName"] = by_id[sid]
        except Exception:
            pass

        return out

    async def apply_desired_schedule(self, desired: dict[str, Any]) -> None:
        door_windows = desired.get("doorWindows") or []
        if not isinstance(door_windows, list):
            return

        by_door: dict[str, list[dict[str, Any]]] = {}
        door_ids_by_door: dict[str, set[str]] = {}
        desired_doors_cfg = desired.get("doorConfigs") or {}
        all_door_keys: set[str] = set()
        for w in door_windows:
            dk = str(w.get("doorKey") or "").strip()
            if not dk:
                continue
            all_door_keys.add(dk)
            by_door.setdefault(dk, []).append(w)
            for raw_id in (w.get("unifiDoorIds") or []):
                rid = str(raw_id).strip()
                if rid:
                    door_ids_by_door.setdefault(dk, set()).add(rid)
        if isinstance(desired_doors_cfg, dict):
            for dk, cfg in desired_doors_cfg.items():
                door_key = str(dk).strip()
                if not door_key:
                    continue
                all_door_keys.add(door_key)
                for raw_id in ((cfg or {}).get("unifiDoorIds") or []):
                    rid = str(raw_id).strip()
                    if rid:
                        door_ids_by_door.setdefault(door_key, set()).add(rid)

        if not all_door_keys:
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

            for door_key in sorted(all_door_keys):
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

                if not door_ids:
                    continue

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
