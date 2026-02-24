from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

_esc = html.escape  # shorthand used throughout HTML generation

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from py_app.event_overrides import (
    add_cancelled_event,
    load_cancelled_events,
    load_event_memory,
    load_event_overrides,
    remove_cancelled_event,
    save_event_overrides,
    validate_event_overrides,
)
from py_app.approvals import load_safe_hours, save_safe_hours
from py_app.logger import get_logger
from py_app.office_hours import load_office_hours, save_office_hours, validate_office_hours
from py_app.settings import Settings
from py_app.sync_service import SyncService
from py_app.vendors.unifi_access import UnifiAccessClient
from py_app.vendors.pco import PcoClient


# Shared CSS for all pages — plain Python string (no f-string) so CSS {} braces don't need escaping.
_SHARED_CSS = """
* { box-sizing: border-box; }
body {
  font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
  margin: 0; background: #f8fafc; color: #1e293b; min-height: 100vh;
}
.page { max-width: 1600px; margin: 0 auto; padding: 24px; }

/* Site header */
.site-header {
  background: #0f172a; color: #fff; padding: 0 24px;
  display: flex; align-items: center; height: 52px; gap: 0;
}
.site-header-title {
  font-size: 15px; font-weight: 700; margin-right: 32px; white-space: nowrap; color: #f1f5f9;
}
.site-nav { display: flex; gap: 4px; }
.site-nav a {
  color: #94a3b8; text-decoration: none; font-size: 14px; font-weight: 500;
  padding: 6px 12px; border-radius: 6px; transition: color .15s, background .15s;
}
.site-nav a:hover { color: #e2e8f0; background: rgba(255,255,255,.08); }
.site-nav a.active { color: #fff; background: rgba(255,255,255,.13); }

/* Cards */
.card {
  background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
  padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.04);
}
.card-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .07em; color: #64748b; margin: 0 0 12px; display: block;
}

/* Collapsible sections */
details.collapsible {
  background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
  margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.04); overflow: hidden;
}
details.collapsible > summary {
  padding: 14px 20px; cursor: pointer; list-style: none;
  display: flex; align-items: center; justify-content: space-between;
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .07em; color: #64748b; user-select: none;
}
details.collapsible > summary::-webkit-details-marker { display: none; }
details.collapsible > summary::after { content: '▼'; font-size: 9px; color: #94a3b8; }
details.collapsible[open] > summary { border-bottom: 1px solid #e2e8f0; }
details.collapsible[open] > summary::after { content: '▲'; }
.details-body { padding: 16px 20px; }

/* Status dots */
.dot {
  display: inline-block; width: 9px; height: 9px;
  border-radius: 50%; margin-right: 5px; vertical-align: middle; flex-shrink: 0;
}
.dot.ok  { background: #22c55e; }
.dot.err { background: #ef4444; }
.dot.warn { background: #f59e0b; }
.dot.unk { background: #94a3b8; }

/* Badges */
.badge {
  display: inline-block; padding: 2px 9px; border-radius: 99px;
  font-size: 11px; font-weight: 700; white-space: nowrap;
}
.badge-apply  { background: #dcfce7; color: #15803d; }
.badge-dryrun { background: #fef3c7; color: #b45309; }
.badge-err    { background: #fee2e2; color: #dc2626; }

/* Buttons */
button {
  border-radius: 8px; padding: 8px 16px; border: 1px solid #e2e8f0;
  background: #fff; cursor: pointer; font-size: 14px; font-weight: 500;
  transition: background .15s;
}
button:hover { background: #f1f5f9; }
button.primary { background: #2563eb; color: #fff; border-color: #2563eb; }
button.primary:hover { background: #1d4ed8; }
button.danger { color: #dc2626; border-color: #fca5a5; }
button.danger:hover { background: #fef2f2; }
button.sm { padding: 6px 13px; font-size: 13px; }

/* Tables */
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th {
  text-align: left; padding: 8px 10px; font-size: 12px; font-weight: 600;
  color: #64748b; border-bottom: 2px solid #e2e8f0; white-space: nowrap;
}
td { padding: 9px 10px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: #f8fafc; }

/* Form elements */
input[type="text"], input[type="number"], input[type="time"], input[type="password"] {
  padding: 7px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px;
}
input:focus, select:focus { outline: none; border-color: #2563eb; box-shadow: 0 0 0 2px #bfdbfe; }
input[type="checkbox"] { width: 17px; height: 17px; cursor: pointer; accent-color: #2563eb; }
select {
  padding: 7px 10px; border: 1px solid #d1d5db; border-radius: 6px;
  font-size: 14px; background: #fff; color: #1e293b;
}

/* Toast */
.toast {
  display: none; position: fixed; top: 20px; right: 20px;
  background: #059669; color: #fff; padding: 12px 20px;
  border-radius: 10px; font-size: 14px; z-index: 999;
  box-shadow: 0 4px 16px rgba(0,0,0,.15);
}
.toast.error { background: #dc2626; }

a { color: #2563eb; text-decoration: none; }
a:hover { text-decoration: underline; }

/* Stat grid */
.stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 16px; }
.stat-label { font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: .06em; }
.stat-val { font-size: 14px; color: #1e293b; font-weight: 500; margin-top: 2px; word-break: break-all; }

/* Page heading */
.page-heading { margin: 0 0 6px; font-size: 20px; font-weight: 700; }
.page-subtitle-text { color: #64748b; font-size: 14px; margin: 0 0 20px; }

/* ── Mobile ────────────────────────────────────────────────────────────── */
@media (max-width: 640px) {
  .page { padding: 12px; }
  /* Nav: stack title above links */
  .site-header {
    height: auto; min-height: 52px; padding: 8px 12px;
    flex-wrap: wrap; align-items: flex-start; gap: 0;
  }
  .site-header-title { margin-right: 0; padding: 4px 0 2px; width: 100%; }
  .site-nav { width: 100%; flex-wrap: wrap; padding-bottom: 6px; gap: 2px; }
  .site-nav a { font-size: 12px; padding: 4px 8px; }
  /* Cards */
  .card { padding: 14px 12px; }
  .details-body { padding: 14px 12px; }
  details.collapsible > summary { padding: 12px 14px; }
  /* Tables */
  th, td { padding: 6px 7px; font-size: 13px; }
  th { font-size: 11px; }
  /* Hide low-priority columns on small screens */
  .hide-mob { display: none !important; }
  /* Toast — anchor to bottom so it doesn't overlap content */
  .toast { top: auto; bottom: 16px; left: 12px; right: 12px; text-align: center; }
  /* Headings */
  .page-heading { font-size: 18px; }
  .stat-grid { grid-template-columns: 1fr 1fr; gap: 12px; }
}
"""


_DOOR_COLORS = ["#3b82f6", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#ec4899"]


def create_app() -> FastAPI:
    settings = Settings()
    logger = get_logger()

    app = FastAPI(title="PCO → UniFi Access Sync")
    sync_service = SyncService(settings, logger)
    unifi_client = UnifiAccessClient(settings)
    pco_client = PcoClient(settings)

    scheduler = AsyncIOScheduler(timezone="UTC")

    def _nav(active: str) -> str:
        """Generate the shared site header HTML."""
        pages = [
            ("dashboard",        "/dashboard",        "Dashboard"),
            ("settings",         "/settings",         "Room Mapping"),
            ("office-hours",     "/office-hours",     "Office Hours"),
            ("event-overrides",  "/event-overrides",  "Event Overrides"),
            ("general-settings", "/general-settings", "Settings"),
        ]
        links = ""
        for key, url, label in pages:
            cls = ' class="active"' if key == active else ""
            links += f'<a href="{url}"{cls}>{label}</a>'
        return (
            '<header class="site-header">'
            '<span class="site-header-title">PCO &#8594; UniFi Sync</span>'
            f'<nav class="site-nav">{links}</nav>'
            '</header>'
        )

    @app.on_event("startup")
    async def _startup() -> None:
        if settings.sync_cron and settings.sync_cron.strip():
            trigger = CronTrigger.from_crontab(settings.sync_cron, timezone="UTC")
            scheduler.add_job(sync_service.run_once, trigger=trigger, max_instances=1)
        else:
            scheduler.add_job(sync_service.run_once, "interval", seconds=int(settings.sync_interval_seconds), max_instances=1)
        scheduler.start()
        try:
            await sync_service.run_once()
        except Exception:
            pass

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        scheduler.shutdown(wait=False)

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard")

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/api/status")
    async def api_status() -> dict:
        return sync_service.snapshot()

    @app.get("/api/unifi/ping")
    async def api_unifi_ping() -> dict:
        ok = await unifi_client.check_connectivity()
        return {"ok": ok, "baseUrl": str(settings.unifi_access_base_url)}

    @app.get("/api/unifi/doors")
    async def api_unifi_doors() -> dict:
        return await unifi_client.list_doors()

    @app.get("/api/unifi/door-status")
    async def api_unifi_door_status() -> dict:
        """Return live lock status for every configured door group."""
        mapping = _read_mapping()
        doors_cfg: dict = mapping.get("doors") or {}
        # Collect all UniFi door IDs we care about
        all_ids: list[str] = []
        for door_data in doors_cfg.values():
            all_ids.extend(door_data.get("unifiDoorIds") or [])
        all_ids = list(dict.fromkeys(all_ids))  # dedupe, preserve order

        try:
            statuses = await unifi_client.get_door_statuses(all_ids)
        except Exception as exc:
            return {"doors": [], "error": str(exc)}

        groups = []
        for door_key, door_data in doors_cfg.items():
            unifi_ids = door_data.get("unifiDoorIds") or []
            groups.append({
                "key": door_key,
                "label": door_data.get("label") or door_key,
                "doors": [
                    {
                        "id": did,
                        **statuses.get(did, {"status": "UNKNOWN", "name": did, "position": "UNKNOWN"}),
                    }
                    for did in unifi_ids
                ],
            })
        return {"doors": groups, "error": None}

    @app.post("/api/unifi/door/{door_id}/lock")
    async def api_lock_door(door_id: str) -> dict:
        from fastapi.responses import JSONResponse
        try:
            await unifi_client.lock_door(door_id)
            return {"ok": True}
        except Exception as exc:
            return JSONResponse(status_code=502, content={"ok": False, "error": str(exc)})

    @app.get("/api/door-schedule")
    async def api_door_schedule() -> dict:
        """Return per-door unlock windows (local day + minutes) for the current sync window."""
        from zoneinfo import ZoneInfo
        from datetime import datetime, timedelta

        now = datetime.now(timezone.utc)
        end_dt = now + timedelta(hours=int(settings.sync_lookahead_hours))
        preview = await sync_service.get_preview(start_dt=now, end_dt=end_dt)
        door_windows = (preview.get("schedule") or {}).get("doorWindows") or []

        try:
            from py_app.mapping import load_room_door_mapping
            mapping_cfg = load_room_door_mapping(settings.room_door_mapping_file)
            all_door_keys = list((mapping_cfg.get("doors") or {}).keys())
        except Exception:
            all_door_keys = []

        color_for_key = {dk: _DOOR_COLORS[i % len(_DOOR_COLORS)] for i, dk in enumerate(all_door_keys)}
        local_tz = ZoneInfo(settings.display_timezone)
        door_map: dict[str, dict] = {}

        for w in door_windows:
            key = w["doorKey"]
            if key not in door_map:
                door_map[key] = {
                    "key": key,
                    "label": w["doorLabel"],
                    "color": color_for_key.get(key, _DOOR_COLORS[len(door_map) % len(_DOOR_COLORS)]),
                    "windows": [],
                }
            start_utc = datetime.fromisoformat(w["openStart"].replace("Z", "+00:00"))
            end_utc   = datetime.fromisoformat(w["openEnd"].replace("Z", "+00:00"))
            cur = start_utc.astimezone(local_tz)
            end_local = end_utc.astimezone(local_tz)
            while cur < end_local:
                next_mid = (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                seg_end = min(end_local, next_mid)
                start_min = cur.hour * 60 + cur.minute
                end_min   = seg_end.hour * 60 + seg_end.minute
                if end_min == 0:
                    end_min = 1440
                if end_min > start_min:
                    door_map[key]["windows"].append({
                        "day": cur.weekday(),  # 0=Mon … 6=Sun
                        "startMin": start_min,
                        "endMin": end_min,
                    })
                cur = next_mid

        # Return in mapping order so colors and display order stay consistent
        ordered = [door_map[dk] for dk in all_door_keys if dk in door_map]
        ordered += [v for dk, v in door_map.items() if dk not in all_door_keys]
        return {"timezone": settings.display_timezone, "doors": ordered}

    @app.get("/api/pco/calendars")
    async def api_pco_calendars() -> dict:
        data = await pco_client.list_calendars(per_page=200)
        out = []
        for row in data:
            attrs = row.get("attributes") or {}
            out.append({"id": row.get("id"), "name": attrs.get("name")})
        return {"count": len(out), "calendars": out}

    @app.get("/api/pco/event-instances/sample")
    async def api_pco_event_instances_sample(per_page: int = 5) -> dict:
        return await pco_client.raw_event_instances_sample(per_page=per_page)

    @app.get("/api/events/upcoming")
    async def api_events_upcoming(hours: int = 24, limit: int = 50) -> dict:
        now = datetime.now(timezone.utc)
        end_dt = now + timedelta(hours=int(hours))
        preview = await sync_service.get_preview(start_dt=now, end_dt=end_dt, limit=limit)
        return {"now": preview["now"], "start": preview["start"], "end": preview["end"], "limit": preview["limit"], "events": preview["events"], "rooms": preview["rooms"]}

    @app.get("/api/preview")
    async def api_preview(hours: int = 24, limit: int = 50) -> dict:
        now = datetime.now(timezone.utc)
        end_dt = now + timedelta(hours=int(hours))
        return await sync_service.get_preview(start_dt=now, end_dt=end_dt, limit=limit)

    @app.get("/api/config")
    async def api_config() -> dict:
        return {
            "applyToUnifi": sync_service.get_apply_to_unifi(),
            "syncCron": settings.sync_cron,
            "unifiBaseUrl": str(settings.unifi_access_base_url),
        }

    @app.post("/api/config/apply")
    async def api_config_apply(payload: dict = Body(...)) -> dict:
        value = bool(payload.get("applyToUnifi"))
        sync_service.set_apply_to_unifi(value)
        return {"ok": True, "applyToUnifi": sync_service.get_apply_to_unifi()}

    @app.post("/dashboard/apply")
    async def dashboard_apply(request: Request) -> RedirectResponse:
        raw = (await request.body()).decode("utf-8", "ignore")
        apply = (parse_qs(raw).get("apply") or ["false"])[0]
        sync_service.set_apply_to_unifi(str(apply).lower() in ("1", "true", "yes", "on"))
        return RedirectResponse(url="/dashboard", status_code=303)

    @app.post("/api/sync/run")
    async def api_sync_run() -> dict:
        await sync_service.run_once()
        return {"ok": True}

    @app.get("/api/approvals/pending")
    async def api_approvals_pending() -> dict:
        return {"pending": sync_service.get_pending_approvals()}

    @app.post("/api/approvals/approve")
    async def api_approvals_approve(payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        event_id = str(payload.get("id") or "").strip()
        if not event_id:
            return JSONResponse(status_code=422, content={"ok": False, "error": "id required"})
        name = sync_service.approve_event(event_id)
        return {"ok": True, "name": name}

    @app.post("/api/approvals/deny")
    async def api_approvals_deny(payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        event_id = str(payload.get("id") or "").strip()
        if not event_id:
            return JSONResponse(status_code=422, content={"ok": False, "error": "id required"})
        name = sync_service.deny_event(event_id)
        return {"ok": True, "name": name}

    @app.get("/api/events/cancelled")
    async def api_events_cancelled() -> dict:
        return load_cancelled_events(settings.cancelled_events_file)

    @app.post("/api/events/cancel")
    async def api_events_cancel(payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        event_id = str(payload.get("id") or "").strip()
        name = str(payload.get("name") or "").strip()
        start_at = str(payload.get("startAt") or "").strip()
        end_at = str(payload.get("endAt") or "").strip()
        if not event_id:
            return JSONResponse(status_code=422, content={"ok": False, "error": "id required"})
        add_cancelled_event(settings.cancelled_events_file, event_id, name, start_at, end_at)
        return {"ok": True}

    @app.post("/api/events/restore")
    async def api_events_restore(payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        event_id = str(payload.get("id") or "").strip()
        if not event_id:
            return JSONResponse(status_code=422, content={"ok": False, "error": "id required"})
        remove_cancelled_event(settings.cancelled_events_file, event_id)
        return {"ok": True}

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        local_tz = ZoneInfo(settings.display_timezone)

        def _fmt_local(iso_str: str | None) -> str:
            if not iso_str:
                return ""
            try:
                dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00")).astimezone(local_tz)
                return dt.strftime("%a %b %-d, %-I:%M %p")
            except Exception:
                return str(iso_str)

        def _fmt_local_short(iso_str: str | None) -> str:
            """Compact date for mobile: 2/26 2:30p"""
            if not iso_str:
                return ""
            try:
                dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00")).astimezone(local_tz)
                return dt.strftime("%-m/%-d %-I:%M") + dt.strftime("%p").lower()
            except Exception:
                return str(iso_str)

        def _fmt_dt_cell(iso_str: str | None) -> str:
            """Render a datetime table cell with full text on desktop, short on mobile."""
            return (
                f'<span class="hide-mob">{_fmt_local(iso_str)}</span>'
                f'<span class="show-mob">{_fmt_local_short(iso_str)}</span>'
            )

        status = sync_service.snapshot()
        last_sync_at_raw = status.get("lastSyncAt")
        last_sync_at = _fmt_local(last_sync_at_raw) if last_sync_at_raw else "(never)"
        last_sync_result = _esc(status.get("lastSyncResult") or "(none)")
        pco_status_raw = status.get("pcoStatus") or ""
        unifi_status_raw = status.get("unifiStatus") or ""
        pco_status = _esc(pco_status_raw)
        unifi_status = _esc(unifi_status_raw)

        def _status_dot(s: str) -> str:
            sl = s.lower()
            if "ok" in sl:
                return "ok"
            if not s or s in ("unknown", ""):
                return "unk"
            return "err"

        pco_dot = _status_dot(pco_status_raw)
        unifi_dot = _status_dot(unifi_status_raw)

        def _fmt_error_line(line: str) -> str:
            parts = line.split(" ", 1)
            if len(parts) == 2:
                ts_formatted = _fmt_local(parts[0])
                if ts_formatted and ts_formatted != parts[0]:
                    return f"{ts_formatted} {_esc(parts[1])}"
            return _esc(line)

        recent_errors_list = status.get("recentErrors") or []
        error_count = len(recent_errors_list)
        recent_errors_html = "<br/>".join([_fmt_error_line(e) for e in recent_errors_list]) or '<span style="color:#9ca3af">No recent errors</span>'

        pco_stats = status.get("pcoStats") or {}

        cancelled_data = load_cancelled_events(settings.cancelled_events_file)
        cancelled_instances = cancelled_data.get("instances") or []
        cancelled_ids = {str(i.get("id")) for i in cancelled_instances if i.get("id")}

        try:
            preview = await sync_service.get_upcoming_preview(limit=50)
        except Exception as exc:
            logger.error("Dashboard preview failed", extra={"err": str(exc)})
            preview = {"events": [], "schedule": {"items": [], "doorWindows": []}}
        preview_events = preview.get("events") or []
        preview_items = (preview.get("schedule") or {}).get("items") or []
        preview_windows = (preview.get("schedule") or {}).get("doorWindows") or []

        # Build event → door groups for display.
        event_doors: dict[str, list[str]] = {}
        for it in preview_items:
            event_id = str(it.get("sourceEventId") or "").strip()
            if not event_id:
                continue
            door_key = str(it.get("doorKey") or "").strip()
            if not door_key:
                continue
            existing = event_doors.setdefault(event_id, [])
            if door_key not in existing:
                existing.append(door_key)

        mapping = None
        try:
            from py_app.mapping import load_room_door_mapping
            mapping = load_room_door_mapping(settings.room_door_mapping_file)
        except Exception:
            mapping = None

        door_color_map: dict[str, str] = {}
        if isinstance(mapping, dict):
            for i, dk in enumerate(list((mapping.get("doors") or {}).keys())):
                door_color_map[dk] = _DOOR_COLORS[i % len(_DOOR_COLORS)]

        mapping_rows = []
        if isinstance(mapping, dict):
            rooms_map = mapping.get("rooms") or {}
            doors_map = mapping.get("doors") or {}
            for room_name in sorted(list(rooms_map.keys())):
                dk_list = rooms_map.get(room_name) or []
                door_labels = []
                door_ids = []
                for dk in dk_list:
                    d = doors_map.get(dk) or {}
                    door_labels.append(str(d.get("label") or dk))
                    ids = d.get("unifiDoorIds") or []
                    door_ids.append(",".join([str(x) for x in ids]) if ids else "")
                mapping_rows.append(
                    "<tr>"
                    f"<td>{_esc(room_name)}</td>"
                    f"<td>{_esc(', '.join(door_labels)) if door_labels else ''}</td>"
                    f'<td style="font-family:monospace;font-size:12px;color:#64748b">'
                    f'{_esc(" | ".join([x for x in door_ids if x]))}</td>'
                    "</tr>"
                )
        mapping_table_rows = "\n".join(mapping_rows)

        apply_to_unifi = bool(status.get("applyToUnifi"))
        mode_badge_class = "badge-apply" if apply_to_unifi else "badge-dryrun"
        mode_label = "APPLY" if apply_to_unifi else "DRY RUN"
        toggle_label = "Switch to DRY RUN" if apply_to_unifi else "Switch to APPLY"

        _ts_keys = {"lastLiveFetchAt", "lastCacheHitAt", "last429FallbackAt"}
        pco_stats_items_html = ""
        for k, v in sorted(pco_stats.items()):
            val = _fmt_local(str(v)) if k in _ts_keys and v and str(v) != "None" else _esc(str(v))
            pco_stats_items_html += (
                f'<div><div class="stat-label">{_esc(k)}</div>'
                f'<div class="stat-val">{val}</div></div>\n'
            )

        evt_count = len(preview_events)
        evt_plural = "s" if evt_count != 1 else ""
        item_count = len(preview_items)
        item_plural = "s" if item_count != 1 else ""

        events_rows_list = []
        for e in preview_events:
            eid = str(e.get("id") or "")
            _doors_cfg = (mapping.get("doors") or {}) if isinstance(mapping, dict) else {}
            _door_spans = []
            for _dk in event_doors.get(eid, []):
                _color = door_color_map.get(_dk, "#6b7280")
                _lbl = str((_doors_cfg.get(_dk) or {}).get("label") or _dk)
                _door_spans.append(f'<span style="color:{_color};font-weight:600">{_esc(_lbl)}</span>')
            doors_html = ", ".join(_door_spans) or '<span style="color:#9ca3af">(none mapped)</span>'
            rooms_str = ", ".join(e["rooms"]) if e.get("rooms") else str(e.get("room") or "")
            ename = str(e.get("name") or "")
            estart = str(e.get("startAt") or "")
            eend = str(e.get("endAt") or "")
            cancel_btn = (
                f'<button class="sm danger" '
                f'data-id="{_esc(eid)}" data-name="{_esc(ename)}" '
                f'data-start="{_esc(estart)}" data-end="{_esc(eend)}" '
                f'onclick="cancelEvent(this)">Cancel</button>'
            )
            events_rows_list.append(
                "<tr>"
                f'<td style="white-space:nowrap">{_fmt_dt_cell(e.get("startAt"))}</td>'
                f'<td style="white-space:nowrap">{_fmt_dt_cell(e.get("endAt"))}</td>'
                f"<td><strong>{_esc(ename)}</strong></td>"
                f'<td class="hide-mob">{_esc(rooms_str)}</td>'
                f'<td class="hide-mob">{doors_html}</td>'
                f"<td>{cancel_btn}</td>"
                "</tr>"
            )
        events_rows = "\n".join(events_rows_list)

        # Cancelled events warning card HTML
        if cancelled_instances:
            cancelled_rows = []
            for inst in cancelled_instances:
                iid = str(inst.get("id") or "")
                iname = _esc(str(inst.get("name") or ""))
                istart = _fmt_local(inst.get("startAt"))
                restore_btn = (
                    f'<button class="sm" data-id="{_esc(iid)}" '
                    f'onclick="restoreEvent(this)">Restore</button>'
                )
                cancelled_rows.append(
                    f"<tr>"
                    f'<td style="white-space:nowrap">{istart}</td>'
                    f"<td><strong>{iname}</strong></td>"
                    f"<td>{restore_btn}</td>"
                    f"</tr>"
                )
            cancelled_rows_html = "\n".join(cancelled_rows)
            cancelled_card_html = f"""
    <div class="card" style="border-color:#fca5a5;background:#fff8f8;">
      <span class="card-title" style="color:#dc2626;">&#9888; Cancelled Events ({len(cancelled_instances)})</span>
      <p style="font-size:13px;color:#7f1d1d;margin:0 0 12px;">
        These events are suppressed from the door schedule until restored or until 24 hours after they end.
      </p>
      <div style="overflow:auto;">
        <table>
          <thead>
            <tr><th>Scheduled Start</th><th>Event</th><th>Action</th></tr>
          </thead>
          <tbody>
            {cancelled_rows_html}
          </tbody>
        </table>
      </div>
    </div>"""
        else:
            cancelled_card_html = ""

        # Pending approvals card
        pending_approvals = sync_service.get_pending_approvals()
        if pending_approvals:
            approval_rows = []
            for pa in pending_approvals:
                paid = _esc(str(pa.get("id") or ""))
                paname = _esc(str(pa.get("name") or ""))
                pastart = _fmt_local(pa.get("startAt"))
                pareason = _esc(str(pa.get("reason") or ""))
                approve_btn = f'<button class="sm primary" data-id="{paid}" onclick="approveEvent(this)">Approve</button>'
                deny_btn = f'<button class="sm danger" data-id="{paid}" onclick="denyEvent(this)" style="margin-left:6px">Deny</button>'
                approval_rows.append(
                    f"<tr>"
                    f'<td style="white-space:nowrap">{pastart}</td>'
                    f"<td><strong>{paname}</strong></td>"
                    f'<td style="color:#92400e;font-size:13px">{pareason}</td>'
                    f"<td>{approve_btn}{deny_btn}</td>"
                    f"</tr>"
                )
            approval_rows_html = "\n".join(approval_rows)
            pending_card_html = f"""
    <div class="card" style="border-color:#fcd34d;background:#fffbeb;">
      <span class="card-title" style="color:#b45309;">&#128274; Pending Approval Required ({len(pending_approvals)})</span>
      <p style="font-size:13px;color:#78350f;margin:0 0 12px;">
        These events fall outside safe hours (5 AM – 10 PM weekdays/weekends, 11 PM on Fridays)
        and are <strong>not unlocking any doors</strong> until approved.
        Approving also auto-approves all future occurrences of that event name.
      </p>
      <div style="overflow:auto;">
        <table>
          <thead><tr><th>Scheduled Start</th><th>Event</th><th>Reason</th><th>Action</th></tr></thead>
          <tbody>{approval_rows_html}</tbody>
        </table>
      </div>
    </div>"""
        else:
            pending_card_html = ""

        err_badge = f'<span class="badge badge-err" style="margin-left:8px">{error_count}</span>' if error_count else ""

        html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Dashboard — PCO UniFi Sync</title>
  <style>{_SHARED_CSS}
    .status-bar {{ display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
    .status-items {{ display: flex; align-items: center; gap: 16px; flex-wrap: wrap; flex: 1; min-width: 0; }}
    .status-item {{ display: flex; align-items: center; font-size: 14px; color: #374151; white-space: nowrap; }}
    .status-actions {{ display: flex; gap: 8px; flex-shrink: 0; }}
    .event-count {{ font-size: 12px; color: #64748b; font-weight: 400; text-transform: none; letter-spacing: 0; margin-left: 6px; }}
    .show-mob {{ display: none; }}
    @media (max-width: 640px) {{
      .show-mob {{ display: inline; }}
      .status-bar {{ gap: 10px; }}
      .status-items {{ gap: 10px; width: 100%; }}
      .status-actions {{ width: 100%; }}
      .status-actions button, .status-actions form {{ flex: 1; }}
      .status-actions form button {{ width: 100%; }}
      .event-count {{ display: block; margin: 4px 0 0; }}
    }}
    .sched-grid {{ width: 100%; }}
    .sched-lbl {{ width: 28px; flex-shrink: 0; font-size: 10px; color: #94a3b8; padding-right: 4px;
      display: flex; align-items: center; justify-content: flex-end; }}
    .sched-track {{ position: relative; flex: 1; border-left: 1px solid #e2e8f0; }}
    .sched-hr {{ position: absolute; font-size: 9px; color: #94a3b8; transform: translateX(-50%); top: 1px; user-select: none; }}
    .sched-vline {{ position: absolute; top: 0; bottom: 0; border-left: 1px solid #f3f4f6; }}
    .sched-vline-major {{ border-color: #e2e8f0; }}
    .sched-modal-overlay {{ position: fixed; inset: 0; background: rgba(0,0,0,0.45); z-index: 9999;
      display: none; align-items: center; justify-content: center; }}
    .sched-modal-overlay.open {{ display: flex; }}
    .sched-modal {{ background: white; border-radius: 12px; padding: 20px; max-width: 860px;
      width: 95%; max-height: 90vh; overflow: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }}
  </style>
</head>
<body>
  {_nav("dashboard")}
  <div class="page">
    <div id="dash-toast" class="toast"></div>

    <!-- Status bar -->
    <div class="card">
      <div class="status-bar">
        <div class="status-items">
          <div class="status-item"><span class="dot {pco_dot}"></span>PCO: {pco_status or "unknown"}</div>
          <div class="status-item"><span class="dot {unifi_dot}"></span>UniFi: {unifi_status or "unknown"}</div>
          <div class="status-item" style="color:#64748b;">Last sync: {last_sync_at}</div>
          <div class="status-item"><span class="badge {mode_badge_class}">{mode_label}</span></div>
        </div>
        <div class="status-actions">
          <button id="syncBtn" class="primary sm" onclick="runSync()">Sync Now</button>
          <form method="post" action="/dashboard/apply" style="margin:0">
            <input type="hidden" name="apply" value="{str(not apply_to_unifi).lower()}" />
            <button type="submit" class="sm">{toggle_label}</button>
          </form>
        </div>
      </div>
    </div>

    {pending_card_html}

    <!-- Door Status -->
    <div class="card" id="doorStatusCard">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;gap:8px;">
        <span class="card-title" style="margin:0">Door Status</span>
        <div style="display:flex;align-items:center;gap:8px;">
          <span id="dsTime" style="font-size:11px;color:#94a3b8"></span>
          <button class="sm" onclick="refreshDoorStatus()" title="Refresh" style="padding:3px 7px;font-size:16px;line-height:1;">↻</button>
        </div>
      </div>
      <div id="dsBody" style="color:#64748b;font-size:14px;">Loading…</div>
    </div>

    <!-- Upcoming Events (always visible) -->
    <div class="card">
      <span class="card-title">
        Upcoming Events
        <span class="event-count">· {evt_count} event{evt_plural} · {item_count} schedule item{item_plural}</span>
      </span>
      <div style="overflow:auto;">
        <table>
          <thead>
            <tr>
              <th>Start</th>
              <th>End</th>
              <th>Event</th>
              <th class="hide-mob">Room(s)</th>
              <th class="hide-mob">Door Group(s)</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {events_rows or '<tr><td colspan="6" style="padding:12px;color:#9ca3af;">No upcoming events found.</td></tr>'}
          </tbody>
        </table>
      </div>
      <p style="margin:12px 0 0;font-size:13px;color:#64748b;">
        <a href="/api/preview">Preview JSON</a> &nbsp;·&nbsp;
        <a href="/api/events/upcoming">Upcoming events JSON</a> &nbsp;·&nbsp;
        <a href="/api/pco/calendars">PCO calendars</a> &nbsp;·&nbsp;
        <a href="/api/pco/event-instances/sample">PCO event_instances sample</a>
      </p>
    </div>

    {cancelled_card_html}

    <!-- Sync Details (collapsed) -->
    <details class="collapsible">
      <summary><span>Sync Details</span></summary>
      <div class="details-body">
        <div class="stat-grid">
          <div><div class="stat-label">Last Sync</div><div class="stat-val">{last_sync_at}</div></div>
          <div><div class="stat-label">Result</div><div class="stat-val">{last_sync_result}</div></div>
          <div><div class="stat-label">Mode</div><div class="stat-val"><span class="badge {mode_badge_class}">{mode_label}</span></div></div>
          <div><div class="stat-label">Events found</div><div class="stat-val">{evt_count}</div></div>
          <div><div class="stat-label">Schedule items</div><div class="stat-val">{item_count}</div></div>
          <div><div class="stat-label">Door windows</div><div class="stat-val">{len(preview_windows)}</div></div>
        </div>
      </div>
    </details>

    <!-- Recent Errors (collapsed) -->
    <details class="collapsible">
      <summary><span>Recent Errors {err_badge}</span></summary>
      <div class="details-body" style="font-size:13px;line-height:1.7;">
        {recent_errors_html}
      </div>
    </details>

    <!-- PCO API Stats (collapsed) -->
    <details class="collapsible">
      <summary><span>PCO API Stats</span></summary>
      <div class="details-body">
        <div class="stat-grid">
          {pco_stats_items_html or '<div><div class="stat-val" style="color:#9ca3af">No stats yet.</div></div>'}
        </div>
      </div>
    </details>


    <!-- Room → Door Mapping (collapsed) -->
    <details class="collapsible">
      <summary><span>Room → Door Mapping</span></summary>
      <div class="details-body">
        <div style="overflow:auto;">
          <table>
            <thead>
              <tr><th>Room</th><th>Door group(s)</th><th>UniFi door IDs</th></tr>
            </thead>
            <tbody>
              {mapping_table_rows or '<tr><td colspan="3" style="padding:12px;color:#9ca3af;">No mapping configured.</td></tr>'}
            </tbody>
          </table>
        </div>
        <p style="margin:12px 0 0;font-size:13px;"><a href="/settings">Edit room mapping →</a></p>
      </div>
    </details>

  </div>
  <script>
    const _toast = () => document.getElementById('dash-toast');
    function _showToast(msg, ok) {{
      const t = _toast();
      t.textContent = msg;
      t.style.background = ok ? '#059669' : '#dc2626';
      t.style.display = 'block';
      setTimeout(() => {{ t.style.display = 'none'; }}, ok ? 2500 : 4000);
    }}

    async function runSync() {{
      const btn = document.getElementById('syncBtn');
      btn.disabled = true; btn.textContent = 'Syncing…';
      try {{
        const resp = await fetch('/api/sync/run', {{ method: 'POST' }});
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        _showToast('Sync triggered successfully.', true);
        setTimeout(() => location.reload(), 2200);
      }} catch (err) {{
        _showToast('Sync failed: ' + err.message, false);
        btn.disabled = false; btn.textContent = 'Sync Now';
      }}
    }}

    async function cancelEvent(btn) {{
      const id = btn.dataset.id, name = btn.dataset.name;
      const startAt = btn.dataset.start, endAt = btn.dataset.end;
      if (!confirm('Cancel "' + name + '"? It will be removed from the door schedule until restored.')) return;
      btn.disabled = true;
      try {{
        const resp = await fetch('/api/events/cancel', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{ id, name, startAt, endAt }}),
        }});
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        _showToast('"' + name + '" cancelled.', true);
        setTimeout(() => location.reload(), 1500);
      }} catch (err) {{
        _showToast('Cancel failed: ' + err.message, false);
        btn.disabled = false;
      }}
    }}

    async function approveEvent(btn) {{
      const id = btn.dataset.id;
      btn.disabled = true;
      try {{
        const resp = await fetch('/api/approvals/approve', {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{ id }}),
        }});
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        _showToast('Approved. Doors will open on next sync.', true);
        setTimeout(() => location.reload(), 1500);
      }} catch (err) {{
        _showToast('Approve failed: ' + err.message, false);
        btn.disabled = false;
      }}
    }}

    async function denyEvent(btn) {{
      const id = btn.dataset.id;
      btn.disabled = true;
      try {{
        const resp = await fetch('/api/approvals/deny', {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{ id }}),
        }});
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        _showToast('Event denied. Doors will remain locked.', true);
        setTimeout(() => location.reload(), 1500);
      }} catch (err) {{
        _showToast('Deny failed: ' + err.message, false);
        btn.disabled = false;
      }}
    }}

    // ── Door Status ──────────────────────────────────────────────────────────
    const DS_REFRESH_MS = {settings.door_status_refresh_seconds * 1000};
    let _lastSchedData = null;

    async function refreshDoorStatus() {{
      try {{
        const [sR, schR] = await Promise.all([
          fetch('/api/unifi/door-status'),
          fetch('/api/door-schedule')
        ]);
        const sd = await sR.json(), sch = await schR.json();
        _lastSchedData = {{status: sd, sched: sch}};
        renderDoorStatus(sd, sch);
      }} catch (e) {{
        document.getElementById('dsBody').innerHTML =
          '<span style="color:#ef4444">Error: ' + e.message + '</span>';
      }}
    }}

    // Build a reusable schedule grid (compact or expanded)
    function buildSchedGrid(doors, opts) {{
      const laneH = opts.laneH, labelH = opts.labelH, hourStep = opts.hourStep,
            showLabels = opts.showLabels, altBg = opts.altBg;
      const DAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
      const n = doors.length;
      const rowH = n * laneH + 4;
      let html = '<div class="sched-grid">';

      // Header row
      html += '<div style="display:flex;height:' + labelH + 'px">'
        + '<div class="sched-lbl"></div><div class="sched-track">';
      for (let h = 0; h <= 24; h += hourStep) {{
        html += '<span class="sched-hr" style="left:' + (h/24*100).toFixed(2) + '%">'
          + String(h%24).padStart(2,'0') + '</span>';
      }}
      html += '</div></div>';

      // Day rows
      for (let day = 0; day < 7; day++) {{
        const bg = (altBg && day%2===0) ? '#f8fafc' : 'transparent';
        html += '<div style="display:flex;height:' + rowH + 'px;background:' + bg + '">'
          + '<div class="sched-lbl" style="height:' + rowH + 'px">' + DAYS[day] + '</div>'
          + '<div class="sched-track">';
        // Grid lines
        for (let h = 0; h <= 24; h++) {{
          html += '<div class="sched-vline' + (h%hourStep===0?' sched-vline-major':'')
            + '" style="left:' + (h/24*100).toFixed(3) + '%"></div>';
        }}
        // Per-door stacked bars
        for (let di = 0; di < n; di++) {{
          const door = doors[di];
          for (const win of (door.windows||[])) {{
            if (win.day !== day) continue;
            const l  = (win.startMin/1440*100).toFixed(3);
            const w  = ((win.endMin-win.startMin)/1440*100).toFixed(3);
            const t  = 2 + di*laneH;
            const bh = laneH - 2;
            const sH = String(Math.floor(win.startMin/60)).padStart(2,'0');
            const sM = String(win.startMin%60).padStart(2,'0');
            const eH = String(Math.floor(win.endMin/60)%24).padStart(2,'0');
            const eM = String(win.endMin%60).padStart(2,'0');
            const tStr = sH+':'+sM+'–'+eH+':'+eM;
            html += '<div style="position:absolute;left:' + l + '%;width:' + w
              + '%;top:' + t + 'px;height:' + bh
              + 'px;border-radius:2px;background:' + door.color
              + ';opacity:0.85;overflow:hidden;min-width:3px" title="' + door.label + ' ' + tStr + '">';
            if (showLabels) {{
              html += '<span style="position:absolute;left:4px;top:50%;transform:translateY(-50%);'
                + 'font-size:10px;color:white;white-space:nowrap;font-weight:600">' + tStr + '</span>';
            }}
            html += '</div>';
          }}
        }}
        html += '</div></div>';
      }}
      html += '</div>';
      return html;
    }}

    // Build live-status lookup maps from statusData
    function buildLiveMaps(statusData) {{
      const liveByKey = {{}}, idByKey = {{}}, positionByKey = {{}};
      for (const g of (statusData.doors||[])) {{
        let unlocked=false, unknown=true, pos='UNKNOWN';
        for (const d of (g.doors||[])) {{
          if (d.status==='UNLOCKED') {{ unlocked=true; unknown=false; }}
          else if (d.status==='LOCKED') {{ unknown=false; }}
          if (!idByKey[g.key]) idByKey[g.key]=d.id;
          if (pos==='UNKNOWN' && d.position && d.position!=='UNKNOWN') pos=d.position;
        }}
        liveByKey[g.key]     = unknown?'UNKNOWN':(unlocked?'UNLOCKED':'LOCKED');
        positionByKey[g.key] = pos;
      }}
      return {{liveByKey, idByKey, positionByKey}};
    }}

    // Build the legend HTML (used in both card and modal)
    function buildLegend(doors, liveByKey, idByKey, positionByKey, closeModalOnLock) {{
      let html = '';
      for (const d of doors) {{
        const live = liveByKey[d.key]||'UNKNOWN';
        const isUnlocked=live==='UNLOCKED', isUnknown=live==='UNKNOWN';
        const doorId=idByKey[d.key];
        const pos=positionByKey[d.key]||'UNKNOWN';

        // Lock/unlock badge
        const lockBg  = isUnknown?'#f1f5f9':(isUnlocked?'#dcfce7':'#fee2e2');
        const lockClr = isUnknown?'#64748b':(isUnlocked?'#15803d':'#dc2626');
        const lockTxt = isUnknown?'?':(isUnlocked?'Unlocked':'Locked');
        const lockCb = closeModalOnLock?'lockDoor(this);closeSchedModal()':'lockDoor(this)';
        const lockAttrs = (isUnlocked&&doorId)
          ? ' data-door-id="'+doorId+'" data-label="'+d.label+'" onclick="'+lockCb+'" style="cursor:pointer" title="Click to lock"'
          : '';

        // Door position badge (physical sensor)
        const posBadge = (pos==='UNKNOWN') ? ''
          : '<span style="font-size:10px;font-weight:600;padding:2px 6px;border-radius:4px;background:'
            +(pos==='OPEN'?'#fef3c7':'#f1f5f9')+';color:'
            +(pos==='OPEN'?'#92400e':'#475569')+'">'
            +(pos==='OPEN'?'Door Open':'Door Closed')+'</span>';

        html += '<div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap">'
          + '<span style="width:10px;height:10px;border-radius:2px;background:'+d.color+';flex-shrink:0"></span>'
          + '<span style="font-size:12px;font-weight:600;color:#1e293b">'+d.label+'</span>'
          + '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:'+lockBg+';color:'+lockClr+'"'+lockAttrs+'>'+lockTxt+'</span>'
          + posBadge
          + '</div>';
      }}
      return html;
    }}

    function renderDoorStatus(statusData, schedData) {{
      const body   = document.getElementById('dsBody');
      const timeEl = document.getElementById('dsTime');
      const doors  = schedData.doors||[];
      const {{liveByKey, idByKey, positionByKey}} = buildLiveMaps(statusData);

      const legend = '<div style="display:flex;flex-direction:column;gap:5px;margin-bottom:10px">'
        + buildLegend(doors, liveByKey, idByKey, positionByKey, false) + '</div>';

      const grid = '<div onclick="openSchedModal()" style="cursor:pointer" title="Click for detail">'
        + buildSchedGrid(doors, {{laneH:5, labelH:12, hourStep:4, showLabels:false, altBg:true}})
        + '</div>';

      body.innerHTML = !doors.length
        ? '<span style="color:#9ca3af">No scheduled windows in the current sync window.</span>'
        : legend + grid;
      timeEl.textContent = new Date().toLocaleTimeString();
    }}

    function openSchedModal() {{
      if (!_lastSchedData) return;
      const {{status: sd, sched: sch}} = _lastSchedData;
      const doors = sch.doors||[];
      const {{liveByKey, idByKey, positionByKey}} = buildLiveMaps(sd);

      let overlay = document.getElementById('schedModalOverlay');
      if (!overlay) {{
        overlay = document.createElement('div');
        overlay.id = 'schedModalOverlay';
        overlay.className = 'sched-modal-overlay';
        overlay.addEventListener('click', function(e) {{ if (e.target===overlay) closeSchedModal(); }});
        document.body.appendChild(overlay);
      }}

      const legend = '<div style="display:flex;flex-direction:column;gap:7px;margin-bottom:16px">'
        + buildLegend(doors, liveByKey, idByKey, positionByKey, true) + '</div>';

      overlay.innerHTML = '<div class="sched-modal">'
        + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">'
        + '<strong style="font-size:15px;color:#1e293b">Door Schedule — Next 7 Days</strong>'
        + '<button onclick="closeSchedModal()" style="background:none;border:none;font-size:20px;cursor:pointer;color:#94a3b8;line-height:1">✕</button>'
        + '</div>'
        + legend
        + '<div style="font-size:11px;color:#94a3b8;margin-bottom:10px">Times in ' + sch.timezone + '</div>'
        + buildSchedGrid(doors, {{laneH:22, labelH:18, hourStep:2, showLabels:true, altBg:true}})
        + '</div>';
      overlay.className = 'sched-modal-overlay open';
    }}

    function closeSchedModal() {{
      const overlay = document.getElementById('schedModalOverlay');
      if (overlay) overlay.className = 'sched-modal-overlay';
    }}

    async function lockDoor(el) {{
      const doorId = el.dataset.doorId;
      const label  = el.dataset.label;
      if (!confirm('Lock "' + label + '" now?\\n\\nThis will override any active unlock schedule.')) return;
      try {{
        const resp = await fetch('/api/unifi/door/' + doorId + '/lock', {{method: 'POST'}});
        const data = await resp.json();
        if (!resp.ok || !data.ok) throw new Error(data.error || 'HTTP ' + resp.status);
        await refreshDoorStatus();
      }} catch (e) {{
        alert('Lock failed: ' + e.message);
      }}
    }}

    refreshDoorStatus();
    if (DS_REFRESH_MS > 0) {{ setInterval(refreshDoorStatus, DS_REFRESH_MS); }}

    async function restoreEvent(btn) {{
      const id = btn.dataset.id;
      btn.disabled = true;
      try {{
        const resp = await fetch('/api/events/restore', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{ id }}),
        }});
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        _showToast('Event restored.', true);
        setTimeout(() => location.reload(), 1500);
      }} catch (err) {{
        _showToast('Restore failed: ' + err.message, false);
        btn.disabled = false;
      }}
    }}
  </script>
</body>
</html>"""
        return HTMLResponse(content=html_out, status_code=200)

    # ── Mapping API ──────────────────────────────────────────────────────

    def _read_mapping() -> dict:
        path = Path(settings.room_door_mapping_file).resolve()
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_mapping(data: dict) -> None:
        path = Path(settings.room_door_mapping_file).resolve()
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def _validate_mapping(data: dict) -> str | None:
        """Return an error string if the mapping payload is structurally invalid, else None."""
        if not isinstance(data, dict):
            return "Payload must be a JSON object"
        if not isinstance(data.get("doors"), dict):
            return "'doors' must be an object"
        if not isinstance(data.get("rooms"), dict):
            return "'rooms' must be an object"
        for dk, dv in data["doors"].items():
            if not isinstance(dv, dict):
                return f"doors['{dk}'] must be an object"
            if not isinstance(dv.get("unifiDoorIds", []), list):
                return f"doors['{dk}'].unifiDoorIds must be an array"
        for rk, rv in data["rooms"].items():
            if not isinstance(rv, list):
                return f"rooms['{rk}'] must be an array of door keys"
            for dk in rv:
                if dk not in data["doors"]:
                    return f"rooms['{rk}'] references unknown door key '{dk}'"
        rules = data.get("rules") or {}
        if not isinstance(rules, dict):
            return "'rules' must be an object"
        excl = rules.get("excludeEventsByRoomContains")
        if excl is not None and not isinstance(excl, list):
            return "'rules.excludeEventsByRoomContains' must be an array of strings"
        return None

    @app.get("/api/mapping")
    async def api_mapping_get() -> dict:
        return _read_mapping()

    @app.post("/api/mapping")
    async def api_mapping_save(payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        err = _validate_mapping(payload)
        if err:
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        _write_mapping(payload)
        return {"ok": True}

    # ── Settings page ────────────────────────────────────────────────────

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page() -> HTMLResponse:
        mapping = _read_mapping()
        doors_map = mapping.get("doors") or {}
        rooms_map = mapping.get("rooms") or {}
        defaults = mapping.get("defaults") or {}
        rules = mapping.get("rules") or {}

        door_keys = list(doors_map.keys())
        room_names = sorted(rooms_map.keys())

        rows_html = ""
        for room in room_names:
            assigned = set(rooms_map.get(room) or [])
            cells = f'<td class="room-name">{_esc(room)}</td>'
            for dk in door_keys:
                checked = "checked" if dk in assigned else ""
                cells += f'<td style="text-align:center"><input type="checkbox" name="room__{room}__{dk}" {checked} /></td>'
            rows_html += f"<tr>{cells}</tr>\n"

        new_row_cells = '<td><input type="text" name="new_room_name" placeholder="New room name…" style="width:100%;padding:6px;border:1px solid #d1d5db;border-radius:6px;" /></td>'
        for dk in door_keys:
            new_row_cells += f'<td style="text-align:center"><input type="checkbox" name="new_room__{dk}" /></td>'
        new_row = f'<tr style="background:#f0fdf4">{new_row_cells}</tr>'

        door_headers = "".join([f"<th style='text-align:center'>{_esc(str(doors_map[dk].get('label', dk)))}</th>" for dk in door_keys])

        html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Room Mapping — PCO UniFi Sync</title>
  <style>{_SHARED_CSS}
    .room-name {{ font-weight: 500; white-space: nowrap; }}
  </style>
</head>
<body>
  {_nav("settings")}
  <div class="page">
    <div id="toast" class="toast"></div>
    <h2 class="page-heading">Room → Door Mapping</h2>
    <p class="page-subtitle-text">Check which doors unlock when an event is scheduled in each room. Changes take effect on the next sync cycle.</p>

    <form id="mappingForm">
      <div class="card" style="overflow:auto;">
        <span class="card-title">Room Assignments</span>
        <table>
          <thead>
            <tr>
              <th>Room</th>
              {door_headers}
            </tr>
          </thead>
          <tbody>
            {rows_html}
            {new_row}
          </tbody>
        </table>
        <p style="margin:12px 0 0;font-size:13px;color:#64748b;">To remove a room: uncheck all its doors and save.</p>
      </div>

      <div style="display:flex;gap:10px;align-items:center;">
        <button type="submit" class="primary">Save mapping</button>
        <a href="/dashboard" style="font-size:14px;color:#64748b;">Cancel</a>
      </div>
    </form>
  </div>

  <script>
    const DOOR_KEYS = {json.dumps(door_keys)};
    const DOORS_MAP = {json.dumps(doors_map)};

    document.getElementById("mappingForm").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const form = e.target;
      const mapping = await fetch("/api/mapping").then(r => r.json());

      const newRooms = {{}};
      const existingRoomNames = {json.dumps(room_names)};

      for (const room of existingRoomNames) {{
        const assignedDoors = [];
        for (const dk of DOOR_KEYS) {{
          const cb = form.querySelector(`input[name="room__${{room}}__${{dk}}"]`);
          if (cb && cb.checked) assignedDoors.push(dk);
        }}
        if (assignedDoors.length > 0) {{
          newRooms[room] = assignedDoors;
        }}
      }}

      const newName = (form.querySelector('input[name="new_room_name"]').value || "").trim();
      if (newName) {{
        const newDoors = [];
        for (const dk of DOOR_KEYS) {{
          const cb = form.querySelector(`input[name="new_room__${{dk}}"]`);
          if (cb && cb.checked) newDoors.push(dk);
        }}
        if (newDoors.length > 0) {{
          newRooms[newName] = newDoors;
        }}
      }}

      mapping.rooms = newRooms;
      // Preserve existing lead/lag — edited via Settings page.

      try {{
        const resp = await fetch("/api/mapping", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(mapping),
        }});
        if (!resp.ok) throw new Error("Save failed: " + resp.status);
        showToast("Mapping saved! Changes take effect on next sync.", false);
        setTimeout(() => location.reload(), 1200);
      }} catch (err) {{
        showToast("Error: " + err.message, true);
      }}
    }});

    function showToast(msg, isError) {{
      const t = document.getElementById("toast");
      t.textContent = msg;
      t.className = isError ? "toast error" : "toast";
      t.style.display = "block";
      setTimeout(() => {{ t.style.display = "none"; }}, 3000);
    }}
  </script>
</body>
</html>"""
        return HTMLResponse(content=html_out, status_code=200)

    # ── Office Hours API ─────────────────────────────────────────────────

    @app.get("/api/office-hours")
    async def api_office_hours_get() -> dict:
        return load_office_hours(settings.office_hours_file)

    @app.post("/api/office-hours")
    async def api_office_hours_save(payload: dict = Body(...)) -> dict:
        err = validate_office_hours(payload)
        if err:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        save_office_hours(settings.office_hours_file, payload)
        return {"ok": True}

    # ── Office Hours settings page ────────────────────────────────────────

    @app.get("/office-hours", response_class=HTMLResponse)
    async def office_hours_page() -> HTMLResponse:
        from py_app.office_hours import DAYS, parse_time_ranges
        from py_app.mapping import load_room_door_mapping

        oh = load_office_hours(settings.office_hours_file)
        oh_enabled = bool(oh.get("enabled"))
        oh_schedule = oh.get("schedule") or {}

        try:
            mapping = load_room_door_mapping(settings.room_door_mapping_file)
        except Exception:
            mapping = {}
        doors_map = mapping.get("doors") or {}
        door_keys = list(doors_map.keys())

        door_headers = "".join(
            [f'<th style="text-align:center;white-space:nowrap">{_esc(str(doors_map[dk].get("label", dk)))}</th>' for dk in door_keys]
        )

        day_labels = {
            "monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday",
            "thursday": "Thursday", "friday": "Friday", "saturday": "Saturday",
            "sunday": "Sunday",
        }

        rows_html = ""
        for day in DAYS:
            day_cfg = oh_schedule.get(day) or {}
            ranges_val = str(day_cfg.get("ranges") or "")
            assigned_doors = set(day_cfg.get("doors") or [])
            label = day_labels[day]

            door_cells = ""
            for dk in door_keys:
                checked = "checked" if dk in assigned_doors else ""
                door_cells += f'<td style="text-align:center"><input type="checkbox" name="{day}__door__{dk}" {checked} /></td>'

            rows_html += (
                f'<tr>'
                f'<td class="day-name">{label}</td>'
                f'<td><input type="text" name="{day}__ranges" value="{_esc(ranges_val, quote=True)}" '
                f'placeholder="e.g. 9:00-17:00" class="ranges-input" /></td>'
                f'{door_cells}'
                f'</tr>\n'
            )

        enabled_checked = "checked" if oh_enabled else ""

        html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Office Hours — PCO UniFi Sync</title>
  <style>{_SHARED_CSS}
    .day-name {{ font-weight: 600; white-space: nowrap; width: 100px; }}
    .ranges-input {{ padding: 7px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; width: 100%; }}
    .ranges-input:focus {{ outline: none; border-color: #2563eb; box-shadow: 0 0 0 2px #bfdbfe; }}
    .toggle-row {{ display: flex; align-items: center; gap: 10px; }}
    .toggle-label {{ font-size: 15px; font-weight: 600; cursor: pointer; }}
  </style>
</head>
<body>
  {_nav("office-hours")}
  <div class="page">
    <div id="toast" class="toast"></div>
    <h2 class="page-heading">Office Hours</h2>
    <p class="page-subtitle-text">
      Configure recurring weekly door-unlock times. When enabled, these windows are merged with
      Planning Center event schedules so doors stay unlocked during office hours regardless of
      whether an event is scheduled.
    </p>

    <form id="officeHoursForm">
      <div class="card">
        <div class="toggle-row">
          <input type="checkbox" name="enabled" id="enabledToggle" {enabled_checked} />
          <label for="enabledToggle" class="toggle-label">Enable Office Hours</label>
        </div>
        <p style="font-size:13px;color:#64748b;margin:8px 0 0;">
          When unchecked, office hours are ignored during sync (your schedule below is preserved).
        </p>
      </div>

      <div class="card" style="overflow:auto;">
        <span class="card-title">Weekly Schedule</span>
        <p style="font-size:13px;color:#64748b;margin:0 0 12px;">
          Leave the hours field empty for a day to keep doors closed. Multiple ranges: <code>8:00-12:00, 13:00-17:00</code>
        </p>
        <table>
          <thead>
            <tr>
              <th>Day</th>
              <th>Hours <span style="font-weight:normal;color:#9ca3af;font-size:12px">(24h, e.g. 9:00-17:00)</span></th>
              {door_headers}
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
      </div>

      <div style="display:flex;gap:10px;align-items:center;">
        <button type="submit" class="primary">Save office hours</button>
        <a href="/dashboard" style="font-size:14px;color:#64748b;">Cancel</a>
      </div>
    </form>
  </div>

  <script>
    const DAYS = {json.dumps(DAYS)};
    const DOOR_KEYS = {json.dumps(door_keys)};

    document.getElementById("officeHoursForm").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const form = e.target;

      const enabled = form.querySelector('input[name="enabled"]').checked;
      const schedule = {{}};

      for (const day of DAYS) {{
        const rangesInput = form.querySelector(`input[name="${{day}}__ranges"]`);
        const ranges = rangesInput ? rangesInput.value.trim() : "";
        const doors = [];
        for (const dk of DOOR_KEYS) {{
          const cb = form.querySelector(`input[name="${{day}}__door__${{dk}}"]`);
          if (cb && cb.checked) doors.push(dk);
        }}
        schedule[day] = {{ ranges, doors }};
      }}

      const payload = {{ enabled, schedule }};

      try {{
        const resp = await fetch("/api/office-hours", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(payload),
        }});
        const data = await resp.json();
        if (!resp.ok || data.error) throw new Error(data.error || "Save failed: " + resp.status);
        showToast("Office hours saved! Changes take effect on next sync.", false);
        setTimeout(() => location.reload(), 1200);
      }} catch (err) {{
        showToast("Error: " + err.message, true);
      }}
    }});

    function showToast(msg, isError) {{
      const t = document.getElementById("toast");
      t.textContent = msg;
      t.className = isError ? "toast error" : "toast";
      t.style.display = "block";
      setTimeout(() => {{ t.style.display = "none"; }}, 3500);
    }}
  </script>
</body>
</html>"""
        return HTMLResponse(content=html_out, status_code=200)

    # ── Event Overrides API ───────────────────────────────────────────────

    @app.get("/api/event-memory")
    async def api_event_memory_get() -> dict:
        return load_event_memory(settings.event_memory_file)

    @app.get("/api/event-overrides")
    async def api_event_overrides_get() -> dict:
        return load_event_overrides(settings.event_overrides_file)

    @app.post("/api/event-overrides")
    async def api_event_overrides_save(payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        err = validate_event_overrides(payload)
        if err:
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        save_event_overrides(settings.event_overrides_file, payload)
        return {"ok": True}

    # ── Event Overrides page ──────────────────────────────────────────────

    @app.get("/event-overrides", response_class=HTMLResponse)
    async def event_overrides_page() -> HTMLResponse:
        from py_app.mapping import load_room_door_mapping

        local_tz = ZoneInfo(settings.display_timezone)

        def _fmt_date(iso_str: str | None) -> str:
            if not iso_str:
                return ""
            try:
                dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00")).astimezone(local_tz)
                now_y = datetime.now(local_tz).year
                return dt.strftime("%b %d, %Y") if dt.year != now_y else dt.strftime("%b %d")
            except Exception:
                return str(iso_str)

        memory = load_event_memory(settings.event_memory_file)
        mem_events: list[dict] = memory.get("events") or []

        overrides_cfg = load_event_overrides(settings.event_overrides_file)
        overrides_dict: dict = overrides_cfg.get("overrides") or {}

        try:
            mapping = load_room_door_mapping(settings.room_door_mapping_file)
        except Exception:
            mapping = {}
        doors_map: dict = mapping.get("doors") or {}
        door_keys: list[str] = list(doors_map.keys())

        override_keys_lower = {k.lower(): k for k in overrides_dict.keys()}

        rows_html = ""
        for evt in mem_events:
            name = str(evt.get("name") or "")
            building = str(evt.get("building") or "")
            rooms = evt.get("rooms") or []
            last_seen = _fmt_date(evt.get("lastSeenAt"))
            next_occ = _fmt_date(evt.get("nextAt"))
            has_override = name.lower() in override_keys_lower

            if has_override:
                override_cell = (
                    '<span style="color:#059669;font-weight:600;font-size:13px;">&#10003; Configured</span> '
                    f'<button type="button" class="btn-edit" data-event="{_esc(name, quote=True)}">Edit</button>'
                )
            else:
                override_cell = f'<button type="button" class="btn-edit" data-event="{_esc(name, quote=True)}">Set Override</button>'

            last_seen_html = _esc(last_seen) if last_seen else '<span style="color:#9ca3af">—</span>'
            next_occ_html = _esc(next_occ) if next_occ else '<span style="color:#9ca3af">—</span>'

            rows_html += (
                f'<tr class="event-row" data-name="{_esc(name.lower(), quote=True)}">'
                f'<td style="font-weight:500">{_esc(name)}</td>'
                f'<td style="color:#64748b">{_esc(building)}</td>'
                f'<td style="color:#374151">{_esc(", ".join(str(r) for r in rooms))}</td>'
                f'<td style="color:#64748b">{last_seen_html}</td>'
                f'<td style="color:#374151">{next_occ_html}</td>'
                f'<td>{override_cell}</td>'
                f'</tr>\n'
            )

        if not mem_events:
            rows_html = '<tr><td colspan="6" style="padding:16px;color:#9ca3af;text-align:center;">No events recorded yet. Run a sync to populate the list.</td></tr>'

        html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Event Overrides — PCO UniFi Sync</title>
  <style>{_SHARED_CSS}
    .event-row.hidden {{ display: none; }}
    .btn-edit {{ background: #eff6ff; color: #2563eb; border-color: #bfdbfe; font-size: 13px; padding: 4px 10px; border-radius: 6px; }}
    .btn-edit:hover {{ background: #dbeafe; }}
    #editPanel {{
      border: 2px solid #2563eb; border-radius: 12px; padding: 24px;
      margin-top: 4px; background: #eff6ff;
    }}
    #editPanel h3 {{ margin: 0 0 10px; font-size: 16px; color: #1e40af; }}
    .edit-instructions {{ font-size: 13px; color: #374151; margin: 0 0 14px; line-height: 1.6; }}
    .edit-table {{ background: #fff; border-radius: 8px; overflow: hidden; }}
    .edit-table td {{ padding: 8px 10px; border-bottom: 1px solid #dbeafe; vertical-align: middle; }}
    .edit-table th {{ padding: 8px 10px; border-bottom: 2px solid #bfdbfe; font-size: 12px; color: #3b82f6; text-align: left; white-space: nowrap; }}
    .edit-table tbody tr:hover td {{ background: #f0f9ff; }}
    .time-input {{ width: 90px; padding: 6px 8px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; font-family: monospace; text-align: center; }}
    .time-input:focus {{ outline: none; border-color: #2563eb; box-shadow: 0 0 0 2px #bfdbfe; }}
    .time-input:disabled {{ background: #f3f4f6; color: #9ca3af; cursor: not-allowed; border-color: #e5e7eb; }}
    .edit-actions {{ margin-top: 16px; display: flex; gap: 10px; align-items: center; }}
    #search {{ padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 14px; width: 280px; }}
    #search:focus {{ outline: none; border-color: #2563eb; box-shadow: 0 0 0 2px #bfdbfe; }}
    #refInfo {{ font-size: 13px; color: #374151; margin: 0 0 12px; padding: 10px 14px; background: #fff; border-radius: 8px; border: 1px solid #dbeafe; line-height: 1.7; }}
  </style>
</head>
<body>
  {_nav("event-overrides")}
  <div class="page">
    <div id="toast" class="toast"></div>
    <h2 class="page-heading">Event Time Overrides</h2>
    <p class="page-subtitle-text">
      Set exact door open/close times per event name. Times are in <strong>{_esc(settings.display_timezone)}</strong>.
      Overrides replace the global lead/lag times for matched events. Unoverridden doors still use defaults.
    </p>

    <div style="margin-bottom:16px;">
      <input type="text" id="search" placeholder="Search events…" oninput="filterRows(this.value)" />
    </div>

    <div class="card" style="padding:0;overflow:auto;margin-bottom:0;">
      <table>
        <thead>
          <tr>
            <th style="padding-left:16px">Event Name</th>
            <th>Building</th>
            <th>Rooms</th>
            <th>Last Seen</th>
            <th>Next Occurrence</th>
            <th>Override</th>
          </tr>
        </thead>
        <tbody id="eventTableBody">
          {rows_html}
        </tbody>
      </table>
    </div>

    <div id="editPanel" style="display:none;">
      <h3>Editing: <span id="editEventName" style="font-style:italic;"></span></h3>
      <div id="refInfo" style="display:none;"></div>
      <p class="edit-instructions">
        <strong>Checked + times filled</strong> → door opens at those exact times for this event only.<br/>
        <strong>Checked + times blank</strong> → door is <em>suppressed</em> for this event (will not open because of this event; other events that use this door are completely unaffected).<br/>
        <strong>Unchecked</strong> → door uses the global lead/lag default.
      </p>
      <div style="overflow:auto;">
        <table class="edit-table">
          <thead>
            <tr>
              <th>Door</th>
              <th>Default Schedule</th>
              <th style="text-align:center;">Override?</th>
              <th>Window 1 Open</th>
              <th>Window 1 Close</th>
              <th>Window 2 Open <span style="font-weight:normal;color:#93c5fd;font-size:11px">(optional)</span></th>
              <th>Window 2 Close <span style="font-weight:normal;color:#93c5fd;font-size:11px">(optional)</span></th>
            </tr>
          </thead>
          <tbody id="editDoorRows"></tbody>
        </table>
      </div>
      <div class="edit-actions">
        <button type="button" class="primary" onclick="saveOverride()">Save</button>
        <button type="button" class="danger" onclick="removeOverride()">Remove Override</button>
        <button type="button" onclick="closeEdit()">Cancel</button>
      </div>
    </div>

  </div>
  <script>
    const OVERRIDES  = {json.dumps(overrides_dict)};
    const DOOR_KEYS  = {json.dumps(door_keys)};
    const DOORS_MAP  = {json.dumps(doors_map)};
    const MEM_EVENTS = {json.dumps(mem_events)};
    const MAPPING    = {json.dumps(mapping)};
    const DISPLAY_TZ = {json.dumps(settings.display_timezone)};
    let currentEventName = null;

    function filterRows(q) {{
      const lower = q.toLowerCase().trim();
      document.querySelectorAll('#eventTableBody tr.event-row').forEach(row => {{
        const name = row.dataset.name || '';
        row.classList.toggle('hidden', lower !== '' && !name.includes(lower));
      }});
    }}

    function fmtTime(isoStr) {{
      if (!isoStr) return '';
      try {{
        return new Intl.DateTimeFormat('en-US', {{
          timeZone: DISPLAY_TZ, hour: 'numeric', minute: '2-digit', hour12: true,
        }}).format(new Date(isoStr));
      }} catch(e) {{ return isoStr; }}
    }}

    function fmtDateTime(isoStr) {{
      if (!isoStr) return '';
      try {{
        return new Intl.DateTimeFormat('en-US', {{
          timeZone: DISPLAY_TZ, weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
        }}).format(new Date(isoStr));
      }} catch(e) {{ return isoStr; }}
    }}

    function openEdit(eventName) {{
      currentEventName = eventName;
      document.getElementById('editEventName').textContent = eventName;

      const memEvt = MEM_EVENTS.find(e => (e.name || '').toLowerCase() === eventName.toLowerCase()) || {{}};
      const eventRooms = memEvt.rooms || [];

      const roomsMap = (MAPPING && MAPPING.rooms) || {{}};
      const applicableDoors = new Set();
      for (const room of eventRooms) {{
        for (const dk of (roomsMap[room] || [])) applicableDoors.add(dk);
      }}

      const refStart = memEvt.nextAt || memEvt.lastSeenAt || null;
      const refEnd   = memEvt.nextEndAt || memEvt.lastEndAt || null;
      const isNext   = !!memEvt.nextAt;

      const defaults  = (MAPPING && MAPPING.defaults) || {{}};
      const leadMins  = defaults.unlockLeadMinutes || 15;
      const lagMins   = defaults.unlockLagMinutes  || 15;

      let normalOpenISO = null, normalCloseISO = null;
      if (refStart) normalOpenISO  = new Date(new Date(refStart).getTime() - leadMins * 60000).toISOString();
      if (refEnd)   normalCloseISO = new Date(new Date(refEnd).getTime()   + lagMins  * 60000).toISOString();

      const refEl = document.getElementById('refInfo');
      if (refStart) {{
        const dateFmt   = fmtDateTime(refStart);
        const occLabel  = isNext ? 'Next occurrence' : 'Last seen (no upcoming)';
        const evtStart  = fmtTime(refStart);
        const evtEnd    = refEnd ? fmtTime(refEnd) : null;
        let info = `<strong>${{occLabel}}:</strong> ${{dateFmt}}`;
        info += evtEnd
          ? ` &nbsp;·&nbsp; <strong>Event:</strong> ${{evtStart}} – ${{evtEnd}}`
          : ` &nbsp;·&nbsp; <strong>Starts:</strong> ${{evtStart}}`;
        if (normalOpenISO && normalCloseISO) {{
          info += ` &nbsp;·&nbsp; <strong>Default doors:</strong> ${{fmtTime(normalOpenISO)}} – ${{fmtTime(normalCloseISO)}} <span style="color:#9ca3af;font-size:12px;">(${{leadMins}} min before / ${{lagMins}} min after)</span>`;
        }}
        refEl.innerHTML = info;
        refEl.style.display = '';
      }} else {{
        refEl.style.display = 'none';
      }}

      const nameLower  = eventName.toLowerCase();
      const overrideKey = Object.keys(OVERRIDES).find(k => k.toLowerCase() === nameLower);
      const eventCfg   = overrideKey ? (OVERRIDES[overrideKey] || {{}}) : {{}};
      const doorOverrides = eventCfg.doorOverrides || {{}};

      function buildDoorRow(dk, isApplicable) {{
        let defaultCell = '';
        if (isApplicable && normalOpenISO && normalCloseISO) {{
          defaultCell = `<span style="color:#059669;font-size:12px;">Opens</span> <strong>${{fmtTime(normalOpenISO)}}</strong><br/><span style="color:#dc2626;font-size:12px;">Closes</span> <strong>${{fmtTime(normalCloseISO)}}</strong>`;
        }} else if (isApplicable && normalOpenISO) {{
          defaultCell = `<span style="color:#059669;font-size:12px;">Opens</span> <strong>${{fmtTime(normalOpenISO)}}</strong><br/><span style="color:#9ca3af;font-size:12px;">Close: sync needed</span>`;
        }} else if (isApplicable) {{
          defaultCell = `<span style="color:#9ca3af;font-size:12px;">Times unknown</span>`;
        }} else {{
          defaultCell = `<span style="color:#d1d5db;font-size:12px;">Not in this event</span>`;
        }}
        const doorCfg   = doorOverrides.hasOwnProperty(dk) ? doorOverrides[dk] : null;
        const isInOverride = doorCfg !== null;
        const wins = isInOverride ? ((doorCfg && doorCfg.windows) || []) : [];
        const w1   = wins[0] || {{}};
        const w2   = wins[1] || {{}};
        const label = (DOORS_MAP[dk] && DOORS_MAP[dk].label) ? DOORS_MAP[dk].label : dk;
        const chk  = isInOverride ? 'checked' : '';
        const dis  = isInOverride ? '' : 'disabled';
        const rowStyle = isApplicable ? '' : 'opacity:0.4;';
        return `<tr style="${{rowStyle}}">
          <td><strong>${{label}}</strong><br/><span style="font-size:12px;color:#6b7280;">${{dk}}</span></td>
          <td>${{defaultCell}}</td>
          <td style="text-align:center;"><input type="checkbox" id="chk_${{dk}}" ${{chk}} onchange="toggleDoor('${{dk}}')" style="width:18px;height:18px;cursor:pointer;accent-color:#2563eb;" /></td>
          <td><input type="text" id="open1_${{dk}}" value="${{w1.openTime||''}}" placeholder="HH:MM" class="time-input" ${{dis}} /></td>
          <td><input type="text" id="close1_${{dk}}" value="${{w1.closeTime||''}}" placeholder="HH:MM" class="time-input" ${{dis}} /></td>
          <td><input type="text" id="open2_${{dk}}" value="${{w2.openTime||''}}" placeholder="HH:MM" class="time-input" ${{dis}} /></td>
          <td><input type="text" id="close2_${{dk}}" value="${{w2.closeTime||''}}" placeholder="HH:MM" class="time-input" ${{dis}} /></td>
        </tr>`;
      }}

      const applicableKeys = DOOR_KEYS.filter(dk => applicableDoors.has(dk));
      const otherKeys      = DOOR_KEYS.filter(dk => !applicableDoors.has(dk));

      let rows = applicableKeys.map(dk => buildDoorRow(dk, true)).join('');
      if (otherKeys.length > 0) {{
        rows += `<tr><td colspan="7" style="padding:6px 10px;font-size:11px;color:#9ca3af;background:#f9fafb;border-top:2px solid #e5e7eb;letter-spacing:0.05em;text-transform:uppercase;">Other doors — not part of this event's rooms</td></tr>`;
        rows += otherKeys.map(dk => buildDoorRow(dk, false)).join('');
      }}

      document.getElementById('editDoorRows').innerHTML = rows;
      const panel = document.getElementById('editPanel');
      panel.style.display = 'block';
      panel.scrollIntoView({{behavior: 'smooth', block: 'nearest'}});
    }}

    function toggleDoor(dk) {{
      const chk = document.getElementById('chk_' + dk);
      ['open1_','close1_','open2_','close2_'].forEach(pfx => {{
        const el = document.getElementById(pfx + dk);
        if (el) el.disabled = !chk.checked;
      }});
      if (chk.checked) {{
        const first = document.getElementById('open1_' + dk);
        if (first && !first.value) first.focus();
      }}
    }}

    function closeEdit() {{
      currentEventName = null;
      document.getElementById('editPanel').style.display = 'none';
    }}

    async function saveOverride() {{
      if (!currentEventName) return;

      const doorOverrides = {{}};
      const timeRe = /^\\d{{1,2}}:\\d{{2}}$/;
      for (const dk of DOOR_KEYS) {{
        const chk = document.getElementById('chk_' + dk);
        if (!chk || !chk.checked) continue;
        const open1  = (document.getElementById('open1_'  + dk).value || '').trim();
        const close1 = (document.getElementById('close1_' + dk).value || '').trim();
        const open2  = (document.getElementById('open2_'  + dk).value || '').trim();
        const close2 = (document.getElementById('close2_' + dk).value || '').trim();
        const label  = (DOORS_MAP[dk] && DOORS_MAP[dk].label) ? DOORS_MAP[dk].label : dk;

        if (!open1 && !close1 && !open2 && !close2) {{
          doorOverrides[dk] = {{ windows: [] }};
          continue;
        }}
        if (!timeRe.test(open1) || !timeRe.test(close1)) {{
          showToast('Invalid Window 1 time for ' + label + '. Use HH:MM (24h), or leave all blank to suppress.', true);
          return;
        }}
        const windows = [{{ openTime: open1, closeTime: close1 }}];
        if (open2 || close2) {{
          if (!timeRe.test(open2) || !timeRe.test(close2)) {{
            showToast('Invalid Window 2 time for ' + label + '. Use HH:MM or leave both blank.', true);
            return;
          }}
          windows.push({{ openTime: open2, closeTime: close2 }});
        }}
        doorOverrides[dk] = {{ windows }};
      }}

      if (Object.keys(doorOverrides).length === 0) {{
        showToast('Check at least one door to override, or use Remove Override to clear.', true);
        return;
      }}

      const newOverrides  = Object.assign({{}}, OVERRIDES);
      const nameLower     = currentEventName.toLowerCase();
      const existingKey   = Object.keys(newOverrides).find(k => k.toLowerCase() === nameLower);
      const useKey        = existingKey || currentEventName;
      newOverrides[useKey] = {{ doorOverrides }};

      await postOverrides(newOverrides, 'Override saved.');
    }}

    async function removeOverride() {{
      if (!currentEventName) return;
      const newOverrides = Object.assign({{}}, OVERRIDES);
      const nameLower    = currentEventName.toLowerCase();
      const existingKey  = Object.keys(newOverrides).find(k => k.toLowerCase() === nameLower);
      if (existingKey) delete newOverrides[existingKey];
      await postOverrides(newOverrides, 'Override removed.');
    }}

    async function postOverrides(overrides, successMsg) {{
      try {{
        const resp = await fetch('/api/event-overrides', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ overrides }}),
        }});
        const data = await resp.json();
        if (!resp.ok || data.error) throw new Error(data.error || 'Save failed: ' + resp.status);
        showToast(successMsg, false);
        setTimeout(() => location.reload(), 1200);
      }} catch (err) {{
        showToast('Error: ' + err.message, true);
      }}
    }}

    function showToast(msg, isError) {{
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.className = isError ? 'toast error' : 'toast';
      t.style.display = 'block';
      setTimeout(() => {{ t.style.display = 'none'; }}, 3500);
    }}

    document.querySelectorAll('.btn-edit').forEach(btn => {{
      btn.addEventListener('click', () => openEdit(btn.dataset.event));
    }});
  </script>
</body>
</html>"""
        return HTMLResponse(content=html_out, status_code=200)

    # ── General Settings API ─────────────────────────────────────────────────

    @app.get("/api/general-settings")
    async def api_general_settings_get() -> dict:
        mapping = _read_mapping()
        defaults = mapping.get("defaults") or {}
        safe_hours = load_safe_hours(settings.safe_hours_file)
        return {
            "unlockLeadMinutes": int(defaults.get("unlockLeadMinutes") or 15),
            "unlockLagMinutes": int(defaults.get("unlockLagMinutes") or 15),
            "safeStartMonday":    safe_hours.get("safeStartMonday")    or "05:00",
            "safeStartTuesday":   safe_hours.get("safeStartTuesday")   or "05:00",
            "safeStartWednesday": safe_hours.get("safeStartWednesday") or "05:00",
            "safeStartThursday":  safe_hours.get("safeStartThursday")  or "05:00",
            "safeStartFriday":    safe_hours.get("safeStartFriday")    or "05:00",
            "safeStartSaturday":  safe_hours.get("safeStartSaturday")  or "05:00",
            "safeStartSunday":    safe_hours.get("safeStartSunday")    or "05:00",
            "safeEndMonday":    safe_hours.get("safeEndMonday")    or "23:00",
            "safeEndTuesday":   safe_hours.get("safeEndTuesday")   or "23:00",
            "safeEndWednesday": safe_hours.get("safeEndWednesday") or "23:00",
            "safeEndThursday":  safe_hours.get("safeEndThursday")  or "23:00",
            "safeEndFriday":    safe_hours.get("safeEndFriday")    or "23:30",
            "safeEndSaturday":  safe_hours.get("safeEndSaturday")  or "23:00",
            "safeEndSunday":    safe_hours.get("safeEndSunday")    or "23:00",
            "syncCron": settings.sync_cron or "",
            "lookaheadHours": int(settings.sync_lookahead_hours),
            "timezone": settings.display_timezone,
            "doorStatusRefreshSeconds": int(settings.door_status_refresh_seconds),
            "telegramConfigured": bool(settings.telegram_bot_token and settings.telegram_chat_ids),
        }

    @app.post("/api/general-settings")
    async def api_general_settings_save(payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        # Validate and save lead/lag into mapping file.
        try:
            lead = int(payload.get("unlockLeadMinutes") or 15)
            lag = int(payload.get("unlockLagMinutes") or 15)
        except (ValueError, TypeError):
            return JSONResponse(status_code=422, content={"ok": False, "error": "Lead/lag must be integers"})
        if not (0 <= lead <= 120) or not (0 <= lag <= 120):
            return JSONResponse(status_code=422, content={"ok": False, "error": "Lead/lag must be 0–120 minutes"})

        mapping = _read_mapping()
        mapping.setdefault("defaults", {})["unlockLeadMinutes"] = lead
        mapping["defaults"]["unlockLagMinutes"] = lag
        _write_mapping(mapping)

        # Validate and save safe hours (per-day start + end).
        import re as _re
        _time_pat = _re.compile(r"^\d{1,2}:\d{2}$")
        _days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        _end_defaults = {"Monday":"23:00","Tuesday":"23:00","Wednesday":"23:00",
                         "Thursday":"23:00","Friday":"23:30","Saturday":"23:00","Sunday":"23:00"}
        per_day: dict[str, str] = {}
        for day in _days:
            start_key = f"safeStart{day}"
            end_key   = f"safeEnd{day}"
            sv = str(payload.get(start_key) or "05:00").strip()
            ev = str(payload.get(end_key) or _end_defaults[day]).strip()
            if not _time_pat.match(sv):
                return JSONResponse(status_code=422, content={"ok": False, "error": f"{day} start must be HH:MM format"})
            if not _time_pat.match(ev):
                return JSONResponse(status_code=422, content={"ok": False, "error": f"{day} end must be HH:MM format"})
            per_day[start_key] = sv
            per_day[end_key]   = ev

        save_safe_hours(settings.safe_hours_file, per_day)
        return {"ok": True}

    # ── System settings helpers ──────────────────────────────────────────────

    _env_path = Path(__file__).resolve().parent.parent / ".env"

    def _write_env_vars(updates: dict[str, str]) -> None:
        """Update specific KEY=VALUE lines in .env, preserving everything else."""
        lines = _env_path.read_text(encoding="utf-8").splitlines() if _env_path.exists() else []
        updated: set[str] = set()
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in updates:
                    new_lines.append(f"{key}={updates[key]}")
                    updated.add(key)
                    continue
            new_lines.append(line)
        for key, val in updates.items():
            if key not in updated:
                new_lines.append(f"{key}={val}")
        _env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    @app.post("/api/system-settings")
    async def api_system_settings_save(payload: dict = Body(...)) -> dict:
        import asyncio, re as _re
        from fastapi.responses import JSONResponse

        cron = str(payload.get("syncCron") or "").strip()
        if cron and not _re.match(r'^[\d\*,\-/]+ [\d\*,\-/]+ [\d\*,\-/]+ [\d\*,\-/]+ [\d\*,\-/]+$', cron):
            return JSONResponse(status_code=422, content={"ok": False, "error": "Invalid cron expression"})
        try:
            lookahead = int(payload.get("lookaheadHours") or 168)
        except (ValueError, TypeError):
            return JSONResponse(status_code=422, content={"ok": False, "error": "Lookahead must be an integer"})
        try:
            door_refresh = max(10, int(payload.get("doorStatusRefreshSeconds") or 30))
        except (ValueError, TypeError):
            return JSONResponse(status_code=422, content={"ok": False, "error": "Door status refresh must be an integer"})

        updates: dict[str, str] = {}
        if cron:
            updates["SYNC_CRON"] = cron
        if lookahead:
            updates["SYNC_LOOKAHEAD_HOURS"] = str(lookahead)
        updates["DOOR_STATUS_REFRESH_SECONDS"] = str(door_refresh)
        tz = str(payload.get("timezone") or "").strip()
        if tz:
            updates["DISPLAY_TIMEZONE"] = tz
        token = str(payload.get("telegramBotToken") or "").strip()
        if token:
            updates["TELEGRAM_BOT_TOKEN"] = token
        chat_ids = str(payload.get("telegramChatIds") or "").strip()
        updates["TELEGRAM_CHAT_IDS"] = chat_ids

        if updates:
            _write_env_vars(updates)

        # Restart the service after sending the response.
        async def _restart():
            await asyncio.sleep(1.5)
            proc = await asyncio.create_subprocess_exec("systemctl", "restart", "pco-unifi-sync")
            await proc.wait()

        asyncio.create_task(_restart())
        return {"ok": True, "restarting": True}

    # ── General Settings page ────────────────────────────────────────────────

    @app.get("/general-settings", response_class=HTMLResponse)
    async def general_settings_page() -> HTMLResponse:
        mapping = _read_mapping()
        defaults = mapping.get("defaults") or {}
        lead = int(defaults.get("unlockLeadMinutes") or 15)
        lag = int(defaults.get("unlockLagMinutes") or 15)
        safe_hours = load_safe_hours(settings.safe_hours_file)
        def _sh(key: str, dflt: str) -> str:
            return safe_hours.get(key) or dflt
        ss_mon = _sh("safeStartMonday",    "05:00"); se_mon = _sh("safeEndMonday",    "23:00")
        ss_tue = _sh("safeStartTuesday",   "05:00"); se_tue = _sh("safeEndTuesday",   "23:00")
        ss_wed = _sh("safeStartWednesday", "05:00"); se_wed = _sh("safeEndWednesday", "23:00")
        ss_thu = _sh("safeStartThursday",  "05:00"); se_thu = _sh("safeEndThursday",  "23:00")
        ss_fri = _sh("safeStartFriday",    "05:00"); se_fri = _sh("safeEndFriday",    "23:30")
        ss_sat = _sh("safeStartSaturday",  "05:00"); se_sat = _sh("safeEndSaturday",  "23:00")
        ss_sun = _sh("safeStartSunday",    "05:00"); se_sun = _sh("safeEndSunday",    "23:00")
        telegram_ok = bool(settings.telegram_bot_token and settings.telegram_chat_ids)
        token_placeholder = "••••••••  (leave blank to keep current)" if settings.telegram_bot_token else ""

        html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Settings — PCO UniFi Sync</title>
  <style>{_SHARED_CSS}
    .field-row {{ display: flex; align-items: center; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }}
    .field-row label {{ font-size: 14px; color: #374151; min-width: 220px; }}
    .field-row input {{ width: 220px; }}
    .field-row input[type="number"] {{ width: 110px; }}
    .field-row input[type="time"] {{ width: 130px; }}
    .day-grid {{ display: grid; grid-template-columns: 110px 1fr 1fr; gap: 8px 14px; align-items: center; margin-bottom: 8px; max-width: 440px; }}
    .day-grid .col-head {{ font-size: 11px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: .04em; }}
    .day-grid label {{ font-size: 14px; color: #374151; }}
    .day-grid input[type="time"] {{ width: 100%; min-width: 0; }}
    .field-row select {{ width: 260px; }}
    .field-hint {{ font-size: 12px; color: #94a3b8; }}
    .field-note {{ font-size: 12px; color: #64748b; margin: -8px 0 14px 0; padding-left: 4px; line-height: 1.5; }}
    .field-note a {{ color: #2563eb; }}
    .section-note {{ font-size: 13px; color: #92400e; background: #fffbeb; border: 1px solid #fcd34d;
      border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; }}
    @media (max-width: 640px) {{
      .field-row {{ flex-direction: column; align-items: flex-start; gap: 6px; margin-bottom: 10px; }}
      .field-row label {{ min-width: 0; width: 100%; font-weight: 500; }}
      .field-row input, .field-row select {{ width: 100% !important; max-width: 100%; box-sizing: border-box; }}
      .field-hint {{ margin-top: 2px; }}
      .day-grid {{ grid-template-columns: 90px 1fr 1fr; max-width: 100%; gap: 6px 8px; }}
    }}
  </style>
</head>
<body>
  {_nav("general-settings")}
  <div class="page">
    <div id="toast" class="toast"></div>
    <h2 class="page-heading">Settings</h2>
    <p class="page-subtitle-text">Adjust timing, after-hours policy, sync schedule, and notifications.</p>

    <!-- Form 1: door timing + safe hours (instant save, no restart) -->
    <form id="timingForm">
      <div class="card">
        <span class="card-title">Door Timing</span>
        <p style="font-size:13px;color:#64748b;margin:0 0 16px;">
          How many minutes before and after each event the doors unlock. These are global defaults —
          use <a href="/event-overrides">Event Overrides</a> to set exact clock times for specific events.
        </p>
        <div class="field-row">
          <label>Unlock lead time</label>
          <input type="number" name="unlockLeadMinutes" value="{lead}" min="0" max="120" />
          <span class="field-hint">minutes before event start</span>
        </div>
        <p class="field-note">
          Doors open this many minutes before the scheduled start time. 15 minutes lets attendees
          arrive before the event begins. Set to 0 to open exactly at the start time.
        </p>
        <div class="field-row">
          <label>Unlock lag time</label>
          <input type="number" name="unlockLagMinutes" value="{lag}" min="0" max="120" />
          <span class="field-hint">minutes after event end</span>
        </div>
        <p class="field-note">
          Doors stay unlocked this many minutes after the scheduled end time. Gives attendees time
          to clear out. Set to 0 to lock immediately when the event ends.
        </p>
      </div>

      <div class="card">
        <span class="card-title">After-Hours Approval Policy</span>
        <p style="font-size:13px;color:#64748b;margin:0 0 14px;">
          Any event whose door window (startAt − lead through endAt + lag) falls outside a day's
          safe hours is held for manual approval before being applied to UniFi.
          Use the <a href="/dashboard">Dashboard</a> to approve or deny flagged events.
          Approving an event by name auto-approves all future occurrences of that event.
        </p>
        <div class="day-grid">
          <span class="col-head">Day</span>
          <span class="col-head">Opens from</span>
          <span class="col-head">Closes by</span>
          <label>Monday</label>
          <input type="time" name="safeStartMonday"    value="{ss_mon}" />
          <input type="time" name="safeEndMonday"      value="{se_mon}" />
          <label>Tuesday</label>
          <input type="time" name="safeStartTuesday"   value="{ss_tue}" />
          <input type="time" name="safeEndTuesday"     value="{se_tue}" />
          <label>Wednesday</label>
          <input type="time" name="safeStartWednesday" value="{ss_wed}" />
          <input type="time" name="safeEndWednesday"   value="{se_wed}" />
          <label>Thursday</label>
          <input type="time" name="safeStartThursday"  value="{ss_thu}" />
          <input type="time" name="safeEndThursday"    value="{se_thu}" />
          <label>Friday</label>
          <input type="time" name="safeStartFriday"    value="{ss_fri}" />
          <input type="time" name="safeEndFriday"      value="{se_fri}" />
          <label>Saturday</label>
          <input type="time" name="safeStartSaturday"  value="{ss_sat}" />
          <input type="time" name="safeEndSaturday"    value="{se_sat}" />
          <label>Sunday</label>
          <input type="time" name="safeStartSunday"    value="{ss_sun}" />
          <input type="time" name="safeEndSunday"      value="{se_sun}" />
        </div>
        <p class="field-note" style="margin-top:10px;">
          "Opens from" — earliest time doors may unlock (accounting for lead time).
          "Closes by" — latest time doors may remain unlocked (accounting for lag time).
          Events outside either boundary will be flagged for approval.
        </p>
      </div>

      <div style="display:flex;gap:10px;align-items:center;margin-bottom:32px;">
        <button type="submit" class="primary">Save</button>
      </div>
    </form>

    <!-- Form 2: system + telegram (writes .env, restarts service) -->
    <form id="systemForm">
      <div class="card">
        <span class="card-title">Sync Schedule</span>
        <p style="font-size:13px;color:#64748b;margin:0 0 16px;">
          Controls how often the service polls Planning Center for new or changed events and
          pushes updates to UniFi. Changes here require a service restart (click Save &amp; Restart below).
        </p>
        <div class="field-row">
          <label>Sync interval</label>
          <select name="syncCron">
            <option value="*/2 * * * *"  {"selected" if settings.sync_cron == "*/2 * * * *"  else ""}>Every 2 minutes</option>
            <option value="*/5 * * * *"  {"selected" if settings.sync_cron == "*/5 * * * *"  else ""}>Every 5 minutes (default)</option>
            <option value="*/7 * * * *"  {"selected" if settings.sync_cron == "*/7 * * * *"  else ""}>Every 7 minutes</option>
            <option value="*/10 * * * *" {"selected" if settings.sync_cron == "*/10 * * * *" else ""}>Every 10 minutes</option>
            <option value="*/15 * * * *" {"selected" if settings.sync_cron == "*/15 * * * *" else ""}>Every 15 minutes</option>
            <option value="*/20 * * * *" {"selected" if settings.sync_cron == "*/20 * * * *" else ""}>Every 20 minutes</option>
            <option value="*/30 * * * *" {"selected" if settings.sync_cron == "*/30 * * * *" else ""}>Every 30 minutes</option>
            <option value="*/45 * * * *" {"selected" if settings.sync_cron == "*/45 * * * *" else ""}>Every 45 minutes</option>
            <option value="0 * * * *"    {"selected" if settings.sync_cron == "0 * * * *"    else ""}>Every 60 minutes</option>
          </select>
        </div>
        <p class="field-note">
          How often the service checks Planning Center for new or changed events and pushes updates
          to UniFi. 5 minutes is a good default — reducing this increases PCO API usage.
          Changes take effect after Save &amp; Restart.
        </p>
        <div class="field-row">
          <label>Lookahead window</label>
          <input type="number" name="lookaheadHours" value="{settings.sync_lookahead_hours}" min="1" max="720" />
          <span class="field-hint">hours ahead to fetch events</span>
        </div>
        <p class="field-note">
          How far into the future to pull events from PCO on each sync. 168 hours = 7 days (default).
          The UniFi door schedule is rebuilt across the full window every sync cycle — increasing this
          means events further out are included in UniFi's weekly schedule sooner.
          Maximum recommended: 720 (30 days).
        </p>
        <div class="field-row">
          <label>Door status refresh interval</label>
          <input type="number" name="doorStatusRefreshSeconds" value="{settings.door_status_refresh_seconds}" min="10" max="3600" />
          <span class="field-hint">seconds</span>
        </div>
        <p class="field-note">
          How often the Dashboard polls UniFi for live door lock status. 30 seconds is a good default.
          Set higher (e.g. 120) to reduce network traffic, or lower (minimum 10) for near-real-time updates.
          Set to 0 to disable automatic polling (you can still refresh manually).
        </p>
        <div class="field-row">
          <label>Display timezone</label>
          <input type="text" name="timezone" value="{_esc(settings.display_timezone)}" style="width:220px" />
          <span class="field-hint">IANA timezone name</span>
        </div>
        <p class="field-note">
          Used for two things: (1) all times shown on the dashboard and event-overrides pages,
          and (2) converting UTC event windows into the local HH:MM times that UniFi's weekly
          schedule uses. Must match your campus timezone.
          Examples: <code>America/New_York</code>, <code>America/Chicago</code>, <code>America/Los_Angeles</code>,
          <code>America/Denver</code>. See the
          <a href="https://en.wikipedia.org/wiki/List_of_tz_database_time_zones" target="_blank" rel="noopener">IANA timezone list</a>
          for all valid values.
        </p>
      </div>

      <div class="card">
        <span class="card-title">Telegram Notifications</span>
        <p style="font-size:13px;color:#64748b;margin:0 0 16px;">
          When a new event is flagged for after-hours approval, Telegram sends an instant message
          to everyone listed in Chat IDs. Requires a bot created via
          <a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a> on Telegram.
          Status: <span class="dot {"ok" if telegram_ok else "err"}"></span>{"Configured" if telegram_ok else "Not configured"}.
        </p>
        <div class="field-row">
          <label>Bot token</label>
          <input type="password" name="telegramBotToken" placeholder="{_esc(token_placeholder)}" autocomplete="new-password" style="width:300px" />
        </div>
        <p class="field-note">
          The API token you received from @BotFather when you created the bot
          (format: <code>123456789:ABCdef…</code>).
          Leave this field blank to keep the current saved token unchanged.
          To set up a bot: open Telegram → message <code>@BotFather</code> → send <code>/newbot</code>
          → follow the prompts → copy the token shown.
        </p>
        <div class="field-row">
          <label>Chat IDs</label>
          <input type="text" name="telegramChatIds" value="{_esc(settings.telegram_chat_ids)}" placeholder="123456789,987654321" style="width:300px" />
          <span class="field-hint">comma-separated, one per person/group</span>
        </div>
        <p class="field-note">
          Numeric Telegram user or group chat IDs to notify. To find your personal chat ID:
          message your bot, then open
          <code>https://api.telegram.org/bot&lt;TOKEN&gt;/getUpdates</code>
          in a browser and look for <code>"id"</code> inside the <code>"chat"</code> object.
          Multiple recipients: separate IDs with commas (no spaces).
        </p>
      </div>

      <div style="display:flex;gap:10px;align-items:center;margin-bottom:24px;">
        <button type="submit" id="sysBtn" class="primary">Save &amp; Restart</button>
        <span id="sysNote" style="font-size:13px;color:#92400e;display:none;">Service restarting — page will reload in a few seconds…</span>
      </div>
    </form>

  </div>
  <script>
    document.getElementById("timingForm").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const form = e.target;
      const toast = document.getElementById("toast");
      const payload = {{
        unlockLeadMinutes:    parseInt(form.unlockLeadMinutes.value) || 15,
        unlockLagMinutes:     parseInt(form.unlockLagMinutes.value) || 15,
        safeStartMonday:      form.safeStartMonday.value,
        safeEndMonday:        form.safeEndMonday.value,
        safeStartTuesday:     form.safeStartTuesday.value,
        safeEndTuesday:       form.safeEndTuesday.value,
        safeStartWednesday:   form.safeStartWednesday.value,
        safeEndWednesday:     form.safeEndWednesday.value,
        safeStartThursday:    form.safeStartThursday.value,
        safeEndThursday:      form.safeEndThursday.value,
        safeStartFriday:      form.safeStartFriday.value,
        safeEndFriday:        form.safeEndFriday.value,
        safeStartSaturday:    form.safeStartSaturday.value,
        safeEndSaturday:      form.safeEndSaturday.value,
        safeStartSunday:      form.safeStartSunday.value,
        safeEndSunday:        form.safeEndSunday.value,
      }};
      try {{
        const resp = await fetch("/api/general-settings", {{
          method: "POST", headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify(payload),
        }});
        const data = await resp.json();
        if (!resp.ok || !data.ok) throw new Error(data.error || "HTTP " + resp.status);
        toast.textContent = "Saved.";
        toast.style.background = "#059669";
        toast.style.display = "block";
        setTimeout(() => {{ toast.style.display = "none"; }}, 2500);
      }} catch (err) {{
        toast.textContent = "Save failed: " + err.message;
        toast.style.background = "#dc2626";
        toast.style.display = "block";
        setTimeout(() => {{ toast.style.display = "none"; }}, 4000);
      }}
    }});

    document.getElementById("systemForm").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const form = e.target;
      const btn = document.getElementById("sysBtn");
      const note = document.getElementById("sysNote");
      btn.disabled = true;
      const payload = {{
        syncCron:                   form.syncCron.value.trim(),
        lookaheadHours:             parseInt(form.lookaheadHours.value) || 168,
        timezone:                   form.timezone.value.trim(),
        doorStatusRefreshSeconds:   parseInt(form.doorStatusRefreshSeconds.value) || 30,
        telegramBotToken:           form.telegramBotToken.value,
        telegramChatIds:            form.telegramChatIds.value.trim(),
      }};
      try {{
        const resp = await fetch("/api/system-settings", {{
          method: "POST", headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify(payload),
        }});
        const data = await resp.json();
        if (!resp.ok || !data.ok) throw new Error(data.error || "HTTP " + resp.status);
        note.style.display = "inline";
        // Poll until the service is back up, then reload.
        const poll = setInterval(async () => {{
          try {{
            await fetch("/health");
            clearInterval(poll);
            location.reload();
          }} catch (_) {{}}
        }}, 1500);
      }} catch (err) {{
        const toast = document.getElementById("toast");
        toast.textContent = "Save failed: " + err.message;
        toast.style.background = "#dc2626";
        toast.style.display = "block";
        setTimeout(() => {{ toast.style.display = "none"; }}, 4000);
        btn.disabled = false;
      }}
    }});
  </script>
</body>
</html>"""
        return HTMLResponse(content=html_out, status_code=200)

    return app


app = create_app()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
