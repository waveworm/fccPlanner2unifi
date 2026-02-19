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

from py_app.logger import get_logger
from py_app.office_hours import load_office_hours, save_office_hours, validate_office_hours
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
        local_tz = ZoneInfo(settings.display_timezone)

        def _fmt_local(iso_str: str | None) -> str:
            if not iso_str:
                return ""
            try:
                dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00")).astimezone(local_tz)
                return dt.strftime("%a %b %d, %Y %I:%M:%S %p %Z")
            except Exception:
                return str(iso_str)

        status = sync_service.snapshot()
        last_sync_at_raw = status.get("lastSyncAt")
        last_sync_at = _fmt_local(last_sync_at_raw) if last_sync_at_raw else "(never)"
        last_sync_result = _esc(status.get("lastSyncResult") or "(none)")
        pco_status = _esc(status.get("pcoStatus") or "")
        unifi_status = _esc(status.get("unifiStatus") or "")
        def _fmt_error_line(line: str) -> str:
            # Error lines start with an ISO timestamp followed by a space.
            parts = line.split(" ", 1)
            if len(parts) == 2:
                ts_formatted = _fmt_local(parts[0])
                if ts_formatted and ts_formatted != parts[0]:
                    # ts_formatted is internally generated (safe); escape the user-facing message.
                    return f"{ts_formatted} {_esc(parts[1])}"
            return _esc(line)

        recent_errors = "\n".join([_fmt_error_line(e) for e in (status.get("recentErrors") or [])]) or "(none)"
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
            # Store plain text; _esc() applied at HTML render time below.
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
                    f"<td>{_esc(room_name)}</td>"
                    f"<td>{_esc(', '.join(door_labels)) if door_labels else ''}</td>"
                    f"<td>{_esc(' | '.join([x for x in door_ids if x]))}</td>"
                    "</tr>"
                )

        mapping_table_rows = "\n".join(mapping_rows)

        apply_to_unifi = bool(status.get("applyToUnifi"))
        _ts_keys = {"lastLiveFetchAt", "lastCacheHitAt", "last429FallbackAt"}
        pco_stats_text = "\n".join(
            [f"{k}: {_fmt_local(str(v)) if k in _ts_keys and v and str(v) != 'None' else _esc(str(v))}"
             for k, v in sorted(pco_stats.items(), key=lambda kv: kv[0])]
        ) or "(none)"

        rooms_lines = "\n".join([f"{_esc(str(k))}: {v}" for k, v in sorted(preview_rooms.items(), key=lambda kv: kv[0])]) or "(none)"

        events_rows = "\n".join(
            [
                "<tr>"
                f"<td>{_fmt_local(e.get('startAt'))}<br/><span style='color:#6b7280;font-size:12px'>{_esc(str(e.get('startAt') or ''))}</span></td>"
                f"<td>{_fmt_local(e.get('endAt'))}<br/><span style='color:#6b7280;font-size:12px'>{_esc(str(e.get('endAt') or ''))}</span></td>"
                f"<td>{_esc(str(e.get('name') or ''))}</td>"
                f"<td>{_esc(str(e.get('building') or ''))}</td>"
                f"<td>{_esc(', '.join(e['rooms']) if e.get('rooms') else _esc(str(e.get('room') or '')))}</td>"
                f"<td>{_esc(', '.join(event_doors.get(str(e.get('id') or ''), []))) or '(none mapped)'}</td>"
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
  <p><a href=\"/api/status\">JSON status</a> | <a href=\"/settings\">Room Mapping</a> | <a href=\"/office-hours\">Office Hours</a></p>

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

  <div id=\"dash-toast\" style=\"display:none;position:fixed;top:20px;right:20px;background:#059669;color:#fff;padding:12px 20px;border-radius:10px;font-size:14px;z-index:999;\"></div>

  <div style=\"margin-top: 16px; display: flex; gap: 12px;\">
    <button id=\"syncBtn\" onclick=\"runSync()\">Run sync now</button>

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

  <script>
    async function runSync() {{
      const btn = document.getElementById('syncBtn');
      const toast = document.getElementById('dash-toast');
      btn.disabled = true;
      btn.textContent = 'Syncing…';
      try {{
        const resp = await fetch('/api/sync/run', {{ method: 'POST' }});
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        toast.textContent = 'Sync triggered successfully.';
        toast.style.background = '#059669';
        toast.style.display = 'block';
        setTimeout(() => {{ toast.style.display = 'none'; location.reload(); }}, 2000);
      }} catch (err) {{
        toast.textContent = 'Sync failed: ' + err.message;
        toast.style.background = '#dc2626';
        toast.style.display = 'block';
        setTimeout(() => {{ toast.style.display = 'none'; }}, 4000);
        btn.disabled = false;
        btn.textContent = 'Run sync now';
      }}
    }}
  </script>
</body>
</html>"""
        return HTMLResponse(content=html, status_code=200)

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

        # Build table rows: each room gets a row with checkboxes for each door.
        rows_html = ""
        for room in room_names:
            assigned = set(rooms_map.get(room) or [])
            cells = f'<td class="room-name">{_esc(room)}</td>'
            for dk in door_keys:
                checked = "checked" if dk in assigned else ""
                cells += f'<td style="text-align:center"><input type="checkbox" name="room__{room}__{dk}" {checked} /></td>'
            rows_html += f"<tr>{cells}</tr>\n"

        # New room input row.
        new_row_cells = '<td><input type="text" name="new_room_name" placeholder="New room name…" style="width:100%;padding:6px;border:1px solid #d1d5db;border-radius:6px;" /></td>'
        for dk in door_keys:
            new_row_cells += f'<td style="text-align:center"><input type="checkbox" name="new_room__{dk}" /></td>'
        new_row = f'<tr style="background:#f0fdf4">{new_row_cells}</tr>'

        door_headers = "".join([f"<th>{_esc(str(doors_map[dk].get('label', dk)))}</th>" for dk in door_keys])

        lead = defaults.get("unlockLeadMinutes", 15)
        lag = defaults.get("unlockLagMinutes", 15)

        html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Settings — Room → Door Mapping</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; margin: 24px; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin-top: 16px; }}
    a {{ color: #2563eb; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ text-align: left; border-bottom: 2px solid #e5e7eb; padding: 8px 6px; font-size: 13px; }}
    td {{ border-bottom: 1px solid #f3f4f6; padding: 8px 6px; }}
    .room-name {{ font-weight: 500; white-space: nowrap; }}
    input[type="checkbox"] {{ width: 18px; height: 18px; cursor: pointer; }}
    button {{ border-radius: 10px; padding: 10px 20px; border: 1px solid #e5e7eb; background: #fff; cursor: pointer; font-size: 14px; }}
    button.primary {{ background: #2563eb; color: #fff; border-color: #2563eb; }}
    button.primary:hover {{ background: #1d4ed8; }}
    button.danger {{ color: #dc2626; border-color: #fca5a5; }}
    button.danger:hover {{ background: #fef2f2; }}
    .toast {{ display: none; position: fixed; top: 20px; right: 20px; background: #059669; color: #fff; padding: 12px 20px; border-radius: 10px; font-size: 14px; z-index: 999; }}
    .toast.error {{ background: #dc2626; }}
    .defaults-row {{ display: flex; gap: 16px; align-items: center; margin-top: 12px; }}
    .defaults-row label {{ font-size: 13px; color: #374151; }}
    .defaults-row input {{ width: 60px; padding: 6px; border: 1px solid #d1d5db; border-radius: 6px; text-align: center; }}
    nav {{ margin-bottom: 16px; font-size: 14px; }}
  </style>
</head>
<body>
  <nav><a href="/dashboard">← Dashboard</a></nav>
  <h1>Room → Door Mapping</h1>
  <p style="color:#6b7280;font-size:14px;">Check the doors that should unlock when an event is scheduled in each room. Changes are saved to <code>{settings.room_door_mapping_file}</code> and take effect on the next sync cycle.</p>

  <div id="toast" class="toast"></div>

  <form id="mappingForm">
    <div class="card" style="overflow:auto;">
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
    </div>

    <div class="card">
      <strong style="font-size:13px;color:#6b7280;text-transform:uppercase;letter-spacing:0.06em;">Defaults</strong>
      <div class="defaults-row">
        <label>Unlock lead (min): <input type="number" name="unlockLeadMinutes" value="{lead}" min="0" max="120" /></label>
        <label>Unlock lag (min): <input type="number" name="unlockLagMinutes" value="{lag}" min="0" max="120" /></label>
      </div>
    </div>

    <div style="margin-top: 16px; display: flex; gap: 12px;">
      <button type="submit" class="primary">Save mapping</button>
    </div>
  </form>

  <div class="card" style="margin-top:24px;">
    <strong style="font-size:13px;color:#6b7280;text-transform:uppercase;letter-spacing:0.06em;">Remove a room</strong>
    <p style="font-size:13px;color:#6b7280;">Uncheck all doors for a room and save — the room will be removed from the mapping.</p>
  </div>

  <script>
    const DOOR_KEYS = {json.dumps(door_keys)};
    const DOORS_MAP = {json.dumps(doors_map)};

    document.getElementById("mappingForm").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const form = e.target;
      const mapping = await fetch("/api/mapping").then(r => r.json());

      // Rebuild rooms from checkboxes.
      const newRooms = {{}};
      const existingRoomNames = {json.dumps(room_names)};

      for (const room of existingRoomNames) {{
        const assignedDoors = [];
        for (const dk of DOOR_KEYS) {{
          const cb = form.querySelector(`input[name="room__${{room}}__${{dk}}"]`);
          if (cb && cb.checked) assignedDoors.push(dk);
        }}
        // Only keep rooms that have at least one door checked.
        if (assignedDoors.length > 0) {{
          newRooms[room] = assignedDoors;
        }}
      }}

      // Handle new room row.
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

      // Update defaults.
      const lead = parseInt(form.querySelector('input[name="unlockLeadMinutes"]').value) || 15;
      const lag = parseInt(form.querySelector('input[name="unlockLagMinutes"]').value) || 15;

      mapping.rooms = newRooms;
      mapping.defaults = {{ unlockLeadMinutes: lead, unlockLagMinutes: lag }};

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
        return HTMLResponse(content=html, status_code=200)

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
                f'placeholder="e.g. 9:00-17:00 or 8:00-12:00, 13:00-17:00" class="ranges-input" /></td>'
                f'{door_cells}'
                f'</tr>\n'
            )

        enabled_checked = "checked" if oh_enabled else ""

        html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Office Hours — PCO UniFi Sync</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; margin: 24px; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin-top: 16px; }}
    a {{ color: #2563eb; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ text-align: left; border-bottom: 2px solid #e5e7eb; padding: 8px 6px; font-size: 13px; }}
    td {{ border-bottom: 1px solid #f3f4f6; padding: 8px 6px; vertical-align: middle; }}
    .day-name {{ font-weight: 600; white-space: nowrap; width: 90px; }}
    .ranges-input {{ width: 100%; padding: 6px 8px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; box-sizing: border-box; }}
    .ranges-input:focus {{ outline: none; border-color: #2563eb; box-shadow: 0 0 0 2px #bfdbfe; }}
    input[type="checkbox"] {{ width: 18px; height: 18px; cursor: pointer; accent-color: #2563eb; }}
    button {{ border-radius: 10px; padding: 10px 20px; border: 1px solid #e5e7eb; background: #fff; cursor: pointer; font-size: 14px; }}
    button.primary {{ background: #2563eb; color: #fff; border-color: #2563eb; }}
    button.primary:hover {{ background: #1d4ed8; }}
    .toast {{ display: none; position: fixed; top: 20px; right: 20px; background: #059669; color: #fff; padding: 12px 20px; border-radius: 10px; font-size: 14px; z-index: 999; }}
    .toast.error {{ background: #dc2626; }}
    .enable-row {{ display: flex; align-items: center; gap: 10px; }}
    .enable-row label {{ font-size: 15px; font-weight: 600; cursor: pointer; }}
    .enable-hint {{ font-size: 13px; color: #6b7280; margin-top: 6px; }}
    nav {{ margin-bottom: 16px; font-size: 14px; }}
    .hint-text {{ font-size: 12px; color: #9ca3af; margin-top: 3px; }}
  </style>
</head>
<body>
  <nav><a href="/dashboard">← Dashboard</a></nav>
  <h1>Office Hours</h1>
  <p style="color:#6b7280;font-size:14px;">Configure recurring weekly door-unlock times. When enabled, these windows are merged with Planning Center event schedules so doors stay unlocked during office hours regardless of whether an event is scheduled.</p>

  <div id="toast" class="toast"></div>

  <form id="officeHoursForm">
    <div class="card">
      <div class="enable-row">
        <input type="checkbox" name="enabled" id="enabledToggle" {enabled_checked} />
        <label for="enabledToggle">Enable Office Hours</label>
      </div>
      <p class="enable-hint">When unchecked, office hours are ignored during sync (your schedule below is preserved).</p>
    </div>

    <div class="card" style="overflow:auto; margin-top:16px;">
      <p style="font-size:13px;color:#6b7280;margin:0 0 12px;">Leave the hours field empty for a day to keep doors locked that day. Multiple ranges: <code>8:00-12:00, 13:00-17:00</code></p>
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

    <div style="margin-top:16px; display:flex; gap:12px; align-items:center;">
      <button type="submit" class="primary">Save office hours</button>
      <a href="/dashboard" style="font-size:14px;">Cancel</a>
    </div>
  </form>

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
        return HTMLResponse(content=html, status_code=200)

    return app


app = create_app()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
