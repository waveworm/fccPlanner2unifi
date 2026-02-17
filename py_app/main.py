from __future__ import annotations

from datetime import datetime, timezone
from datetime import timedelta
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from py_app.logger import get_logger
from py_app.settings import Settings
from py_app.sync_service import SyncService
from py_app.vendors.unifi_access import UnifiAccessClient
from py_app.vendors.pco import PcoClient


def create_app() -> FastAPI:
    settings = Settings()
    logger = get_logger()

    app = FastAPI(title="PCO → UniFi Access Sync")
    sync_service = SyncService(settings, logger)
    unifi_client = UnifiAccessClient(settings)
    pco_client = PcoClient(settings)

    scheduler = AsyncIOScheduler(timezone="UTC")

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

    @app.get("/api/pco/calendars")
    async def api_pco_calendars() -> dict:
        data = await pco_client.list_calendars(per_page=200)
        # Return a compact view for UI discovery.
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

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        status = sync_service.snapshot()
        last_sync_at = status.get("lastSyncAt") or "(never)"
        last_sync_result = status.get("lastSyncResult") or "(none)"
        pco_status = status.get("pcoStatus")
        unifi_status = status.get("unifiStatus")
        recent_errors = "\n".join(status.get("recentErrors") or []) or "(none)"
        pco_stats = status.get("pcoStats") or {}

        preview = await sync_service.get_upcoming_preview(limit=50)
        preview_events = preview.get("events") or []
        preview_rooms = preview.get("rooms") or {}
        preview_items = (preview.get("schedule") or {}).get("items") or []
        preview_windows = (preview.get("schedule") or {}).get("doorWindows") or []

        # Build event -> door groups for dashboard visibility.
        event_doors: dict[str, list[str]] = {}
        for it in preview_items:
            event_id = str(it.get("sourceEventId") or "").strip()
            if not event_id:
                continue
            door_key = str(it.get("doorKey") or "").strip()
            door_label = str(it.get("doorLabel") or door_key).strip()
            if not door_key:
                continue
            entry = f"{door_label} ({door_key})"
            existing = event_doors.setdefault(event_id, [])
            if entry not in existing:
                existing.append(entry)

        mapping = None
        try:
            # Lazy load the mapping file for dashboard display.
            from py_app.mapping import load_room_door_mapping

            mapping = load_room_door_mapping(settings.room_door_mapping_file)
        except Exception:
            mapping = None

        mapping_rows = []
        if isinstance(mapping, dict):
            rooms_map = mapping.get("rooms") or {}
            doors_map = mapping.get("doors") or {}

            for room_name in sorted(list(rooms_map.keys())):
                door_keys = rooms_map.get(room_name) or []
                door_labels = []
                door_ids = []
                for dk in door_keys:
                    d = doors_map.get(dk) or {}
                    door_labels.append(str(d.get("label") or dk))
                    ids = d.get("unifiDoorIds") or []
                    door_ids.append(",".join([str(x) for x in ids]) if ids else "")

                mapping_rows.append(
                    "<tr>"
                    f"<td>{room_name}</td>"
                    f"<td>{', '.join(door_labels) if door_labels else ''}</td>"
                    f"<td>{' | '.join([x for x in door_ids if x])}</td>"
                    "</tr>"
                )

        mapping_table_rows = "\n".join(mapping_rows)

        apply_to_unifi = bool(status.get("applyToUnifi"))
        pco_stats_text = "\n".join([f"{k}: {v}" for k, v in sorted(pco_stats.items(), key=lambda kv: kv[0])]) or "(none)"

        rooms_lines = "\n".join([f"{k}: {v}" for k, v in sorted(preview_rooms.items(), key=lambda kv: kv[0])]) or "(none)"

        eastern = ZoneInfo("America/New_York")

        def _fmt_local(iso_str: str | None) -> str:
            if not iso_str:
                return ""
            try:
                dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00")).astimezone(eastern)
                return dt.strftime("%Y-%m-%d %I:%M %p %Z")
            except Exception:
                return str(iso_str)

        events_rows = "\n".join(
            [
                "<tr>"
                f"<td>{_fmt_local(e.get('startAt'))}<br/><span style='color:#6b7280;font-size:12px'>{(e.get('startAt') or '')}</span></td>"
                f"<td>{_fmt_local(e.get('endAt'))}<br/><span style='color:#6b7280;font-size:12px'>{(e.get('endAt') or '')}</span></td>"
                f"<td>{(e.get('name') or '')}</td>"
                f"<td>{(e.get('building') or '')}</td>"
                f"<td>{(e.get('room') or '')}</td>"
                f"<td>{', '.join(event_doors.get(str(e.get('id') or ''), [])) or '(none mapped)'}</td>"
                "</tr>"
                for e in preview_events
            ]
        )

        html = f"""<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>PCO → UniFi Access Sync</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; margin: 24px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; }}
    .k {{ color: #6b7280; font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }}
    .v {{ font-size: 14px; white-space: pre-wrap; word-break: break-word; }}
    a {{ color: #2563eb; }}
    button {{ border-radius: 10px; padding: 10px 12px; border: 1px solid #e5e7eb; background: #fff; cursor: pointer; }}
    table td {{ border-bottom: 1px solid #f3f4f6; padding: 6px; vertical-align: top; }}
    table th {{ text-align:left; border-bottom:1px solid #e5e7eb; padding:6px; }}
  </style>
</head>
<body>
  <h1>PCO → UniFi Access Sync</h1>
  <p><a href=\"/api/status\">JSON status</a></p>

  <div class=\"grid\">
    <div class=\"card\"><div class=\"k\">Last Sync</div><div class=\"v\">{last_sync_at}</div></div>
    <div class=\"card\"><div class=\"k\">Last Result</div><div class=\"v\">{last_sync_result}</div></div>
    <div class=\"card\"><div class=\"k\">PCO</div><div class=\"v\">{pco_status}</div></div>
    <div class=\"card\"><div class=\"k\">UniFi Access</div><div class=\"v\">{unifi_status}</div></div>
  </div>

  <div class=\"card\" style=\"margin-top: 16px;\">
    <div class=\"k\">Recent Errors</div>
    <div class=\"v\">{recent_errors}</div>
  </div>

  <div class=\"card\" style=\"margin-top: 16px;\">
    <div class=\"k\">PCO API Counters</div>
    <div class=\"v\">{pco_stats_text}</div>
  </div>

  <div style=\"margin-top: 16px; display: flex; gap: 12px;\">
    <form method=\"post\" action=\"/api/sync/run\">
      <button type=\"submit\">Run sync now</button>
    </form>

    <form method=\"post\" action=\"/dashboard/apply\">
      <input type=\"hidden\" name=\"apply\" value=\"{str(not apply_to_unifi).lower()}\" />
      <button type=\"submit\">Mode: {"APPLY" if apply_to_unifi else "DRY RUN"} (toggle)</button>
    </form>
  </div>

  <div class=\"card\" style=\"margin-top: 16px;\">
    <div class=\"k\">Config</div>
    <div class=\"v\">sync: {settings.sync_cron or f"interval {settings.sync_interval_seconds}s"}\nunifi: {settings.unifi_access_base_url}\nmapping: {settings.room_door_mapping_file}</div>
  </div>

  <div class=\"card\" style=\"margin-top: 16px;\">
    <div class=\"k\">Preview (upcoming)</div>
    <div class=\"v\">events: {len(preview_events)}\nschedule items: {len(preview_items)}\nmerged door windows: {len(preview_windows)}\n\nrooms found:\n{rooms_lines}</div>
    <p style=\"margin-top: 12px;\"><a href=\"/api/preview\">Preview JSON</a> | <a href=\"/api/events/upcoming\">Upcoming events JSON</a> | <a href=\"/api/pco/calendars\">PCO calendars</a> | <a href=\"/api/pco/event-instances/sample\">PCO event_instances sample</a></p>
    <div style=\"overflow:auto;\">
      <table style=\"width:100%; border-collapse: collapse;\">
        <thead>
          <tr>
            <th>Start</th>
            <th>End</th>
            <th>Event</th>
            <th>Building</th>
            <th>Room</th>
            <th>Door Group(s)</th>
          </tr>
        </thead>
        <tbody>
          {events_rows or '<tr><td colspan="6" style="padding:6px;">(none)</td></tr>'}
        </tbody>
      </table>
    </div>
  </div>

  <div class=\"card\" style=\"margin-top: 16px;\">
    <div class=\"k\">Room → Door Group(s) → UniFi Door IDs (from mapping file)</div>
    <div style=\"overflow:auto;\">
      <table style=\"width:100%; border-collapse: collapse;\">
        <thead>
          <tr>
            <th>Room</th>
            <th>Door group(s)</th>
            <th>UniFi door IDs</th>
          </tr>
        </thead>
        <tbody>
          {mapping_table_rows or '<tr><td colspan="3" style="padding:6px;">(none)</td></tr>'}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>"""
        return HTMLResponse(content=html, status_code=200)

    return app


app = create_app()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
