from __future__ import annotations

import html
import json
from datetime import date, datetime, timezone
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode
from zoneinfo import ZoneInfo

_esc = html.escape  # shorthand used throughout HTML generation

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from py_app.audit import append_audit_log, ensure_tailscale_peer_map, read_recent_audit_entries
from py_app.event_overrides import (
    add_cancelled_event,
    load_cancelled_events,
    load_event_memory,
    load_event_overrides,
    remove_cancelled_event,
    save_event_overrides,
    validate_event_overrides,
)
from py_app.exceptions_calendar import (
    apply_office_hours_exceptions_to_instances,
    build_exception_instances,
    create_exception_entry,
    delete_exception_entry,
    list_exception_entries,
    validate_exception_entry,
)
from py_app.manual_access import (
    cancel_manual_access_entry,
    create_manual_access_entry,
    list_manual_access,
    update_manual_access_entry,
    validate_manual_access_window,
)
from py_app.approvals import is_manual_window_outside_safe_hours, load_safe_hours, save_safe_hours
from py_app.logger import get_logger
from py_app.office_hours import (
    add_cancelled_office_hours_date,
    get_office_hours_instances,
    load_cancelled_office_hours,
    load_office_hours,
    remove_cancelled_office_hours_date,
    save_office_hours,
    validate_office_hours,
)
from py_app.settings import Settings
from py_app.sync_service import SyncService
from py_app.utils import parse_iso
from py_app.vendors.unifi_access import UnifiAccessClient
from py_app.vendors.pco import PcoClient


# Shared CSS for all pages — plain Python string (no f-string) so CSS {} braces don't need escaping.
_SHARED_CSS = """
* { box-sizing: border-box; }
html { overflow-x: hidden; }
body {
  font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
  margin: 0; background: #f8fafc; color: #1e293b; min-height: 100vh;
  overflow-x: hidden; max-width: 100%;
  overscroll-behavior-x: none;
  overscroll-behavior-y: auto;
  -webkit-overflow-scrolling: touch;
  touch-action: pan-y pinch-zoom;
}
.page {
  max-width: 1600px; margin: 0 auto; padding: 24px; min-width: 0;
  touch-action: pan-y pinch-zoom;
}

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
  min-width: 0; touch-action: pan-y pinch-zoom;
}
.card-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .07em; color: #64748b; margin: 0 0 12px; display: block;
}

/* Collapsible sections */
details.collapsible {
  background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
  margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.04); overflow: visible;
  touch-action: pan-y pinch-zoom;
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
.details-body { padding: 16px 20px; touch-action: pan-y pinch-zoom; }

/* Inline help tip */
.help-tip {
  display:inline-flex; align-items:center; justify-content:center;
  width:16px; height:16px; border-radius:999px; margin-left:6px;
  border:1px solid #93c5fd; background:#dbeafe; color:#1e3a8a;
  font-size:11px; font-weight:800; line-height:1; cursor:help; position:relative;
  user-select:none; vertical-align:middle;
}
.help-tip::before {
  content:''; position:absolute; left:50%; top:calc(100% + 4px); transform:translateX(-50%);
  border:6px solid transparent; border-bottom-color:#0f172a; opacity:0; pointer-events:none;
  transition: opacity .12s ease;
}
.help-tip::after {
  content:attr(data-tip); position:absolute; left:50%; top:calc(100% + 10px); transform:translateX(-50%);
  width:max-content; min-width:220px; max-width:320px;
  background:#0f172a; color:#e2e8f0; border-radius:8px; padding:8px 10px;
  box-shadow:0 12px 30px rgba(2,6,23,.35); font-size:12px; font-weight:500; line-height:1.45;
  white-space:normal; opacity:0; pointer-events:none; z-index:10020; text-transform:none; letter-spacing:0;
  transition: opacity .12s ease;
}
.help-tip:hover::before, .help-tip:focus-visible::before,
.help-tip:hover::after, .help-tip:focus-visible::after { opacity:1; }

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
  transition: background .15s; touch-action: manipulation;
}
button:hover { background: #f1f5f9; }
button:disabled { opacity: .6; cursor: not-allowed; }
button.primary { background: #2563eb; color: #fff; border-color: #2563eb; }
button.primary:hover { background: #1d4ed8; }
button.danger { color: #dc2626; border-color: #fca5a5; }
button.danger:hover { background: #fef2f2; }
button.sm { padding: 6px 13px; font-size: 13px; }
button:focus-visible,
a:focus-visible,
summary:focus-visible,
.help-tip:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 2px;
}

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
.table-wrap {
  overflow-x: auto;
  overflow-y: hidden;
  max-width: 100%;
  -webkit-overflow-scrolling: touch;
  overscroll-behavior-x: contain;
  overscroll-behavior-y: auto;
}

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
  .card { padding: 12px 10px; overflow: visible; }
  .details-body { padding: 14px 12px; }
  details.collapsible > summary { padding: 12px 14px; }
  /* Tables */
  th, td { padding: 6px 7px; font-size: 13px; }
  th { font-size: 11px; }
  /* Hide low-priority columns on small screens */
  .hide-mob { display: none !important; }
  .show-mob { display: inline !important; }
  .table-wrap { overflow: visible; }
  .events-wrap, .manual-wrap { touch-action: pan-y; }
  .mobile-cards-table, .mobile-cards-table tbody, .mobile-cards-table tr, .mobile-cards-table td {
    display: block;
    width: 100%;
  }
  .mobile-cards-table thead { display: none; }
  .mobile-cards-table tbody tr {
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    overflow: hidden;
    padding: 9px 10px;
    margin-bottom: 8px;
    box-shadow: 0 1px 2px rgba(15,23,42,.04);
    touch-action: pan-y;
  }
  .mobile-cards-table tbody tr:last-child { margin-bottom: 0; }
  .mobile-cards-table td {
    border-bottom: none;
    padding: 0;
    margin-bottom: 7px;
    white-space: normal !important;
    touch-action: pan-y;
  }
  .mobile-cards-table td:last-child { margin-bottom: 0; }
  .mobile-cards-table td[data-label]::before {
    content: attr(data-label);
    display: block;
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .06em;
    color: #94a3b8;
    margin-bottom: 2px;
  }
  .mobile-cards-table .actions-cell {
    display: flex;
    flex-direction: row;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
  }
  .mobile-cards-table .hide-mob { display: none !important; }
  .mobile-cards-table .actions-cell button {
    width: auto;
    flex: 0 1 auto;
    margin: 0 !important;
  }
  .mobile-cards-table .event-name-cell strong {
    display: inline-block;
    font-size: 14px;
    line-height: 1.25;
    color: #0f172a;
  }
  .mobile-cards-table .time-cell { color: #334155; font-size: 12px; }
  .mobile-cards-table .meta-cell { color: #475569; font-size: 12px; line-height: 1.35; }
  .mobile-cards-table .event-name-cell > div,
  .mobile-cards-table .event-name-cell > span,
  .mobile-cards-table .event-name-cell br { max-width: 100%; }
  .events-table,
  .manual-table,
  .events-table tbody tr,
  .events-table tbody,
  .events-table td,
  .manual-table tbody tr,
  .manual-table tbody,
  .manual-table td {
    pointer-events: none;
  }
  .events-table button,
  .events-table a,
  .manual-table button,
  .manual-table a {
    pointer-events: auto;
  }
  .events-table .actions-cell br,
  .manual-table .actions-cell br { display: none; }
  .events-table .actions-cell button,
  .manual-table .actions-cell button { padding: 5px 10px; font-size: 12px; }
  .events-table td[data-label="Event"]::before,
  .manual-table td[data-label="Description"]::before { margin-bottom: 1px; }
  /* Toast — anchor to bottom so it doesn't overlap content */
  .toast { top: auto; bottom: 16px; left: 12px; right: 12px; text-align: center; }
  /* Headings */
  .page-heading { font-size: 18px; }
  .stat-grid { grid-template-columns: 1fr 1fr; gap: 12px; }
  .help-tip::after { min-width: 180px; max-width: min(280px, calc(100vw - 32px)); left:auto; right:0; transform:none; }
  .help-tip::before { left:auto; right:6px; transform:none; }
}
"""


_DOOR_COLORS = ["#3b82f6", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#ec4899"]


def _make_icon_png(size: int) -> bytes:
    """Generate a door icon PNG: navy background, slate frame, glass panel, blue knob."""
    import struct, zlib

    W = H = size
    buf = bytearray([15, 23, 42] * (W * H))  # navy #0f172a background

    def fill_rect(fx1: float, fy1: float, fx2: float, fy2: float, col: tuple) -> None:
        x1, y1 = int(fx1 * W), int(fy1 * H)
        x2, y2 = int(fx2 * W), int(fy2 * H)
        cr, cg, cb = col
        row_bytes = bytes([cr, cg, cb] * max(0, x2 - x1))
        for y in range(max(0, y1), min(H, y2)):
            base = y * W * 3 + x1 * 3
            buf[base : base + len(row_bytes)] = row_bytes

    def fill_circle(fcx: float, fcy: float, fr: float, col: tuple) -> None:
        cx, cy, rad = int(fcx * W), int(fcy * H), max(1, int(fr * W))
        cr, cg, cb = col
        rad2 = rad * rad
        for y in range(max(0, cy - rad - 1), min(H, cy + rad + 2)):
            for x in range(max(0, cx - rad - 1), min(W, cx + rad + 2)):
                if (x - cx) ** 2 + (y - cy) ** 2 <= rad2:
                    i = (y * W + x) * 3
                    buf[i] = cr; buf[i + 1] = cg; buf[i + 2] = cb

    # Proportions based on a 64-unit grid (same as the SVG)
    # Door frame (slate-400 #94a3b8)
    fill_rect(17/64, 12/64, 47/64, 53/64, (148, 163, 184))
    # Door surface (slate-50 #f8fafc)
    fill_rect(20/64, 15/64, 44/64, 50/64, (248, 250, 252))
    # Upper glass panel (blue-200 #bfdbfe)
    fill_rect(23/64, 18/64, 41/64, 28/64, (191, 219, 254))
    # Horizontal divider (slate-300 #cbd5e1)
    fill_rect(20/64, 31/64, 44/64, 33/64, (203, 213, 225))
    # Lower panel (slate-200 #e2e8f0)
    fill_rect(23/64, 34/64, 41/64, 46/64, (226, 232, 240))
    # Doorknob (blue-500 #3b82f6)
    fill_circle(40/64, 36/64, 2.8/64, (59, 130, 246))

    raw = b"".join(b"\x00" + bytes(buf[y * W * 3 : (y + 1) * W * 3]) for y in range(H))
    compressed = zlib.compress(raw, 6)

    def chunk(t: bytes, d: bytes) -> bytes:
        c = zlib.crc32(t + d) & 0xFFFFFFFF
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", c)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


# SVG icon — used as browser-tab favicon (vector, scales perfectly at any size)
_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" fill="#0f172a"/>'
    # Door frame slate-400
    '<rect x="17" y="12" width="30" height="41" rx="2" fill="#94a3b8"/>'
    # Door surface slate-50
    '<rect x="20" y="15" width="24" height="35" rx="1" fill="#f8fafc"/>'
    # Upper glass panel blue-200
    '<rect x="23" y="18" width="18" height="10" rx="1" fill="#bfdbfe"/>'
    # Divider slate-300
    '<rect x="20" y="31" width="24" height="2" fill="#cbd5e1"/>'
    # Lower panel slate-200
    '<rect x="23" y="34" width="18" height="12" rx="1" fill="#e2e8f0"/>'
    # Doorknob blue-500
    '<circle cx="40" cy="36" r="2.5" fill="#3b82f6"/>'
    '</svg>'
)

_ICON_192 = _make_icon_png(192)
_ICON_512 = _make_icon_png(512)

_PWA_HEAD = (
    '  <link rel="manifest" href="/manifest.json">\n'
    '  <link rel="icon" type="image/svg+xml" href="/icon.svg">\n'
    '  <meta name="theme-color" content="#0f172a">\n'
    '  <meta name="mobile-web-app-capable" content="yes">\n'
    '  <meta name="apple-mobile-web-app-capable" content="yes">\n'
    '  <meta name="apple-mobile-web-app-title" content="PCO Sync">\n'
    '  <link rel="apple-touch-icon" href="/icon-192.png">\n'
    "  <script>if('serviceWorker'in navigator){"
    "navigator.serviceWorker.register('/sw.js');}</script>\n"
)


def create_app() -> FastAPI:
    settings = Settings()
    logger = get_logger()

    app = FastAPI(title="PCO → UniFi Access Sync")
    sync_service = SyncService(settings, logger)
    unifi_client = UnifiAccessClient(settings)
    pco_client = PcoClient(settings)
    config_dir = Path(settings.room_door_mapping_file).resolve().parent
    audit_log_file = str(config_dir / "audit-log.jsonl")
    tailscale_peers_file = str(config_dir / "tailscale-peers.json")
    manual_access_file = str(config_dir / "manual-access-windows.json")
    exception_calendar_file = settings.exception_calendar_file
    ensure_tailscale_peer_map(tailscale_peers_file)

    scheduler = AsyncIOScheduler(timezone="UTC")

    def _audit(
        request: Request,
        *,
        action: str,
        target: str = "",
        note: str = "",
        result: str = "ok",
        error: str = "",
        extra: dict | None = None,
    ) -> None:
        append_audit_log(
            audit_log_file,
            tailscale_peers_file,
            request=request,
            action=action,
            target=target,
            note=note,
            result=result,
            error=error,
            extra=extra,
        )

    async def _notify(request: Request, message: str) -> None:
        """Send a Telegram notification for a manual user action (no-ops if not configured)."""
        from py_app.audit import resolve_request_actor
        actor = resolve_request_actor(request, tailscale_peers_file)
        display = actor.get("displayName") or actor.get("requestIp") or "Someone"
        await sync_service.telegram.notify_user_action(display, message)

    def _manual_access_approval_reason(start_at: str, end_at: str) -> str:
        start_dt = parse_iso(start_at)
        end_dt = parse_iso(end_at)
        if start_dt is None or end_dt is None:
            return ""
        safe_hours = load_safe_hours(settings.safe_hours_file)
        local_tz = ZoneInfo(settings.display_timezone)
        outside, reason = is_manual_window_outside_safe_hours(
            start_dt,
            end_dt,
            local_tz,
            safe_hours,
        )
        return reason if outside else ""

    def _event_room_candidates_local(event: dict) -> list[str]:
        rooms: list[str] = []
        evt_rooms = event.get("rooms")
        if isinstance(evt_rooms, list):
            for room in evt_rooms:
                room_text = str(room or "").strip()
                if room_text:
                    rooms.append(room_text)
        if not rooms:
            room_text = str(event.get("room") or "").strip()
            if room_text:
                rooms.append(room_text)
        return list(dict.fromkeys(rooms))

    def _local_day_range(days: int) -> tuple[datetime, datetime, ZoneInfo]:
        local_tz = ZoneInfo(settings.display_timezone)
        now = datetime.now(timezone.utc)
        start_local = now.astimezone(local_tz).replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=days)
        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), local_tz

    def _build_schedule_views(mapping: dict) -> list[dict[str, Any]]:
        doors_cfg = mapping.get("doors") or {}
        all_door_keys = [str(k) for k in doors_cfg.keys()]
        all_door_set = set(all_door_keys)
        views: list[dict[str, Any]] = [{
            "key": "all",
            "label": "All Church",
            "doorKeys": all_door_keys,
            "roomNames": [],
            "source": "built_in",
        }]
        seen_keys = {"all"}

        zone_views_cfg = mapping.get("zoneViews") or {}
        if isinstance(zone_views_cfg, dict):
            for key, raw in zone_views_cfg.items():
                if key in seen_keys or not isinstance(raw, dict):
                    continue
                door_keys = [str(k).strip() for k in (raw.get("doorKeys") or []) if str(k).strip() in all_door_set]
                room_names = [str(r).strip() for r in (raw.get("roomNames") or []) if str(r).strip()]
                if not door_keys and not room_names:
                    continue
                views.append({
                    "key": str(key),
                    "label": str(raw.get("label") or key),
                    "doorKeys": door_keys or all_door_keys,
                    "roomNames": room_names,
                    "source": "zone_view",
                })
                seen_keys.add(str(key))

        door_groups_cfg = mapping.get("doorGroups") or {}
        if isinstance(door_groups_cfg, dict):
            for key, raw in door_groups_cfg.items():
                if key in seen_keys or not isinstance(raw, dict):
                    continue
                door_keys = [str(k).strip() for k in (raw.get("doorKeys") or []) if str(k).strip() in all_door_set]
                if not door_keys or set(door_keys) == all_door_set:
                    continue
                views.append({
                    "key": str(key),
                    "label": str(raw.get("label") or key),
                    "doorKeys": door_keys,
                    "roomNames": [],
                    "source": "door_group",
                })
                seen_keys.add(str(key))

        return views

    def _pick_schedule_view(mapping: dict, view_key: str | None) -> dict[str, Any]:
        views = _build_schedule_views(mapping)
        selected = next((view for view in views if view["key"] == str(view_key or "").strip()), None)
        return selected or views[0]

    def _board_event_matches_view(
        *,
        door_keys: list[str],
        room_names: list[str],
        selected_view: dict[str, Any],
    ) -> bool:
        view_door_keys = set(selected_view.get("doorKeys") or [])
        view_room_names = {str(name).strip() for name in (selected_view.get("roomNames") or []) if str(name).strip()}
        if not view_door_keys and not view_room_names:
            return True
        if view_door_keys.intersection({str(key).strip() for key in door_keys if str(key).strip()}):
            return True
        if view_room_names.intersection({str(name).strip() for name in room_names if str(name).strip()}):
            return True
        return False

    def _board_event_matches_query(event: dict[str, Any], query: str) -> bool:
        if not query:
            return True
        haystack = " ".join([
            str(event.get("typeLabel") or ""),
            str(event.get("name") or ""),
            str(event.get("roomText") or ""),
            " ".join(str(label) for label in (event.get("doorLabels") or [])),
        ]).lower()
        return query in haystack

    async def _build_schedule_board(days: int, *, view_key: str = "all", query: str = "") -> dict:
        days = max(1, min(int(days), 14))
        start_dt, end_dt, local_tz = _local_day_range(days)
        now = datetime.now(timezone.utc)
        preview_limit = max(250, days * 80)
        preview = await sync_service.get_preview(start_dt=start_dt, end_dt=end_dt, limit=preview_limit)
        mapping = _read_mapping()
        selected_view = _pick_schedule_view(mapping, view_key)
        raw_query = str(query or "").strip()
        query = raw_query.lower()
        doors_cfg = mapping.get("doors") or {}
        door_keys = list(doors_cfg.keys())
        door_color_map = {dk: _DOOR_COLORS[i % len(_DOOR_COLORS)] for i, dk in enumerate(door_keys)}
        selected_door_keys = [dk for dk in (selected_view.get("doorKeys") or []) if dk in doors_cfg]
        selected_door_key_set = set(selected_door_keys)

        event_doors: dict[str, list[str]] = {}
        for item in (preview.get("schedule") or {}).get("items") or []:
            event_id = str(item.get("sourceEventId") or "").strip()
            door_key = str(item.get("doorKey") or "").strip()
            if not event_id or not door_key:
                continue
            event_doors.setdefault(event_id, [])
            if door_key not in event_doors[event_id]:
                event_doors[event_id].append(door_key)

        day_rows: list[dict] = []
        day_lookup: dict[str, dict] = {}
        cursor = start_dt.astimezone(local_tz)
        for _ in range(days):
            date_iso = cursor.date().isoformat()
            row = {
                "date": date_iso,
                "label": cursor.strftime("%a %-m/%-d"),
                "longLabel": cursor.strftime("%A, %b %-d"),
                "isToday": cursor.date() == now.astimezone(local_tz).date(),
                "events": [],
            }
            day_rows.append(row)
            day_lookup[date_iso] = row
            cursor += timedelta(days=1)

        board_events: list[dict] = []
        for event in preview.get("events") or []:
            event_id = str(event.get("id") or "")
            start_iso = str(event.get("startAt") or "")
            end_iso = str(event.get("endAt") or "")
            start_local = parse_iso(start_iso)
            end_local = parse_iso(end_iso)
            if not start_local or not end_local:
                continue
            start_local = start_local.astimezone(local_tz)
            end_local = end_local.astimezone(local_tz)
            date_key = start_local.date().isoformat()
            if date_key not in day_lookup:
                continue
            room_names = _event_room_candidates_local(event)
            event_door_keys = list(event_doors.get(event_id, []))
            if not _board_event_matches_view(
                door_keys=event_door_keys,
                room_names=room_names,
                selected_view=selected_view,
            ):
                continue
            display_door_keys = [
                dk for dk in event_door_keys
                if not selected_door_key_set or dk in selected_door_key_set
            ] or event_door_keys
            door_labels = [
                str((doors_cfg.get(dk) or {}).get("label") or dk)
                for dk in display_door_keys
            ]
            board_event = {
                "id": event_id,
                "type": "event",
                "typeLabel": "PCO Event",
                "name": str(event.get("name") or ""),
                "startAt": start_iso,
                "endAt": end_iso,
                "startLabel": start_local.strftime("%-I:%M %p"),
                "endLabel": end_local.strftime("%-I:%M %p"),
                "roomText": ", ".join(room_names) or "No room",
                "doorLabels": [label for label in door_labels if label],
                "doorKeys": display_door_keys,
                "sortKey": start_iso,
            }
            if not _board_event_matches_query(board_event, query):
                continue
            board_events.append(board_event)
            day_lookup[date_key]["events"].append(board_event)

        exception_entries = list_exception_entries(settings.exception_calendar_file)
        office_hours_cfg = load_office_hours(settings.office_hours_file)
        cancelled_oh = load_cancelled_office_hours(settings.cancelled_office_hours_file)
        office_events = get_office_hours_instances(
            office_hours_cfg,
            start_dt,
            end_dt,
            local_tz,
            cancelled_dates=cancelled_oh,
        )
        office_events = apply_office_hours_exceptions_to_instances(
            office_events,
            exception_entries,
            local_tz=local_tz,
            doors_map=doors_cfg,
        )
        for event in office_events:
            start_iso = str(event.get("startAt") or "")
            end_iso = str(event.get("endAt") or "")
            start_local = parse_iso(start_iso)
            end_local = parse_iso(end_iso)
            if not start_local or not end_local:
                continue
            start_local = start_local.astimezone(local_tz)
            end_local = end_local.astimezone(local_tz)
            date_key = start_local.date().isoformat()
            if date_key not in day_lookup:
                continue
            door_keys_for_event = [str(dk) for dk in (event.get("doors") or []) if str(dk)]
            if not _board_event_matches_view(
                door_keys=door_keys_for_event,
                room_names=[],
                selected_view=selected_view,
            ):
                continue
            display_door_keys = [
                dk for dk in door_keys_for_event
                if not selected_door_key_set or dk in selected_door_key_set
            ] or door_keys_for_event
            board_event = {
                "id": str(event.get("id") or ""),
                "type": "office_hours",
                "typeLabel": "Office Hours",
                "name": "Office Hours",
                "startAt": start_iso,
                "endAt": end_iso,
                "startLabel": start_local.strftime("%-I:%M %p"),
                "endLabel": end_local.strftime("%-I:%M %p"),
                "roomText": str(event.get("timeRanges") or "Recurring schedule"),
                "doorLabels": [
                    str((doors_cfg.get(dk) or {}).get("label") or dk)
                    for dk in display_door_keys
                ],
                "doorKeys": display_door_keys,
                "sortKey": start_iso,
            }
            if not _board_event_matches_query(board_event, query):
                continue
            board_events.append(board_event)
            day_lookup[date_key]["events"].append(board_event)

        for entry in list_manual_access(manual_access_file):
            start_dt_entry = parse_iso(entry.get("startAt"))
            end_dt_entry = parse_iso(entry.get("endAt"))
            if not start_dt_entry or not end_dt_entry:
                continue
            if end_dt_entry <= start_dt or start_dt_entry >= end_dt:
                continue
            start_local = start_dt_entry.astimezone(local_tz)
            end_local = end_dt_entry.astimezone(local_tz)
            date_key = start_local.date().isoformat()
            if date_key not in day_lookup:
                continue
            entry_door_keys = [str(dk).strip() for dk in (entry.get("doorKeys") or []) if str(dk).strip()]
            if not _board_event_matches_view(
                door_keys=entry_door_keys or door_keys,
                room_names=[],
                selected_view=selected_view,
            ):
                continue
            display_door_keys = [
                dk for dk in entry_door_keys
                if not selected_door_key_set or dk in selected_door_key_set
            ] or entry_door_keys
            note_text = str(entry.get("note") or "").strip()
            board_event = {
                "id": f"manual-{str(entry.get('id') or '')}",
                "type": "manual_access",
                "typeLabel": "Manual Access",
                "name": note_text or "Manual Access",
                "startAt": str(entry.get("startAt") or ""),
                "endAt": str(entry.get("endAt") or ""),
                "startLabel": start_local.strftime("%-I:%M %p"),
                "endLabel": end_local.strftime("%-I:%M %p"),
                "roomText": "Temporary door access",
                "doorLabels": [
                    str((doors_cfg.get(dk) or {}).get("label") or dk)
                    for dk in display_door_keys
                ],
                "doorKeys": display_door_keys,
                "sortKey": str(entry.get("startAt") or ""),
            }
            if not _board_event_matches_query(board_event, query):
                continue
            board_events.append(board_event)
            day_lookup[date_key]["events"].append(board_event)

        for event in build_exception_instances(
            exception_entries,
            from_dt=start_dt,
            to_dt=end_dt,
            local_tz=local_tz,
            doors_map=doors_cfg,
        ):
            start_iso = str(event.get("startAt") or "")
            end_iso = str(event.get("endAt") or "")
            start_local = parse_iso(start_iso)
            end_local = parse_iso(end_iso)
            if not start_local or not end_local:
                continue
            start_local = start_local.astimezone(local_tz)
            end_local = end_local.astimezone(local_tz)
            date_key = start_local.date().isoformat()
            if date_key not in day_lookup:
                continue
            event_type = str(event.get("type") or "")
            event_door_keys = [str(dk) for dk in (event.get("doors") or []) if str(dk)]
            if not _board_event_matches_view(
                door_keys=event_door_keys or door_keys,
                room_names=[],
                selected_view=selected_view,
            ):
                continue
            display_door_keys = [
                dk for dk in event_door_keys
                if not selected_door_key_set or dk in selected_door_key_set
            ] or event_door_keys
            board_event = {
                "id": str(event.get("id") or ""),
                "type": event_type,
                "typeLabel": "Office Closed" if event_type == "exception_closure" else "Extra Office Hours",
                "name": str(event.get("name") or ""),
                "startAt": start_iso,
                "endAt": end_iso,
                "startLabel": start_local.strftime("%-I:%M %p"),
                "endLabel": end_local.strftime("%-I:%M %p"),
                "roomText": str(event.get("note") or ("Office hours closed for this date" if event_type == "exception_closure" else "One-time office-hours window")),
                "doorLabels": [
                    str((doors_cfg.get(dk) or {}).get("label") or dk)
                    for dk in display_door_keys
                ],
                "doorKeys": display_door_keys,
                "sortKey": start_iso,
            }
            if not _board_event_matches_query(board_event, query):
                continue
            board_events.append(board_event)
            day_lookup[date_key]["events"].append(board_event)

        for row in day_rows:
            row["events"].sort(key=lambda item: (item.get("sortKey") or "", item.get("name") or ""))

        visible_pco_event_ids = {
            str(item.get("id") or "")
            for item in board_events
            if item.get("type") == "event" and str(item.get("id") or "")
        }

        room_conflicts: list[dict] = []
        seen_room_conflicts: set[tuple[str, str, str]] = set()
        room_events: dict[str, list[dict]] = {}
        for event in preview.get("events") or []:
            start_dt_event = parse_iso(event.get("startAt"))
            end_dt_event = parse_iso(event.get("endAt"))
            event_id = str(event.get("id") or "")
            if not start_dt_event or not end_dt_event or not event_id:
                continue
            if event_id not in visible_pco_event_ids:
                continue
            for room_name in _event_room_candidates_local(event):
                room_events.setdefault(room_name, []).append({
                    "id": event_id,
                    "name": str(event.get("name") or ""),
                    "start": start_dt_event,
                    "end": end_dt_event,
                })
        for room_name, entries in room_events.items():
            entries.sort(key=lambda item: item["start"])
            active: list[dict] = []
            for current in entries:
                active = [item for item in active if item["end"] > current["start"]]
                for previous in active:
                    key = tuple(sorted([previous["id"], current["id"]]) + [room_name])
                    if key in seen_room_conflicts:
                        continue
                    seen_room_conflicts.add(key)
                    overlap_start = max(previous["start"], current["start"]).astimezone(local_tz)
                    overlap_end = min(previous["end"], current["end"]).astimezone(local_tz)
                    room_conflicts.append({
                        "room": room_name,
                        "firstEvent": previous["name"],
                        "secondEvent": current["name"],
                        "startLabel": overlap_start.strftime("%a %-m/%-d %-I:%M %p"),
                        "endLabel": overlap_end.strftime("%-I:%M %p"),
                    })
                active.append(current)
        room_conflicts.sort(key=lambda item: (item["startLabel"], item["room"]))

        shared_door_windows: list[dict] = []
        for window in (preview.get("schedule") or {}).get("doorWindows") or []:
            door_key = str(window.get("doorKey") or "")
            if selected_door_key_set and door_key not in selected_door_key_set:
                continue
            names = [str(name or "").strip() for name in (window.get("sourceEventNames") or []) if str(name or "").strip()]
            unique_names = list(dict.fromkeys(names))
            if len(unique_names) < 2:
                continue
            start_local = parse_iso(window.get("openStart"))
            end_local = parse_iso(window.get("openEnd"))
            if not start_local or not end_local:
                continue
            start_local = start_local.astimezone(local_tz)
            end_local = end_local.astimezone(local_tz)
            shared_door_windows.append({
                "doorKey": door_key,
                "doorLabel": str(window.get("doorLabel") or window.get("doorKey") or ""),
                "startLabel": start_local.strftime("%a %-m/%-d %-I:%M %p"),
                "endLabel": end_local.strftime("%-I:%M %p"),
                "eventNames": unique_names,
            })
        if query:
            shared_door_windows = [
                item for item in shared_door_windows
                if query in " ".join([item["doorLabel"], *item["eventNames"]]).lower()
            ]
        shared_door_windows.sort(key=lambda item: (item["startLabel"], item["doorLabel"]))

        timeline_rows: list[dict] = []
        timeline_by_key: dict[str, dict] = {}
        timeline_seed_keys = selected_door_keys or door_keys
        for dk in timeline_seed_keys:
            row = {
                "key": dk,
                "label": str((doors_cfg.get(dk) or {}).get("label") or dk),
                "color": door_color_map.get(dk, _DOOR_COLORS[0]),
                "days": {day["date"]: [] for day in day_rows},
            }
            timeline_rows.append(row)
            timeline_by_key[dk] = row

        for window in (preview.get("schedule") or {}).get("doorWindows") or []:
            door_key = str(window.get("doorKey") or "")
            if selected_door_key_set and door_key not in selected_door_key_set:
                continue
            if door_key not in timeline_by_key:
                row = {
                    "key": door_key,
                    "label": str(window.get("doorLabel") or door_key),
                    "color": door_color_map.get(door_key, _DOOR_COLORS[len(timeline_rows) % len(_DOOR_COLORS)]),
                    "days": {day["date"]: [] for day in day_rows},
                }
                timeline_rows.append(row)
                timeline_by_key[door_key] = row
            start_utc = parse_iso(window.get("openStart"))
            end_utc = parse_iso(window.get("openEnd"))
            if not start_utc or not end_utc:
                continue
            current_local = start_utc.astimezone(local_tz)
            end_local = end_utc.astimezone(local_tz)
            names = list(dict.fromkeys([
                str(name or "").strip()
                for name in (window.get("sourceEventNames") or [])
                if str(name or "").strip()
            ]))
            title_query_text = " ".join([timeline_by_key[door_key]["label"], *names]).lower()
            if query and query not in title_query_text:
                continue
            while current_local < end_local:
                next_midnight = (current_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                segment_end = min(end_local, next_midnight)
                date_key = current_local.date().isoformat()
                if date_key in timeline_by_key[door_key]["days"]:
                    start_min = current_local.hour * 60 + current_local.minute
                    end_min = segment_end.hour * 60 + segment_end.minute
                    if end_min == 0:
                        end_min = 1440
                    if end_min > start_min:
                        timeline_by_key[door_key]["days"][date_key].append({
                            "startMin": start_min,
                            "endMin": end_min,
                            "title": f"{timeline_by_key[door_key]['label']}: "
                                     f"{current_local.strftime('%-I:%M %p')} - {segment_end.strftime('%-I:%M %p')}"
                                     + (f" | {', '.join(names)}" if names else ""),
                            "events": names,
                        })
                current_local = next_midnight

        for row in timeline_rows:
            for date_key in row["days"]:
                row["days"][date_key].sort(key=lambda item: (item["startMin"], item["endMin"]))
        if query:
            timeline_rows = [
                row for row in timeline_rows
                if query in row["label"].lower() or any(row["days"][day["date"]] for day in day_rows)
            ]

        return {
            "days": days,
            "selectedView": selected_view,
            "availableViews": _build_schedule_views(mapping),
            "query": raw_query,
            "timezone": settings.display_timezone,
            "generatedAt": now.astimezone(local_tz).strftime("%a %b %-d, %-I:%M %p"),
            "summary": {
                "eventCount": len([item for item in board_events if item["type"] == "event"]),
                "totalItems": len(board_events),
                "activeDoors": len([row for row in timeline_rows if any(row["days"][day["date"]] for day in day_rows)]),
                "roomConflictCount": len(room_conflicts),
                "sharedDoorCount": len(shared_door_windows),
            },
            "dayRows": day_rows,
            "timelineRows": timeline_rows,
            "roomConflicts": room_conflicts,
            "sharedDoorWindows": shared_door_windows,
        }

    def _nav(active: str) -> str:
        """Generate the shared site header HTML."""
        pages = [
            ("dashboard",        "/dashboard",        "Dashboard"),
            ("schedule-board",   "/schedule-board",   "Schedule Board"),
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

    @app.get("/manifest.json")
    async def pwa_manifest() -> Response:
        return Response(
            content=json.dumps({
                "name": "PCO \u2192 UniFi Sync",
                "short_name": "PCO Sync",
                "start_url": "/dashboard",
                "display": "standalone",
                "background_color": "#0f172a",
                "theme_color": "#0f172a",
                "icons": [
                    {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"},
                    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
                ],
            }),
            media_type="application/manifest+json",
        )

    @app.get("/sw.js")
    async def pwa_service_worker() -> Response:
        return Response(
            content="self.addEventListener('fetch',function(e){});\n",
            media_type="application/javascript",
        )

    @app.get("/icon.svg")
    async def pwa_icon_svg() -> Response:
        return Response(content=_ICON_SVG, media_type="image/svg+xml")

    @app.get("/icon-192.png")
    async def pwa_icon_192() -> Response:
        return Response(content=_ICON_192, media_type="image/png")

    @app.get("/icon-512.png")
    async def pwa_icon_512() -> Response:
        return Response(content=_ICON_512, media_type="image/png")

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
    async def api_lock_door(door_id: str, request: Request) -> dict:
        from fastapi.responses import JSONResponse
        try:
            await unifi_client.lock_door(door_id)
            _audit(request, action="door.lock", target=door_id)
            return {"ok": True}
        except Exception as exc:
            _audit(request, action="door.lock", target=door_id, result="error", error=str(exc))
            return JSONResponse(status_code=502, content={"ok": False, "error": str(exc)})

    @app.get("/api/door-schedule")
    async def api_door_schedule() -> dict:
        """Return per-door unlock windows (local day + minutes) for the current sync window."""
        from zoneinfo import ZoneInfo
        from datetime import datetime, timedelta

        now = datetime.now(timezone.utc)
        lookbehind_hours = max(int(settings.sync_lookbehind_hours), 24)
        start_dt = now - timedelta(hours=lookbehind_hours)
        end_dt = now + timedelta(hours=int(settings.sync_lookahead_hours))
        preview = await sync_service.get_preview(start_dt=start_dt, end_dt=end_dt)
        door_windows = (preview.get("schedule") or {}).get("doorWindows") or []

        mapping_cfg: dict = {}
        try:
            from py_app.mapping import load_room_door_mapping
            mapping_cfg = load_room_door_mapping(settings.room_door_mapping_file)
            all_door_keys = list((mapping_cfg.get("doors") or {}).keys())
        except Exception:
            all_door_keys = []

        color_for_key = {dk: _DOOR_COLORS[i % len(_DOOR_COLORS)] for i, dk in enumerate(all_door_keys)}
        local_tz = ZoneInfo(settings.display_timezone)
        local_today = now.astimezone(local_tz).date()
        local_today_start = datetime.combine(local_today, datetime.min.time(), tzinfo=local_tz)
        visible_date_keys = {
            (local_today + timedelta(days=offset)).isoformat()
            for offset in range(7)
        }
        day_dates_by_weekday = {
            (local_today + timedelta(days=offset)).weekday(): {
                "weekday": (local_today + timedelta(days=offset)).weekday(),
                "dayLabel": (local_today + timedelta(days=offset)).strftime("%a"),
                "dateLabel": (local_today + timedelta(days=offset)).strftime("%-m/%-d"),
                "isoDate": (local_today + timedelta(days=offset)).isoformat(),
                "isToday": offset == 0,
            }
            for offset in range(7)
        }
        door_map: dict[str, dict] = {}
        active_names_by_key: dict[str, list[str]] = {}
        active_count_by_key: dict[str, int] = {}

        for w in door_windows:
            try:
                start_utc = datetime.fromisoformat(str(w["openStart"]).replace("Z", "+00:00"))
                end_utc = datetime.fromisoformat(str(w["openEnd"]).replace("Z", "+00:00"))
            except Exception:
                continue
            # Keep windows from the current local day even after they have ended so
            # operators can still review what opened earlier today on the timeline.
            if end_utc.astimezone(local_tz) <= local_today_start:
                continue
            key = w["doorKey"]
            if key not in door_map:
                door_map[key] = {
                    "key": key,
                    "label": w["doorLabel"],
                    "color": color_for_key.get(key, _DOOR_COLORS[len(door_map) % len(_DOOR_COLORS)]),
                    "windows": [],
                }
            try:
                if start_utc <= now < end_utc:
                    active_count_by_key[key] = int(active_count_by_key.get(key) or 0) + 1
                    names = active_names_by_key.setdefault(key, [])
                    for name in (w.get("sourceEventNames") or []):
                        n = str(name or "").strip()
                        if n and n not in names:
                            names.append(n)
            except Exception:
                pass
            cur = start_utc.astimezone(local_tz)
            end_local = end_utc.astimezone(local_tz)
            while cur < end_local:
                next_mid = (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                seg_end = min(end_local, next_mid)
                start_min = cur.hour * 60 + cur.minute
                end_min   = seg_end.hour * 60 + seg_end.minute
                if end_min == 0:
                    end_min = 1440
                # The dashboard timeline shows a rolling 7 local-day window.
                # Keep only those visible dates before merging by weekday so
                # names from a different calendar week cannot leak into a bar.
                if end_min > start_min and cur.date().isoformat() in visible_date_keys:
                    door_map[key]["windows"].append({
                        "day": cur.weekday(),  # 0=Mon … 6=Sun
                        "startMin": start_min,
                        "endMin": end_min,
                        "events": list(dict.fromkeys(w.get("sourceEventNames") or [])),
                    })
                cur = next_mid

        # Merge overlapping day-minute windows per door.
        # The sync window spans >7 days so the same day-of-week can appear from two
        # different calendar weeks.  Collapsing them here gives one unified bar per
        # day on the weekly timeline, with all contributing event names combined.
        for key in list(door_map.keys()):
            by_day: dict[int, list] = {}
            for w in door_map[key]["windows"]:
                by_day.setdefault(w["day"], []).append(w)
            merged_wins: list[dict] = []
            for day_wins in by_day.values():
                day_wins.sort(key=lambda w: w["startMin"])
                cur: dict | None = None
                for w in day_wins:
                    if cur is None:
                        cur = {**w, "events": list(w["events"])}
                    elif w["startMin"] <= cur["endMin"]:
                        cur["endMin"] = max(cur["endMin"], w["endMin"])
                        for ev in w["events"]:
                            if ev not in cur["events"]:
                                cur["events"].append(ev)
                    else:
                        merged_wins.append(cur)
                        cur = {**w, "events": list(w["events"])}
                if cur is not None:
                    merged_wins.append(cur)
            door_map[key]["windows"] = merged_wins

        # Ensure all configured doors appear (empty windows if none scheduled)
        doors_cfg = mapping_cfg.get("doors") or {}
        for dk in all_door_keys:
            if dk not in door_map:
                label = str((doors_cfg.get(dk) or {}).get("label") or dk)
                door_map[dk] = {
                    "key": dk,
                    "label": label,
                    "color": color_for_key.get(dk, _DOOR_COLORS[0]),
                    "windows": [],
                }
            names = active_names_by_key.get(dk) or []
            door_map[dk]["activeNow"] = {
                "isOpenBySchedule": bool(active_count_by_key.get(dk)),
                "windowCount": int(active_count_by_key.get(dk) or 0),
                "events": names,
            }
        for dk, row in door_map.items():
            if "activeNow" in row:
                continue
            names = active_names_by_key.get(dk) or []
            row["activeNow"] = {
                "isOpenBySchedule": bool(active_count_by_key.get(dk)),
                "windowCount": int(active_count_by_key.get(dk) or 0),
                "events": names,
            }
        # Return in mapping order so colors and display order stay consistent
        ordered = [door_map[dk] for dk in all_door_keys if dk in door_map]
        ordered += [v for dk, v in door_map.items() if dk not in all_door_keys]
        return {
            "timezone": settings.display_timezone,
            "now": now.isoformat(),
            "dayDatesByWeekday": day_dates_by_weekday,
            "doors": ordered,
        }

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

    @app.get("/api/schedule-board")
    async def api_schedule_board(days: int = 7, view: str = "all", q: str = "") -> dict:
        return await _build_schedule_board(days, view_key=view, query=q)

    @app.get("/api/config")
    async def api_config() -> dict:
        return {
            "applyToUnifi": sync_service.get_apply_to_unifi(),
            "syncCron": settings.sync_cron,
            "unifiBaseUrl": str(settings.unifi_access_base_url),
        }

    @app.get("/api/audit/recent")
    async def api_audit_recent(limit: int = 20) -> dict:
        limit = max(1, min(int(limit), 200))
        return {"entries": read_recent_audit_entries(audit_log_file, limit=limit)}

    @app.post("/api/config/apply")
    async def api_config_apply(request: Request, payload: dict = Body(...)) -> dict:
        value = bool(payload.get("applyToUnifi"))
        sync_service.set_apply_to_unifi(value)
        _audit(request, action="mode.set", target="applyToUnifi", note=f"apply={str(value).lower()}")
        return {"ok": True, "applyToUnifi": sync_service.get_apply_to_unifi()}

    @app.post("/dashboard/apply")
    async def dashboard_apply(request: Request) -> RedirectResponse:
        raw = (await request.body()).decode("utf-8", "ignore")
        apply = (parse_qs(raw).get("apply") or ["false"])[0]
        next_value = str(apply).lower() in ("1", "true", "yes", "on")
        sync_service.set_apply_to_unifi(next_value)
        _audit(request, action="mode.set", target="applyToUnifi", note=f"apply={str(next_value).lower()}")
        return RedirectResponse(url="/dashboard", status_code=303)

    @app.post("/api/sync/run")
    async def api_sync_run(request: Request) -> dict:
        try:
            await sync_service.run_once()
        except Exception as exc:
            _audit(request, action="sync.run", result="error", error=str(exc))
            raise
        _audit(request, action="sync.run")
        return {"ok": True}

    @app.get("/api/approvals/pending")
    async def api_approvals_pending() -> dict:
        return {"pending": sync_service.get_pending_approvals()}

    @app.post("/api/approvals/approve")
    async def api_approvals_approve(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        event_id = str(payload.get("id") or "").strip()
        if not event_id:
            _audit(request, action="approval.approve", result="error", error="id required")
            return JSONResponse(status_code=422, content={"ok": False, "error": "id required"})
        name = sync_service.approve_event(event_id)
        _audit(request, action="approval.approve", target=event_id, note=name or "")
        await _notify(request, f"Approved after-hours event: {name or event_id}")
        return {"ok": True, "name": name}

    @app.post("/api/approvals/deny")
    async def api_approvals_deny(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        event_id = str(payload.get("id") or "").strip()
        if not event_id:
            _audit(request, action="approval.deny", result="error", error="id required")
            return JSONResponse(status_code=422, content={"ok": False, "error": "id required"})
        name = sync_service.deny_event(event_id)
        _audit(request, action="approval.deny", target=event_id, note=name or "")
        await _notify(request, f"Denied after-hours event: {name or event_id}")
        return {"ok": True, "name": name}

    @app.get("/api/events/cancelled")
    async def api_events_cancelled() -> dict:
        return load_cancelled_events(settings.cancelled_events_file)

    @app.post("/api/events/cancel")
    async def api_events_cancel(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        event_id = str(payload.get("id") or "").strip()
        name = str(payload.get("name") or "").strip()
        start_at = str(payload.get("startAt") or "").strip()
        end_at = str(payload.get("endAt") or "").strip()
        if not event_id:
            _audit(request, action="event.cancel", result="error", error="id required")
            return JSONResponse(status_code=422, content={"ok": False, "error": "id required"})
        add_cancelled_event(settings.cancelled_events_file, event_id, name, start_at, end_at)
        _audit(request, action="event.cancel", target=event_id, note=name)
        await _notify(request, f"Cancelled event: {name or event_id}")
        return {"ok": True}

    @app.post("/api/events/restore")
    async def api_events_restore(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        event_id = str(payload.get("id") or "").strip()
        if not event_id:
            _audit(request, action="event.restore", result="error", error="id required")
            return JSONResponse(status_code=422, content={"ok": False, "error": "id required"})
        restored_name = ""
        for row in (load_cancelled_events(settings.cancelled_events_file).get("instances") or []):
            if str(row.get("id") or "") == event_id:
                restored_name = str(row.get("name") or "").strip()
                break
        remove_cancelled_event(settings.cancelled_events_file, event_id)
        _audit(request, action="event.restore", target=event_id, note=restored_name)
        await _notify(request, f"Restored cancelled event: {restored_name or event_id}")
        return {"ok": True}

    @app.get("/api/manual-access")
    async def api_manual_access() -> dict:
        return {"windows": list_manual_access(manual_access_file)}

    @app.post("/api/manual-access")
    async def api_manual_access_create(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse

        # Accept doorKeys array (primary) or legacy single doorKey
        raw_keys = payload.get("doorKeys")
        if raw_keys and isinstance(raw_keys, list):
            door_keys_raw = [str(k).strip() for k in raw_keys if str(k).strip()]
        else:
            single = str(payload.get("doorKey") or "").strip()
            door_keys_raw = [single] if single else []
        start_at = str(payload.get("startAt") or "").strip()
        end_at = str(payload.get("endAt") or "").strip()
        note = str(payload.get("note") or "").strip()
        override_approval = str(payload.get("overrideApproval") or "").strip().lower() in ("1", "true", "yes", "on")
        mapping = _read_mapping()
        doors_map = mapping.get("doors") or {}
        if not door_keys_raw:
            err = "Select a door or group"
            _audit(request, action="manual_access.create", result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        invalid_keys = [k for k in door_keys_raw if k not in doors_map]
        if invalid_keys:
            err = f"Unknown door key(s): {', '.join(invalid_keys)}"
            _audit(request, action="manual_access.create", result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        if not note:
            err = "Description is required"
            _audit(request, action="manual_access.create", result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        err = validate_manual_access_window(start_at=start_at, end_at=end_at, door_keys=door_keys_raw)
        if err:
            _audit(request, action="manual_access.create", target=",".join(door_keys_raw), result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        approval_reason = _manual_access_approval_reason(start_at, end_at)
        if approval_reason and not override_approval:
            _audit(
                request,
                action="manual_access.create",
                target=",".join(door_keys_raw),
                result="needs_approval",
                error=approval_reason,
            )
            return JSONResponse(
                status_code=409,
                content={"ok": False, "requiresApproval": True, "error": approval_reason},
            )
        entry = create_manual_access_entry(
            manual_access_file,
            door_keys=door_keys_raw,
            start_at=start_at,
            end_at=end_at,
            note=note,
        )
        door_labels = ", ".join(str((doors_map.get(k) or {}).get("label") or k) for k in door_keys_raw)
        approval_note = " [outside safe hours confirmed]" if approval_reason else ""
        _audit(
            request,
            action="manual_access.create",
            target=str(entry.get("id") or ""),
            note=f"{door_labels} {start_at} -> {end_at} ({note}){approval_note}",
        )
        sync_warning = ""
        try:
            await sync_service.run_once()
        except Exception as exc:
            sync_warning = str(exc)
        notify_msg = f"Quick Door Access set: {door_labels} — {note} ({start_at} → {end_at})"
        if approval_reason:
            notify_msg += " [outside safe hours confirmed]"
        if sync_warning:
            notify_msg += f" [sync warning: {sync_warning}]"
        await _notify(request, notify_msg)
        return {
            "ok": True,
            "entry": entry,
            "syncWarning": sync_warning,
            "approvedOutsideSafeHours": bool(approval_reason),
        }

    @app.post("/api/manual-access/update")
    async def api_manual_access_update(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse

        entry_id = str(payload.get("id") or "").strip()
        raw_keys = payload.get("doorKeys")
        if raw_keys and isinstance(raw_keys, list):
            door_keys_raw = [str(k).strip() for k in raw_keys if str(k).strip()]
        else:
            single = str(payload.get("doorKey") or "").strip()
            door_keys_raw = [single] if single else []
        start_at = str(payload.get("startAt") or "").strip()
        end_at = str(payload.get("endAt") or "").strip()
        note = str(payload.get("note") or "").strip()
        override_approval = str(payload.get("overrideApproval") or "").strip().lower() in ("1", "true", "yes", "on")
        if not entry_id:
            err = "id required"
            _audit(request, action="manual_access.update", result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        mapping = _read_mapping()
        doors_map = mapping.get("doors") or {}
        if not door_keys_raw:
            err = "Select a door or group"
            _audit(request, action="manual_access.update", target=entry_id, result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        invalid_keys = [k for k in door_keys_raw if k not in doors_map]
        if invalid_keys:
            err = f"Unknown door key(s): {', '.join(invalid_keys)}"
            _audit(request, action="manual_access.update", target=entry_id, result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        if not note:
            err = "Description is required"
            _audit(request, action="manual_access.update", target=entry_id, result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        err = validate_manual_access_window(start_at=start_at, end_at=end_at, door_keys=door_keys_raw)
        if err:
            _audit(request, action="manual_access.update", target=entry_id, result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        approval_reason = _manual_access_approval_reason(start_at, end_at)
        if approval_reason and not override_approval:
            _audit(
                request,
                action="manual_access.update",
                target=entry_id,
                result="needs_approval",
                error=approval_reason,
            )
            return JSONResponse(
                status_code=409,
                content={"ok": False, "requiresApproval": True, "error": approval_reason},
            )
        entry = update_manual_access_entry(
            manual_access_file,
            entry_id=entry_id,
            door_keys=door_keys_raw,
            start_at=start_at,
            end_at=end_at,
            note=note,
        )
        if entry is None:
            err = "Manual access window not found"
            _audit(request, action="manual_access.update", target=entry_id, result="error", error=err)
            return JSONResponse(status_code=404, content={"ok": False, "error": err})
        door_labels = ", ".join(str((doors_map.get(k) or {}).get("label") or k) for k in door_keys_raw)
        approval_note = " [outside safe hours confirmed]" if approval_reason else ""
        _audit(
            request,
            action="manual_access.update",
            target=entry_id,
            note=f"{door_labels} {start_at} -> {end_at} ({note}){approval_note}",
        )
        sync_warning = ""
        try:
            await sync_service.run_once()
        except Exception as exc:
            sync_warning = str(exc)
        notify_msg = f"Quick Door Access updated: {door_labels} — {note} ({start_at} → {end_at})"
        if approval_reason:
            notify_msg += " [outside safe hours confirmed]"
        if sync_warning:
            notify_msg += f" [sync warning: {sync_warning}]"
        await _notify(request, notify_msg)
        return {
            "ok": True,
            "entry": entry,
            "syncWarning": sync_warning,
            "approvedOutsideSafeHours": bool(approval_reason),
        }

    @app.post("/api/manual-access/cancel")
    async def api_manual_access_cancel(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse

        entry_id = str(payload.get("id") or "").strip()
        if not entry_id:
            err = "id required"
            _audit(request, action="manual_access.cancel", result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        removed = cancel_manual_access_entry(manual_access_file, entry_id)
        if removed is None:
            err = "Manual access window not found"
            _audit(request, action="manual_access.cancel", target=entry_id, result="error", error=err)
            return JSONResponse(status_code=404, content={"ok": False, "error": err})
        mapping = _read_mapping()
        doors_map = mapping.get("doors") or {}
        door_names = [
            str((doors_map.get(dk) or {}).get("label") or dk)
            for dk in (removed.get("doorKeys") or [])
        ]
        _removed_doors = ", ".join([n for n in door_names if n]) or "unknown"
        _removed_note = str(removed.get("note") or "")
        _audit(
            request,
            action="manual_access.cancel",
            target=entry_id,
            note=_removed_doors + (f" ({_removed_note})" if _removed_note else ""),
        )
        await _notify(request, f"Quick Door Access cancelled: {_removed_doors}" + (f" — {_removed_note}" if _removed_note else ""))
        sync_warning = ""
        try:
            await sync_service.run_once()
        except Exception as exc:
            sync_warning = str(exc)
        return {"ok": True, "syncWarning": sync_warning}

    @app.get("/api/exception-calendar")
    async def api_exception_calendar() -> dict:
        return {"entries": list_exception_entries(exception_calendar_file)}

    @app.post("/api/exception-calendar")
    async def api_exception_calendar_create(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse

        kind = str(payload.get("kind") or "").strip()
        from_date_str = str(payload.get("fromDate") or payload.get("date") or "").strip()
        to_date_str = str(payload.get("toDate") or from_date_str).strip()
        label = str(payload.get("label") or "").strip()
        note = str(payload.get("note") or "").strip()
        start_time = str(payload.get("startTime") or "").strip()
        end_time = str(payload.get("endTime") or "").strip()
        raw_keys = payload.get("doorKeys")
        door_keys = [str(k).strip() for k in raw_keys if str(k).strip()] if isinstance(raw_keys, list) else []

        mapping = _read_mapping()
        doors_map = mapping.get("doors") or {}
        invalid_keys = [k for k in door_keys if k not in doors_map]
        if invalid_keys:
            err = f"Unknown door key(s): {', '.join(invalid_keys)}"
            _audit(request, action="exception_calendar.create", result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})

        err = validate_exception_entry(
            kind=kind,
            from_date_str=from_date_str,
            to_date_str=to_date_str,
            door_keys=door_keys,
            label=label,
            start_time=start_time,
            end_time=end_time,
        )
        if err:
            _audit(request, action="exception_calendar.create", result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})

        entry = create_exception_entry(
            exception_calendar_file,
            kind=kind,
            from_date_str=from_date_str,
            to_date_str=to_date_str,
            door_keys=door_keys,
            label=label,
            note=note,
            start_time=start_time,
            end_time=end_time,
        )
        target_text = ", ".join(
            str((doors_map.get(k) or {}).get("label") or k)
            for k in (entry.get("doorKeys") or [])
        ) or "All doors"
        _audit(
            request,
            action="exception_calendar.create",
            target=str(entry.get("id") or ""),
            note=f"{kind} {from_date_str}..{to_date_str} {target_text} {label}",
        )
        sync_warning = ""
        try:
            await sync_service.run_once()
        except Exception as exc:
            sync_warning = str(exc)
        if from_date_str == to_date_str:
            notify_range = from_date_str
        else:
            notify_range = f"{from_date_str} to {to_date_str}"
        await _notify(request, f"Office Hours Calendar added: {label} ({kind} on {notify_range})")
        return {"ok": True, "entry": entry, "syncWarning": sync_warning}

    @app.post("/api/exception-calendar/delete")
    async def api_exception_calendar_delete(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse

        entry_id = str(payload.get("id") or "").strip()
        if not entry_id:
            err = "id required"
            _audit(request, action="exception_calendar.delete", result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})

        removed = delete_exception_entry(exception_calendar_file, entry_id)
        if removed is None:
            err = "Exception entry not found"
            _audit(request, action="exception_calendar.delete", target=entry_id, result="error", error=err)
            return JSONResponse(status_code=404, content={"ok": False, "error": err})

        _audit(
            request,
            action="exception_calendar.delete",
            target=entry_id,
            note=f"{str(removed.get('kind') or '')} {str(removed.get('fromDate') or removed.get('date') or '')}..{str(removed.get('toDate') or removed.get('fromDate') or removed.get('date') or '')} {str(removed.get('label') or '')}",
        )
        sync_warning = ""
        try:
            await sync_service.run_once()
        except Exception as exc:
            sync_warning = str(exc)
        await _notify(request, f"Office Hours Calendar removed: {str(removed.get('label') or entry_id)}")
        return {"ok": True, "syncWarning": sync_warning}

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

        def _fmt_hhmm(hhmm: str) -> str:
            """Convert '18:40' (24h) → '6:40 PM' for display."""
            try:
                h, m = hhmm.split(":")
                h, m = int(h), int(m)
                ampm = "AM" if h < 12 else "PM"
                return f"{h % 12 or 12}:{m:02d} {ampm}"
            except Exception:
                return hhmm

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
        cancelled_oh_dates = load_cancelled_office_hours(settings.cancelled_office_hours_file)

        try:
            _ov_data = load_event_overrides(settings.event_overrides_file)
            dash_overrides: dict = _ov_data.get("overrides") or {}
        except Exception:
            dash_overrides = {}

        try:
            _mem_data = load_event_memory(settings.event_memory_file)
            dash_mem_events: list = _mem_data.get("events") or []
        except Exception:
            dash_mem_events = []

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
        door_keys: list[str] = []
        doors_map: dict = {}
        if isinstance(mapping, dict):
            for i, dk in enumerate(list((mapping.get("doors") or {}).keys())):
                door_color_map[dk] = _DOOR_COLORS[i % len(_DOOR_COLORS)]
            door_keys = list((mapping.get("doors") or {}).keys())
            doors_map = mapping.get("doors") or {}

        manual_entries = list_manual_access(manual_access_file)
        door_groups_cfg: dict = (mapping.get("doorGroups") or {}) if isinstance(mapping, dict) else {}
        _indiv_opts = "".join(
            f'<option value="single:{_esc(dk, quote=True)}" data-keys="{_esc(dk, quote=True)}">'
            f'{_esc(str((doors_map.get(dk) or {}).get("label") or dk))}</option>'
            for dk in door_keys
        )
        _group_opts = "".join(
            f'<option value="group:{_esc(gk, quote=True)}"'
            f' data-keys="{_esc(",".join(str(k) for k in (gv.get("doorKeys") or [])), quote=True)}">'
            f'{_esc(str(gv.get("label") or gk))}</option>'
            for gk, gv in door_groups_cfg.items()
        )
        door_options_html = (
            (f'<optgroup label="Individual Doors">{_indiv_opts}</optgroup>' if _indiv_opts else "")
            + (f'<optgroup label="Door Groups">{_group_opts}</optgroup>' if _group_opts else "")
        )
        manual_rows = []
        for entry in manual_entries:
            entry_id = str(entry.get("id") or "")
            entry_doors = [
                str((doors_map.get(dk) or {}).get("label") or dk)
                for dk in (entry.get("doorKeys") or [])
            ]
            note_text = str(entry.get("note") or "").strip()
            note_html = _esc(note_text) if note_text else '<span style="color:#9ca3af">—</span>'
            edit_btn = (
                f'<button class="sm" data-id="{_esc(entry_id)}" '
                f'data-keys="{_esc(",".join(str(dk) for dk in (entry.get("doorKeys") or [])), quote=True)}" '
                f'data-start="{_esc(str(entry.get("startAt") or ""), quote=True)}" '
                f'data-end="{_esc(str(entry.get("endAt") or ""), quote=True)}" '
                f'data-note="{_esc(note_text, quote=True)}" '
                f'onclick="editManualAccess(this)">Edit</button>'
            )
            cancel_btn = (
                f'<button class="sm danger" data-id="{_esc(entry_id)}" style="margin-left:6px;" '
                f'onclick="cancelManualAccess(this)">Cancel</button>'
            )
            manual_rows.append(
                "<tr>"
                f'<td class="meta-cell" data-label="Door">{_esc(", ".join([d for d in entry_doors if d]) or "(unknown)")}</td>'
                f'<td class="time-cell" data-label="Start" style="white-space:nowrap">{_fmt_dt_cell(entry.get("startAt"))}</td>'
                f'<td class="time-cell" data-label="End" style="white-space:nowrap">{_fmt_dt_cell(entry.get("endAt"))}</td>'
                f'<td class="meta-cell" data-label="Description">{note_html}</td>'
                f'<td class="actions-cell" data-label="Actions">{edit_btn}{cancel_btn}</td>'
                "</tr>"
            )
        manual_rows_html = "\n".join(manual_rows)

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
            is_oh = e.get("type") == "office_hours"

            if is_oh:
                # Office hours: show configured door labels in color
                oh_door_keys: list[str] = e.get("doors") or []
                _door_spans = []
                for _dk in oh_door_keys:
                    _color = door_color_map.get(_dk, "#6b7280")
                    _lbl = str((_doors_cfg.get(_dk) or {}).get("label") or _dk)
                    _door_spans.append(f'<span style="color:{_color};font-weight:600">{_esc(_lbl)}</span>')
                doors_html = ", ".join(_door_spans) or '<span style="color:#9ca3af">(none mapped)</span>'
                rooms_str = _esc(str(e.get("timeRanges") or ""))
            else:
                _door_spans = []
                for _dk in event_doors.get(eid, []):
                    _color = door_color_map.get(_dk, "#6b7280")
                    _lbl = str((_doors_cfg.get(_dk) or {}).get("label") or _dk)
                    _door_spans.append(f'<span style="color:{_color};font-weight:600">{_esc(_lbl)}</span>')
                doors_html = ", ".join(_door_spans) or '<span style="color:#9ca3af">(none mapped)</span>'
                rooms_str = _esc(", ".join(e["rooms"]) if e.get("rooms") else str(e.get("room") or ""))

            ename = str(e.get("name") or "")
            estart = str(e.get("startAt") or "")
            eend = str(e.get("endAt") or "")

            if is_oh:
                oh_badge = (
                    ' <span style="font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px;'
                    'background:#d1fae5;color:#065f46;vertical-align:middle">Office Hours</span>'
                )
                date_str = _esc(str(e.get("dateStr") or ""))
                cancel_btn = (
                    f'<button class="sm danger" '
                    f'data-date="{date_str}" data-name="{_esc(ename)}" '
                    f'onclick="cancelOfficeHoursDay(this)">Cancel</button>'
                )
                events_rows_list.append(
                    '<tr style="background:#f0fdf4">'
                    f'<td class="time-cell" data-label="Start" style="white-space:nowrap">{_fmt_dt_cell(e.get("startAt"))}</td>'
                    f'<td class="time-cell" data-label="End" style="white-space:nowrap">{_fmt_dt_cell(e.get("endAt"))}</td>'
                    f'<td class="event-name-cell" data-label="Event"><strong>{_esc(ename)}</strong>{oh_badge}</td>'
                    f'<td class="hide-mob meta-cell" data-label="Room(s)">{rooms_str}</td>'
                    f'<td class="hide-mob meta-cell" data-label="Door Group(s)">{doors_html}</td>'
                    f'<td class="actions-cell" data-label="Actions" style="white-space:nowrap">{cancel_btn}</td>'
                    "</tr>"
                )
                continue

            ename_lower = ename.lower()
            ov_key = next((k for k in dash_overrides if k.lower() == ename_lower), None)
            has_override = ov_key is not None
            override_badge = (
                ' <span style="font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px;'
                'background:#dbeafe;color:#1d4ed8;vertical-align:middle">Override</span>'
                if has_override else ""
            )

            # Build per-door override time detail shown below the event name
            override_detail_html = ""
            if has_override:
                door_ovs = (dash_overrides[ov_key].get("doorOverrides") or {}) if ov_key else {}
                ov_lines = []
                for _dk, _ov in door_ovs.items():
                    _color = door_color_map.get(_dk, "#6b7280")
                    _lbl = _esc(str((doors_map.get(_dk) or {}).get("label") or _dk))
                    _wins = _ov.get("windows") or []
                    if not _wins:
                        # suppressed
                        ov_lines.append(
                            f'<span style="color:{_color}">&#9679;</span> '
                            f'<span style="color:#64748b">{_lbl}:</span> '
                            f'<span style="color:#94a3b8;font-style:italic">blocked</span>'
                        )
                    else:
                        _time_parts = []
                        for _w in _wins:
                            _o = _fmt_hhmm(_w.get("openTime") or "")
                            _c = _fmt_hhmm(_w.get("closeTime") or "")
                            _time_parts.append(
                                f'<span style="color:#059669">{_esc(_o)}</span>'
                                f'<span style="color:#64748b"> – </span>'
                                f'<span style="color:#dc2626">{_esc(_c)}</span>'
                            )
                        ov_lines.append(
                            f'<span style="color:{_color}">&#9679;</span> '
                            f'<span style="color:#64748b">{_lbl}:</span> '
                            + ' <span style="color:#cbd5e1">/</span> '.join(_time_parts)
                        )
                if ov_lines:
                    override_detail_html = (
                        '<div style="margin-top:5px;font-size:11px;line-height:1.9;'
                        'border-top:1px solid #e2e8f0;padding-top:5px">'
                        + "<br>".join(ov_lines)
                        + "</div>"
                    )

            override_btn_label = "Edit Override" if has_override else "Set Override"
            cancel_btn = (
                f'<button class="sm danger" '
                f'data-id="{_esc(eid)}" data-name="{_esc(ename)}" '
                f'data-start="{_esc(estart)}" data-end="{_esc(eend)}" '
                f'onclick="cancelEvent(this)">Cancel</button>'
            )
            override_btn = (
                f'<button class="sm" style="margin-top:4px" '
                f'data-name="{_esc(ename)}" data-start="{_esc(estart)}" data-end="{_esc(eend)}" '
                f'onclick="openOverrideModal(this)">&#9881; {_esc(override_btn_label)}</button>'
            )
            events_rows_list.append(
                "<tr>"
                f'<td class="time-cell" data-label="Start" style="white-space:nowrap">{_fmt_dt_cell(e.get("startAt"))}</td>'
                f'<td class="time-cell" data-label="End" style="white-space:nowrap">{_fmt_dt_cell(e.get("endAt"))}</td>'
                f'<td class="event-name-cell" data-label="Event"><strong>{_esc(ename)}</strong>{override_badge}{override_detail_html}</td>'
                f'<td class="hide-mob meta-cell" data-label="Room(s)">{rooms_str}</td>'
                f'<td class="hide-mob meta-cell" data-label="Door Group(s)">{doors_html}</td>'
                f'<td class="actions-cell" data-label="Actions" style="white-space:nowrap">{cancel_btn}<br/>{override_btn}</td>'
                "</tr>"
            )
        events_rows = "\n".join(events_rows_list)

        # Cancelled events warning card HTML
        # Build cancelled OH rows sorted by date
        from datetime import date as _date_type
        cancelled_oh_rows = []
        for _ds in sorted(cancelled_oh_dates):
            try:
                _d = _date_type.fromisoformat(_ds)
                _day_label = _d.strftime("%A, %b %-d")
            except Exception:
                _day_label = _ds
            restore_oh_btn = (
                f'<button class="sm" data-date="{_esc(_ds)}" '
                f'onclick="restoreOfficeHoursDay(this)">Restore</button>'
            )
            cancelled_oh_rows.append(
                f"<tr>"
                f'<td style="white-space:nowrap">{_esc(_day_label)}</td>'
                f'<td><strong>Office Hours</strong> '
                f'<span style="font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px;'
                f'background:#d1fae5;color:#065f46;vertical-align:middle">Office Hours</span></td>'
                f"<td>{restore_oh_btn}</td>"
                f"</tr>"
            )

        total_cancelled = len(cancelled_instances) + len(cancelled_oh_rows)
        if total_cancelled > 0:
            cancelled_rows = []
            for inst in cancelled_instances:
                iid = str(inst.get("id") or "")
                iname = _esc(str(inst.get("name") or ""))
                istart = _fmt_local(inst.get("startAt"))
                restore_btn = (
                    f'<button class="sm" data-id="{_esc(iid)}" data-name="{iname}" '
                    f'onclick="restoreEvent(this)">Restore</button>'
                )
                cancelled_rows.append(
                    f"<tr>"
                    f'<td style="white-space:nowrap">{istart}</td>'
                    f"<td><strong>{iname}</strong></td>"
                    f"<td>{restore_btn}</td>"
                    f"</tr>"
                )
            cancelled_rows_html = "\n".join(cancelled_rows) + "\n".join(cancelled_oh_rows)
            cancelled_card_html = f"""
    <div class="card" style="border-color:#fca5a5;background:#fff8f8;">
      <span class="card-title" style="color:#dc2626;">&#9888; Cancelled ({total_cancelled})</span>
      <p style="font-size:13px;color:#7f1d1d;margin:0 0 12px;">
        These are suppressed from the door schedule until restored.
      </p>
      <div style="overflow:auto;">
        <table>
          <thead>
            <tr><th>Date / Start</th><th>Event</th><th>Action</th></tr>
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

        audit_rows = []
        for entry in read_recent_audit_entries(audit_log_file, limit=12):
            ts = _fmt_local(entry.get("timestamp"))
            action = _esc(str(entry.get("action") or ""))
            target = _esc(str(entry.get("target") or "")) or '<span style="color:#9ca3af">—</span>'
            actor_name = _esc(str(entry.get("displayName") or entry.get("requestIp") or "unknown"))
            actor_ip = _esc(str(entry.get("requestIp") or ""))
            actor_html = actor_name
            if actor_ip and actor_ip != actor_name:
                actor_html += f'<div style="font-size:11px;color:#94a3b8;font-family:monospace">{actor_ip}</div>'
            note = _esc(str(entry.get("note") or "")) or '<span style="color:#9ca3af">—</span>'
            if entry.get("result") == "error":
                result_html = '<span class="badge badge-err">ERROR</span>'
                note = _esc(str(entry.get("error") or entry.get("note") or "")) or note
            else:
                result_html = '<span class="badge badge-apply" style="background:#dbeafe;color:#1d4ed8">OK</span>'
            audit_rows.append(
                "<tr>"
                f'<td style="white-space:nowrap">{_esc(ts or "")}</td>'
                f"<td>{action}</td>"
                f"<td>{target}</td>"
                f"<td>{actor_html}</td>"
                f"<td>{note}</td>"
                f"<td>{result_html}</td>"
                "</tr>"
            )
        audit_card_html = f"""
    <details class="collapsible" id="recentChangesDetails">
      <summary><span>Recent Changes</span></summary>
      <div class="details-body">
        <div class="table-wrap">
          <table>
            <thead><tr><th>When</th><th>Action</th><th>Target</th><th>Who</th><th>Detail</th><th>Result</th></tr></thead>
            <tbody>
              {"".join(audit_rows) or '<tr><td colspan="6" style="padding:12px;color:#9ca3af;">No operator changes logged yet.</td></tr>'}
            </tbody>
          </table>
        </div>
        <p style="margin:12px 0 0;font-size:13px;color:#64748b;">
          Friendly names come from <code>config/tailscale-peers.json</code>. Raw IPs are always retained in the audit log.
        </p>
      </div>
    </details>"""

        err_badge = f'<span class="badge badge-err" style="margin-left:8px">{error_count}</span>' if error_count else ""
        def _help_tip(text: str) -> str:
            tip = _esc(text)
            return (
                f'<span class="help-tip" tabindex="0" role="note" '
                f'aria-label="{tip}" data-tip="{tip}">?</span>'
            )

        help_quick_access = _help_tip(
            "Temporary unlock window for one door or group without editing PCO. "
            "Use for one-off needs, then cancel when finished."
        )
        help_door_status = _help_tip(
            "Live lock/sensor state from UniFi plus whether a schedule window is active right now."
        )
        help_upcoming = _help_tip(
            "Events currently mapped to doors. These drive the unlock windows sent to UniFi during sync."
        )
        help_sync_details = _help_tip(
            "Summary of the last sync run, result, mode, and counts."
        )
        help_recent_errors = _help_tip(
            "Most recent sync/API errors to help diagnose issues quickly."
        )
        help_pco_stats = _help_tip(
            "PCO API usage and cache stats for troubleshooting performance/rate limits."
        )
        help_room_mapping = _help_tip(
            "Maps PCO room names to door groups. If a room is unmapped, no doors unlock for that event."
        )

        html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Dashboard — PCO UniFi Sync</title>
{_PWA_HEAD}  <style>{_SHARED_CSS}
    .status-bar {{ display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
    .status-items {{ display: flex; align-items: center; gap: 16px; flex-wrap: wrap; flex: 1; min-width: 0; }}
    .status-item {{ display: flex; align-items: center; font-size: 14px; color: #374151; white-space: nowrap; }}
    .status-actions {{ display: flex; gap: 8px; flex-shrink: 0; }}
    .quick-access-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; align-items:end; }}
    .quick-access-grid label {{ font-size: 12px; color:#64748b; font-weight:600; display:block; margin-bottom:4px; }}
    .quick-access-grid input, .quick-access-grid select {{ width:100%; }}
    .event-count {{ font-size: 12px; color: #64748b; font-weight: 400; text-transform: none; letter-spacing: 0; margin-left: 6px; }}
    #doorStatusCard {{ overflow-anchor: none; }}
    #dsBody {{ min-height: 240px; }}
    .help-tip {{
      display:inline-flex; align-items:center; justify-content:center;
      width:16px; height:16px; border-radius:999px; margin-left:6px;
      border:1px solid #93c5fd; background:#dbeafe; color:#1e3a8a;
      font-size:11px; font-weight:800; line-height:1; cursor:help; position:relative;
      user-select:none; vertical-align:middle;
    }}
    .help-tip::before {{
      content:''; position:absolute; left:50%; top:calc(100% + 4px); transform:translateX(-50%);
      border:6px solid transparent; border-bottom-color:#0f172a; opacity:0; pointer-events:none;
      transition: opacity .12s ease;
    }}
    .help-tip::after {{
      content:attr(data-tip); position:absolute; left:50%; top:calc(100% + 10px); transform:translateX(-50%);
      width:max-content; min-width:220px; max-width:320px;
      background:#0f172a; color:#e2e8f0; border-radius:8px; padding:8px 10px;
      box-shadow:0 12px 30px rgba(2,6,23,.35); font-size:12px; font-weight:500; line-height:1.45;
      white-space:normal; opacity:0; pointer-events:none; z-index:10020; text-transform:none; letter-spacing:0;
      transition: opacity .12s ease;
    }}
    .help-tip:hover::before, .help-tip:focus-visible::before,
    .help-tip:hover::after, .help-tip:focus-visible::after {{ opacity:1; }}
    .show-mob {{ display: none; }}
    .events-wrap, .manual-wrap {{ overflow: visible; }}
    @media (max-width: 640px) {{
      .show-mob {{ display: inline; }}
      .status-bar {{ gap: 10px; }}
      .status-items {{ gap: 10px; width: 100%; }}
      .status-actions {{ width: 100%; }}
      .status-actions button, .status-actions form {{ flex: 1; }}
      .status-actions form button {{ width: 100%; }}
      .event-count {{ display: block; margin: 4px 0 0; }}
      #dsBody {{ min-height: 200px; }}
      .help-tip::after {{ min-width: 180px; max-width: min(280px, calc(100vw - 32px)); left:auto; right:0; transform:none; }}
      .help-tip::before {{ left:auto; right:6px; transform:none; }}
    }}
    .sched-grid {{ width: 100%; }}
    .sched-day-row {{
      --sched-row-bg: transparent;
      background: var(--sched-row-bg);
      border-radius: 10px;
      transition: background-color .14s ease, box-shadow .14s ease, background .14s ease;
    }}
    .sched-day-row.sched-day-clickable {{ cursor: pointer; }}
    .sched-day-row.sched-day-clickable:hover {{ background: linear-gradient(90deg, rgba(248,250,252,.92) 0%, rgba(241,245,249,.96) 100%); }}
    .sched-day-row.sched-day-today {{
      background: linear-gradient(90deg, rgba(219,234,254,.72) 0%, rgba(239,246,255,.94) 100%);
      box-shadow: inset 0 0 0 1px #dbeafe;
    }}
    .sched-day-row.sched-day-selected {{
      background: linear-gradient(90deg, rgba(191,219,254,.96) 0%, rgba(219,234,254,1) 100%);
      box-shadow: inset 0 0 0 1px #60a5fa;
    }}
    .sched-day-row.sched-day-selected .sched-lbl {{ color: #1d4ed8; font-weight: 700; }}
    .sched-lbl {{ width: 52px; flex-shrink: 0; font-size: 10px; color: #94a3b8; padding-right: 6px;
      display: flex; align-items: center; justify-content: flex-end; text-align: right; }}
    .sched-lbl-inner {{ display:flex; flex-direction:column; align-items:flex-end; line-height:1.05; gap:2px; }}
    .sched-lbl-day {{ font-size:10px; font-weight:700; color:#64748b; }}
    .sched-lbl-date {{ font-size:9px; color:#94a3b8; }}
    .sched-lbl-expanded {{ width: 78px; padding-right: 10px; }}
    .sched-lbl-expanded .sched-lbl-inner {{ gap: 3px; }}
    .sched-lbl-expanded .sched-lbl-day {{ font-size: 13px; }}
    .sched-lbl-expanded .sched-lbl-date {{ font-size: 11px; font-weight: 600; }}
    .sched-track {{ position: relative; flex: 1; border-left: 1px solid #e2e8f0; }}
    .sched-hr {{ position: absolute; font-size: 9px; color: #94a3b8; transform: translateX(-50%); top: 1px; user-select: none; }}
    .sched-vline {{ position: absolute; top: 0; bottom: 0; border-left: 1px solid #f3f4f6; }}
    .sched-vline-major {{ border-color: #e2e8f0; }}
    .sched-modal-overlay {{ position: fixed; inset: 0; background: rgba(0,0,0,0.45); z-index: 9999;
      display: none; align-items: center; justify-content: center; }}
    .sched-modal-overlay.open {{ display: flex; }}
    .sched-modal {{ background: white; border-radius: 12px; padding: 20px; max-width: 860px;
      width: 95%; max-height: 90vh; overflow: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }}
    .ov-modal-overlay {{ position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 9998;
      display: none; align-items: flex-start; justify-content: center; padding: 40px 12px; overflow-y: auto; }}
    .ov-modal-overlay.open {{ display: flex; }}
    .ov-modal {{ background: white; border-radius: 12px; padding: 24px; max-width: 800px;
      width: 100%; box-shadow: 0 20px 60px rgba(0,0,0,0.3); position: relative; }}
    .ov-modal h3 {{ margin: 0 0 4px; font-size: 17px; color: #1e293b; }}
    .ov-modal .ov-ref {{ font-size: 12px; color: #64748b; margin: 0 0 14px; line-height: 1.6; }}
    .ov-instructions {{ font-size: 12px; color: #475569; background: #f8fafc;
      border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px 12px; margin-bottom: 14px; line-height: 1.6; }}
    .ov-edit-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .ov-edit-table th {{ padding: 7px 8px; border-bottom: 2px solid #bfdbfe; font-size: 11px;
      color: #3b82f6; text-align: left; white-space: nowrap; }}
    .ov-edit-table td {{ padding: 7px 8px; border-bottom: 1px solid #dbeafe; vertical-align: middle; }}
    .ov-edit-table tbody tr:hover td {{ background: #f0f9ff; }}
    .ov-time-input {{ width: 72px; padding: 4px 6px; border: 1px solid #d1d5db; border-radius: 5px;
      font-size: 13px; font-family: monospace; }}
    .ov-time-input:disabled {{ background: #f9fafb; color: #9ca3af; }}
    .ov-actions {{ display: flex; gap: 8px; margin-top: 16px; flex-wrap: wrap; }}
    #sched-tip {{
      position: fixed; z-index: 10001; pointer-events: none; display: none;
      background: #1e293b; color: #f1f5f9; border-radius: 8px; padding: 10px 14px;
      font-size: 13px; line-height: 1.5; box-shadow: 0 4px 20px rgba(0,0,0,0.4);
      max-width: 280px; word-break: break-word;
    }}
    .stip-door {{ font-size: 10px; color: #94a3b8; font-weight: 700; text-transform: uppercase;
      letter-spacing: .06em; margin-bottom: 2px; }}
    .stip-time {{ font-size: 18px; font-weight: 700; color: #fff; margin-bottom: 8px; }}
    .stip-ev-header {{ font-size: 10px; color: #64748b; text-transform: uppercase;
      letter-spacing: .05em; margin-bottom: 4px; border-top: 1px solid rgba(255,255,255,.1);
      padding-top: 7px; }}
    .stip-ev {{ font-size: 12px; color: #cbd5e1; padding: 1px 0 1px 10px; position: relative; }}
    .stip-ev::before {{ content: '•'; position: absolute; left: 0; color: #60a5fa; }}
  </style>
</head>
<body>
  <div id="sched-tip"></div>

  <!-- Override Editor Modal -->
  <div id="ovModalOverlay" class="ov-modal-overlay" onclick="if(event.target===this)closeOverrideModal()">
    <div class="ov-modal">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px">
        <div>
          <h3 id="ovModalTitle">Event Override</h3>
          <div id="ovModalRef" class="ov-ref"></div>
        </div>
        <button onclick="closeOverrideModal()" style="background:none;border:none;font-size:22px;cursor:pointer;color:#94a3b8;line-height:1;padding:0 0 0 12px">✕</button>
      </div>
      <div class="ov-instructions">
        <strong>Checked + times filled</strong> → door opens at those exact times for this event only.&nbsp;
        <strong>Checked + times blank</strong> → door is <em>suppressed</em> (won't open for this event).&nbsp;
        <strong>Unchecked</strong> → door uses the global lead/lag default.
      </div>
      <div style="overflow:auto">
        <table class="ov-edit-table">
          <thead>
            <tr>
              <th>Door</th><th>Default Schedule</th><th style="text-align:center">Override?</th>
              <th>Window 1 Open</th><th>Window 1 Close</th>
              <th>Window 2 Open <span style="font-weight:normal;color:#93c5fd;font-size:10px">(opt)</span></th>
              <th>Window 2 Close <span style="font-weight:normal;color:#93c5fd;font-size:10px">(opt)</span></th>
            </tr>
          </thead>
          <tbody id="ovDoorRows"></tbody>
        </table>
      </div>
      <div class="ov-actions">
        <button class="primary" onclick="saveOverrideModal()">Save Override</button>
        <button class="danger" onclick="removeOverrideModal()">Remove Override</button>
        <button onclick="closeOverrideModal()">Cancel</button>
      </div>
    </div>
  </div>

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

    <details class="collapsible" id="quickAccessDetails">
      <summary>
        <span>Quick Door Access {help_quick_access}</span>
        {f'<span style="margin-left:8px;font-size:12px;font-weight:700;padding:1px 7px;border-radius:10px;background:#dbeafe;color:#1d4ed8;vertical-align:middle">{len(manual_entries)} active</span>' if manual_entries else ""}
      </summary>
      <div class="details-body">
        <p style="font-size:13px;color:#64748b;margin:0 0 12px;">
          Create a temporary unlock window for one door group without changing PCO or office hours. The picker uses this device's local time; times are shown in <strong>{_esc(settings.display_timezone)}</strong>.
        </p>
        <p style="font-size:12px;color:#94a3b8;margin:0 0 12px;">
          If the requested window falls outside your safe hours, the app will ask for one extra approval check before saving it.
        </p>
        <form id="quickAccessForm">
          <input id="qaEditId" type="hidden" />
          <div class="quick-access-grid">
            <div>
              <label for="qaDoor">Door Group</label>
              <select id="qaDoor" required>
                <option value="">Select a door or group…</option>
                {door_options_html}
              </select>
            </div>
            <div>
              <label for="qaStartAt">Start</label>
              <input id="qaStartAt" type="datetime-local" required />
            </div>
            <div>
              <label for="qaEndAt">End</label>
              <input id="qaEndAt" type="datetime-local" required />
            </div>
            <div>
              <label for="qaNote">Description <span style="color:#ef4444">*</span></label>
              <input id="qaNote" type="text" maxlength="120" placeholder="e.g. Special event – extra access needed" required />
            </div>
          </div>
          <div id="qaEditState" style="display:none;margin-top:10px;padding:8px 10px;border-radius:8px;background:#eff6ff;color:#1d4ed8;font-size:13px;font-weight:600;">
            Editing scheduled manual access window.
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px;">
            <button type="button" class="sm" onclick="setQuickAccessNow()">Start Now</button>
            <button type="button" class="sm" onclick="setQuickAccessDuration(15)">+15 min</button>
            <button type="button" class="sm" onclick="setQuickAccessDuration(30)">+30 min</button>
            <button type="button" class="sm" onclick="setQuickAccessDuration(60)">+60 min</button>
            <button type="button" class="sm" onclick="setQuickAccessDuration(120)">+120 min</button>
            <button id="qaSubmitBtn" type="submit" class="primary sm">Schedule Access</button>
            <button id="qaCancelEditBtn" type="button" class="sm" style="display:none;" onclick="clearQuickAccessEdit()">Cancel Edit</button>
          </div>
        </form>
      </div>
    </details>
    {"" if not manual_entries else f"""
    <div class="card" style="border-color:#bfdbfe;background:#eff6ff;padding:12px 16px;margin-top:-4px;">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
        <span style="font-weight:700;font-size:14px;color:#1e40af;">&#128274; Scheduled Manual Access</span>
        <span style="font-size:12px;font-weight:700;padding:1px 7px;border-radius:10px;background:#dbeafe;color:#1d4ed8;">{len(manual_entries)} window{"s" if len(manual_entries) != 1 else ""}</span>
      </div>
      <div class="table-wrap manual-wrap">
        <table class="mobile-cards-table manual-table">
          <thead><tr><th>Door</th><th>Start</th><th>End</th><th>Description</th><th>Action</th></tr></thead>
          <tbody>
            {manual_rows_html}
          </tbody>
        </table>
      </div>
    </div>"""}

    <!-- Door Status -->
    <div class="card" id="doorStatusCard">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;gap:8px;">
        <span class="card-title" style="margin:0">Door Status {help_door_status}</span>
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
        Upcoming Events {help_upcoming}
        <span class="event-count">· {evt_count} event{evt_plural} · {item_count} schedule item{item_plural}</span>
      </span>
      <div class="table-wrap events-wrap">
        <table class="mobile-cards-table events-table">
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

    {audit_card_html}

    <!-- Sync Details (collapsed) -->
    <details class="collapsible">
      <summary><span>Sync Details {help_sync_details}</span></summary>
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
      <summary><span>Recent Errors {help_recent_errors} {err_badge}</span></summary>
      <div class="details-body" style="font-size:13px;line-height:1.7;">
        {recent_errors_html}
      </div>
    </details>

    <!-- PCO API Stats (collapsed) -->
    <details class="collapsible">
      <summary><span>PCO API Stats {help_pco_stats}</span></summary>
      <div class="details-body">
        <div class="stat-grid">
          {pco_stats_items_html or '<div><div class="stat-val" style="color:#9ca3af">No stats yet.</div></div>'}
        </div>
      </div>
    </details>


    <!-- Room → Door Mapping (collapsed) -->
    <details class="collapsible">
      <summary><span>Room → Door Mapping {help_room_mapping}</span></summary>
      <div class="details-body">
        <div class="table-wrap">
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
    // Data for override editor modal
    const OV_OVERRIDES  = {json.dumps(dash_overrides)};
    const OV_MEM_EVENTS = {json.dumps(dash_mem_events)};
    const OV_DOOR_KEYS  = {json.dumps(door_keys)};
    const OV_DOORS_MAP  = {json.dumps(doors_map)};
    const OV_MAPPING    = {json.dumps(mapping if isinstance(mapping, dict) else {{}})};
    const OV_TZ         = {json.dumps(settings.display_timezone)};

    const _toast = () => document.getElementById('dash-toast');
    function _showToast(msg, ok) {{
      const t = _toast();
      t.textContent = msg;
      t.style.background = ok ? '#059669' : '#dc2626';
      t.style.display = 'block';
      setTimeout(() => {{ t.style.display = 'none'; }}, ok ? 2500 : 4000);
    }}

    function _toInputValue(date) {{
      const pad = (n) => String(n).padStart(2, '0');
      return date.getFullYear()
        + '-' + pad(date.getMonth() + 1)
        + '-' + pad(date.getDate())
        + 'T' + pad(date.getHours())
        + ':' + pad(date.getMinutes());
    }}

    function _fromIsoToInputValue(isoValue) {{
      const dt = new Date(isoValue);
      if (Number.isNaN(dt.getTime())) return '';
      return _toInputValue(dt);
    }}

    function _normalizeDoorKeys(keys) {{
      return [...keys].filter(Boolean).sort().join(',');
    }}

    function _findDoorOptionForKeys(keys) {{
      const normalized = _normalizeDoorKeys(keys);
      const sel = document.getElementById('qaDoor');
      for (const opt of sel.options) {{
        const optionKeys = (opt.dataset.keys || '').split(',').filter(Boolean);
        if (_normalizeDoorKeys(optionKeys) === normalized) return opt.value;
      }}
      return '';
    }}

    function clearQuickAccessEdit() {{
      document.getElementById('qaEditId').value = '';
      document.getElementById('qaEditState').style.display = 'none';
      document.getElementById('qaSubmitBtn').textContent = 'Schedule Access';
      document.getElementById('qaCancelEditBtn').style.display = 'none';
      document.getElementById('quickAccessForm').reset();
      setQuickAccessNow();
      setQuickAccessDuration(30);
    }}

    function editManualAccess(btn) {{
      const details = document.getElementById('quickAccessDetails');
      if (details && !details.open) {{
        details.open = true;
      }}
      const optionValue = _findDoorOptionForKeys((btn.dataset.keys || '').split(',').filter(Boolean));
      if (!optionValue) {{
        _showToast('This entry uses a door combination that cannot be edited from the current dropdown.', false);
        return;
      }}
      document.getElementById('qaEditId').value = btn.dataset.id || '';
      document.getElementById('qaDoor').value = optionValue;
      document.getElementById('qaStartAt').value = _fromIsoToInputValue(btn.dataset.start || '');
      document.getElementById('qaEndAt').value = _fromIsoToInputValue(btn.dataset.end || '');
      document.getElementById('qaNote').value = btn.dataset.note || '';
      document.getElementById('qaEditState').style.display = 'block';
      document.getElementById('qaSubmitBtn').textContent = 'Update Access';
      document.getElementById('qaCancelEditBtn').style.display = 'inline-flex';
      setTimeout(() => {{
        const form = document.getElementById('quickAccessForm');
        if (form) {{
          form.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
        }}
        document.getElementById('qaNote').focus();
      }}, details && details.open ? 140 : 0);
    }}

    function setQuickAccessNow() {{
      document.getElementById('qaStartAt').value = _toInputValue(new Date());
    }}

    function setQuickAccessDuration(minutes) {{
      const startEl = document.getElementById('qaStartAt');
      const endEl = document.getElementById('qaEndAt');
      let start = startEl.value ? new Date(startEl.value) : new Date();
      if (Number.isNaN(start.getTime())) start = new Date();
      endEl.value = _toInputValue(new Date(start.getTime() + minutes * 60000));
    }}

    async function saveQuickAccessRequest(url, payload) {{
      const resp = await fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(payload),
      }});
      const data = await resp.json();
      if (data && data.requiresApproval) {{
        return {{ needsApproval: true, data }};
      }}
      if (!resp.ok || data.error) throw new Error(data.error || ('HTTP ' + resp.status));
      return {{ needsApproval: false, data }};
    }}

    async function submitQuickAccess(event) {{
      event.preventDefault();
      const sel = document.getElementById('qaDoor');
      const selVal = sel.value;
      let doorKeys = [];
      if (selVal.startsWith('single:')) {{
        doorKeys = [selVal.slice(7)];
      }} else if (selVal.startsWith('group:')) {{
        const opt = sel.options[sel.selectedIndex];
        doorKeys = (opt.dataset.keys || '').split(',').filter(Boolean);
      }}
      const startValue = document.getElementById('qaStartAt').value;
      const endValue = document.getElementById('qaEndAt').value;
      const note = (document.getElementById('qaNote').value || '').trim();
      const editId = document.getElementById('qaEditId').value;
      if (!doorKeys.length || !startValue || !endValue) {{
        _showToast('Door, start, and end are required.', false);
        return;
      }}
      if (!note) {{
        _showToast('Description is required.', false);
        document.getElementById('qaNote').focus();
        return;
      }}
      const startAt = new Date(startValue);
      const endAt = new Date(endValue);
      if (Number.isNaN(startAt.getTime()) || Number.isNaN(endAt.getTime())) {{
        _showToast('Enter valid start and end times.', false);
        return;
      }}
      try {{
        const url = editId ? '/api/manual-access/update' : '/api/manual-access';
        let result = await saveQuickAccessRequest(url, {{
          id: editId || undefined,
          doorKeys,
          startAt: startAt.toISOString(),
          endAt: endAt.toISOString(),
          note,
        }});
        if (result.needsApproval) {{
          const approvalMessage = result.data.error || 'This manual access window falls outside safe hours and needs approval.';
          const confirmMsg = approvalMessage + '\\n\\nApprove and save this manual access window?';
          if (!confirm(confirmMsg)) return;
          result = await saveQuickAccessRequest(url, {{
            id: editId || undefined,
            doorKeys,
            startAt: startAt.toISOString(),
            endAt: endAt.toISOString(),
            note,
            overrideApproval: true,
          }});
        }}
        const data = result.data;
        if (data.syncWarning) {{
          _showToast((editId ? 'Updated' : 'Saved') + ', but sync failed: ' + data.syncWarning, false);
          setTimeout(() => location.reload(), 2200);
        }} else if (data.approvedOutsideSafeHours) {{
          _showToast(editId ? 'Outside safe hours confirmed and updated.' : 'Outside safe hours confirmed and scheduled.', true);
          setTimeout(() => location.reload(), 1200);
        }} else {{
          _showToast(editId ? 'Temporary door access updated.' : 'Temporary door access scheduled.', true);
          setTimeout(() => location.reload(), 1200);
        }}
      }} catch (err) {{
        _showToast((editId ? 'Quick access update failed: ' : 'Quick access failed: ') + err.message, false);
      }}
    }}

    async function cancelManualAccess(btn) {{
      const id = btn.dataset.id;
      if (!confirm('Cancel this temporary door access window?')) return;
      btn.disabled = true;
      try {{
        const resp = await fetch('/api/manual-access/cancel', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{ id }}),
        }});
        const data = await resp.json();
        if (!resp.ok || data.error) throw new Error(data.error || ('HTTP ' + resp.status));
        if (data.syncWarning) {{
          _showToast('Canceled, but sync failed: ' + data.syncWarning, false);
          setTimeout(() => location.reload(), 2200);
        }} else {{
          _showToast('Temporary door access canceled.', true);
          setTimeout(() => location.reload(), 1200);
        }}
      }} catch (err) {{
        _showToast('Cancel failed: ' + err.message, false);
        btn.disabled = false;
      }}
    }}

    document.getElementById('quickAccessForm').addEventListener('submit', submitQuickAccess);
    setQuickAccessNow();
    setQuickAccessDuration(30);

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

    async function cancelOfficeHoursDay(btn) {{
      const date = btn.dataset.date, name = 'Office Hours on ' + date;
      if (!confirm('Cancel ' + name + '? The doors will not open for office hours that day.')) return;
      btn.disabled = true;
      try {{
        const resp = await fetch('/api/office-hours/cancel', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{ date }}),
        }});
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        _showToast('Office Hours cancelled for ' + date + '.', true);
        setTimeout(() => location.reload(), 1500);
      }} catch (err) {{
        _showToast('Cancel failed: ' + err.message, false);
        btn.disabled = false;
      }}
    }}

    async function restoreOfficeHoursDay(btn) {{
      const date = btn.dataset.date;
      btn.disabled = true;
      try {{
        const resp = await fetch('/api/office-hours/restore', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{ date }}),
        }});
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        _showToast('Office Hours restored for ' + date + '.', true);
        setTimeout(() => location.reload(), 1500);
      }} catch (err) {{
        _showToast('Restore failed: ' + err.message, false);
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
    let _schedSelectedDay = null;

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

    function rerenderSchedViews() {{
      if (!_lastSchedData) return;
      renderDoorStatus(_lastSchedData.status, _lastSchedData.sched);
      const overlay = document.getElementById('schedModalOverlay');
      if (overlay && overlay.classList.contains('open')) {{
        openSchedModal();
      }}
    }}

    function onSchedDayClick(event, day, mode) {{
      event.stopPropagation();
      _schedSelectedDay = (_schedSelectedDay === day) ? null : day;
      if (mode === 'open') {{
        renderDoorStatus(_lastSchedData.status, _lastSchedData.sched);
        openSchedModal();
        return;
      }}
      rerenderSchedViews();
    }}

    // Build a reusable schedule grid (compact or expanded)
    function buildSchedGrid(doors, schedData, opts) {{
      const laneH = opts.laneH, labelH = opts.labelH, hourStep = opts.hourStep,
            showLabels = opts.showLabels, altBg = opts.altBg, dayClickMode = opts.dayClickMode || '',
            expandedLabels = !!opts.expandedLabels;
      const DAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
      const dayDatesByWeekday = (schedData && schedData.dayDatesByWeekday) || {{}};
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
        const dayMeta = dayDatesByWeekday[day] || {{
          dayLabel: DAYS[day],
          dateLabel: '',
          isToday: false
        }};
        const rowClasses = ['sched-day-row'];
        if (dayClickMode) rowClasses.push('sched-day-clickable');
        if (dayMeta.isToday) rowClasses.push('sched-day-today');
        if (day === _schedSelectedDay) rowClasses.push('sched-day-selected');
        const bg = (altBg && day%2===0) ? '#f8fafc' : 'transparent';
        const clickAttr = dayClickMode
          ? ' onclick="onSchedDayClick(event,' + day + ',\\'' + dayClickMode + '\\')"'
          : '';
        const labelClass = expandedLabels ? 'sched-lbl sched-lbl-expanded' : 'sched-lbl';
        const labelHtml = '<div class="sched-lbl-inner">'
          + '<span class="sched-lbl-day">' + dayMeta.dayLabel + '</span>'
          + (dayMeta.dateLabel ? '<span class="sched-lbl-date">' + dayMeta.dateLabel + '</span>' : '')
          + '</div>';
        html += '<div class="' + rowClasses.join(' ') + '" style="display:flex;height:' + rowH + 'px;--sched-row-bg:' + bg + ';"' + clickAttr + '>'
          + '<div class="' + labelClass + '" style="height:' + rowH + 'px">' + labelHtml + '</div>'
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
            const evts = (win.events||[]).map(function(s){{ return s.trim(); }}).filter(Boolean);
            const evtNames = evts.join(', ');
            const _tid = 'b' + (++window._schedBarId);
            window._schedTips[_tid] = {{door: door.label, time: tStr, evts: evts, color: door.color}};
            html += '<div data-tip-id="' + _tid + '"'
              + ' onmouseenter="showSchedTip(this,event)"'
              + ' onmousemove="moveSchedTip(event)"'
              + ' onmouseleave="hideSchedTip()"'
              + ' style="position:absolute;left:' + l + '%;width:' + w
              + '%;top:' + t + 'px;height:' + bh
              + 'px;border-radius:2px;background:' + door.color
              + ';opacity:0.85;overflow:hidden;min-width:3px;cursor:default">';
            if (showLabels) {{
              const barLabel = evtNames ? tStr + '  ' + evtNames : tStr;
              html += '<span style="position:absolute;left:4px;top:50%;transform:translateY(-50%);'
                + 'font-size:10px;color:white;white-space:nowrap;font-weight:600">' + barLabel + '</span>';
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

    function buildActiveNowMap(schedData) {{
      const out = {{}};
      for (const d of (schedData.doors||[])) {{
        out[d.key] = d.activeNow || {{isOpenBySchedule:false, windowCount:0, events:[]}};
      }}
      return out;
    }}

    function buildDoorReason(live, pos, activeNow) {{
      const active = activeNow || {{isOpenBySchedule:false, events:[]}};
      const evtNames = (active.events||[]).map(s => String(s||'').trim()).filter(Boolean);
      const evtText = evtNames.join(', ');

      if (live === 'UNLOCKED' && active.isOpenBySchedule) {{
        return evtText
          ? ('Open by schedule: ' + evtText)
          : 'Open by schedule right now';
      }}
      if (live === 'UNLOCKED' && !active.isOpenBySchedule) {{
        return 'Unlocked outside this schedule (manual unlock or another UniFi schedule/policy)';
      }}
      if (live === 'LOCKED' && active.isOpenBySchedule) {{
        const prefix = evtText
          ? ('Scheduled open now for: ' + evtText)
          : 'Scheduled open now';
        return prefix + ' (but currently locked). Check UniFi door settings if this persists.';
      }}
      if (live === 'LOCKED' && pos === 'OPEN') {{
        return 'Door is physically open, but lock relay reports locked';
      }}
      if (live === 'LOCKED') {{
        return 'No active schedule window right now';
      }}
      return 'Live state unavailable';
    }}

    // Build the legend HTML (used in both card and modal)
    function buildLegend(doors, liveByKey, idByKey, positionByKey, activeNowByKey, closeModalOnLock) {{
      let html = '';
      for (const d of doors) {{
        const live = liveByKey[d.key]||'UNKNOWN';
        const isUnlocked=live==='UNLOCKED', isUnknown=live==='UNKNOWN';
        const doorId=idByKey[d.key];
        const pos=positionByKey[d.key]||'UNKNOWN';
        const activeNow = activeNowByKey[d.key] || {{isOpenBySchedule:false, events:[]}};
        const reason = buildDoorReason(live, pos, activeNow);

        // Lock/unlock badge
        const lockBg  = isUnknown?'#e2e8f0':(isUnlocked?'#dcfce7':'#fee2e2');
        const lockClr = isUnknown?'#475569':(isUnlocked?'#166534':'#991b1b');
        const lockTxt = isUnknown?'?':(isUnlocked?'Unlocked':'Locked');
        const lockCb = closeModalOnLock?'lockDoor(this);closeSchedModal()':'lockDoor(this)';
        const lockAttrs = (isUnlocked&&doorId)
          ? ' data-door-id="'+doorId+'" data-label="'+d.label+'" onclick="'+lockCb+'" style="cursor:pointer" title="Click to lock"'
          : '';

        // Door position badge (physical sensor)
        const posBadge = (pos==='UNKNOWN') ? ''
          : '<span style="font-size:10px;font-weight:600;padding:2px 6px;border-radius:4px;background:'
            +(pos==='OPEN'?'#fef3c7':'#e2e8f0')+';color:'
            +(pos==='OPEN'?'#92400e':'#334155')+'">'
            +(pos==='OPEN'?'Door Open':'Door Closed')+'</span>';

        const reasonEsc = reason.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
        const reasonTitle = reasonEsc.replaceAll('"', '&quot;');
        html += '<div style="display:flex;align-items:center;gap:5px;min-width:0;margin-bottom:2px;">'
          + '<span style="width:10px;height:10px;border-radius:2px;background:'+d.color+';flex-shrink:0"></span>'
          + '<span style="font-size:12px;font-weight:600;color:#1e293b">'+d.label+'</span>'
          + '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:'+lockBg+';color:'+lockClr+'"'+lockAttrs+'>'+lockTxt+'</span>'
          + posBadge
          + '<span title="'+reasonTitle+'" style="font-size:11px;color:#64748b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1">'
          + reasonEsc
          + '</span>'
          + '</div>';
      }}
      return html;
    }}

    function renderDoorStatus(statusData, schedData) {{
      const body   = document.getElementById('dsBody');
      const timeEl = document.getElementById('dsTime');
      const doors  = schedData.doors||[];
      const {{liveByKey, idByKey, positionByKey}} = buildLiveMaps(statusData);
      const activeNowByKey = buildActiveNowMap(schedData);

      const legend = '<div style="display:flex;flex-direction:column;gap:5px;margin-bottom:10px">'
        + buildLegend(doors, liveByKey, idByKey, positionByKey, activeNowByKey, false) + '</div>';

      const grid = '<div onclick="openSchedModal()" style="cursor:pointer" title="Click for detail">'
        + buildSchedGrid(doors, schedData, {{laneH:5, labelH:12, hourStep:4, showLabels:false, altBg:true, dayClickMode:'open'}})
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
      const activeNowByKey = buildActiveNowMap(sch);

      let overlay = document.getElementById('schedModalOverlay');
      if (!overlay) {{
        overlay = document.createElement('div');
        overlay.id = 'schedModalOverlay';
        overlay.className = 'sched-modal-overlay';
        overlay.addEventListener('click', function(e) {{ if (e.target===overlay) closeSchedModal(); }});
        document.body.appendChild(overlay);
      }}

      const legend = '<div style="display:flex;flex-direction:column;gap:7px;margin-bottom:16px">'
        + buildLegend(doors, liveByKey, idByKey, positionByKey, activeNowByKey, true) + '</div>';

      overlay.innerHTML = '<div class="sched-modal">'
        + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">'
        + '<strong style="font-size:15px;color:#1e293b">Door Schedule — Next 7 Days</strong>'
        + '<button onclick="closeSchedModal()" style="background:none;border:none;font-size:20px;cursor:pointer;color:#94a3b8;line-height:1">✕</button>'
        + '</div>'
        + legend
        + '<div style="font-size:11px;color:#94a3b8;margin-bottom:10px">Times in ' + sch.timezone + '</div>'
        + buildSchedGrid(doors, sch, {{laneH:22, labelH:18, hourStep:2, showLabels:true, altBg:true, dayClickMode:'select', expandedLabels:true}})
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

    // ── Override Editor Modal ────────────────────────────────────────────────
    let _ovCurrentName = null;

    function _ovFmtTime(iso) {{
      if (!iso) return '';
      try {{
        return new Intl.DateTimeFormat('en-US', {{
          timeZone: OV_TZ, hour: 'numeric', minute: '2-digit', hour12: true,
        }}).format(new Date(iso));
      }} catch(e) {{ return iso; }}
    }}

    function _ovFmtDate(iso) {{
      if (!iso) return '';
      try {{
        return new Intl.DateTimeFormat('en-US', {{
          timeZone: OV_TZ, weekday: 'short', month: 'short', day: 'numeric',
        }}).format(new Date(iso));
      }} catch(e) {{ return iso; }}
    }}

    function openOverrideModal(btn) {{
      const eventName = btn.dataset.name;
      const evtStart  = btn.dataset.start || null;
      const evtEnd    = btn.dataset.end   || null;
      _ovCurrentName  = eventName;

      document.getElementById('ovModalTitle').textContent = eventName;

      const memEvt = OV_MEM_EVENTS.find(e => (e.name||'').toLowerCase() === eventName.toLowerCase()) || {{}};
      const eventRooms = memEvt.rooms || [];
      const roomsMap   = (OV_MAPPING && OV_MAPPING.rooms) || {{}};
      const applicableDoors = new Set();
      for (const room of eventRooms) {{
        for (const dk of (roomsMap[room] || [])) applicableDoors.add(dk);
      }}

      const refStart = evtStart || memEvt.nextAt || memEvt.lastSeenAt || null;
      const refEnd   = evtEnd   || memEvt.nextEndAt || memEvt.lastEndAt || null;
      const defaults = (OV_MAPPING && OV_MAPPING.defaults) || {{}};
      const leadMins = defaults.unlockLeadMinutes || 15;
      const lagMins  = defaults.unlockLagMinutes  || 15;
      let normOpen = null, normClose = null;
      if (refStart) normOpen  = new Date(new Date(refStart).getTime() - leadMins * 60000).toISOString();
      if (refEnd)   normClose = new Date(new Date(refEnd).getTime()   + lagMins  * 60000).toISOString();

      const refEl = document.getElementById('ovModalRef');
      if (refStart) {{
        let info = `<strong>${{_ovFmtDate(refStart)}}</strong>`;
        info += ` &nbsp;·&nbsp; <strong>Event:</strong> ${{_ovFmtTime(refStart)}}`;
        if (refEnd) info += ` – ${{_ovFmtTime(refEnd)}}`;
        if (normOpen && normClose) info += ` &nbsp;·&nbsp; <strong>Default doors:</strong> ${{_ovFmtTime(normOpen)}} – ${{_ovFmtTime(normClose)}} <span style="color:#9ca3af">(${{leadMins}} min before / ${{lagMins}} min after)</span>`;
        refEl.innerHTML = info;
      }} else {{
        refEl.textContent = '';
      }}

      const nameLower = eventName.toLowerCase();
      const ovKey = Object.keys(OV_OVERRIDES).find(k => k.toLowerCase() === nameLower);
      const doorOverrides = ovKey ? ((OV_OVERRIDES[ovKey] || {{}}).doorOverrides || {{}}) : {{}};

      function buildDoorRow(dk, isApplicable) {{
        let defCell = '';
        if (isApplicable && normOpen && normClose) {{
          defCell = `<span style="color:#059669;font-size:12px">Opens</span> <strong>${{_ovFmtTime(normOpen)}}</strong><br><span style="color:#dc2626;font-size:12px">Closes</span> <strong>${{_ovFmtTime(normClose)}}</strong>`;
        }} else if (!isApplicable) {{
          defCell = `<span style="color:#d1d5db;font-size:12px">Not in this event</span>`;
        }} else {{
          defCell = `<span style="color:#9ca3af;font-size:12px">Times unknown</span>`;
        }}
        const doorCfg = doorOverrides.hasOwnProperty(dk) ? doorOverrides[dk] : null;
        const wins = doorCfg ? (doorCfg.windows || []) : [];
        const w1 = wins[0] || {{}}, w2 = wins[1] || {{}};
        const label = (OV_DOORS_MAP[dk] && OV_DOORS_MAP[dk].label) ? OV_DOORS_MAP[dk].label : dk;
        const chk = doorCfg !== null ? 'checked' : '';
        const dis = doorCfg !== null ? '' : 'disabled';
        const rowStyle = isApplicable ? '' : 'opacity:0.4';
        return `<tr style="${{rowStyle}}">
          <td><strong>${{label}}</strong><br><span style="font-size:11px;color:#6b7280">${{dk}}</span></td>
          <td>${{defCell}}</td>
          <td style="text-align:center"><input type="checkbox" id="ovChk_${{dk}}" ${{chk}} onchange="ovToggleDoor('${{dk}}')" style="width:18px;height:18px;cursor:pointer;accent-color:#2563eb"></td>
          <td><input type="text" id="ovO1_${{dk}}" value="${{w1.openTime||''}}" placeholder="HH:MM" class="ov-time-input" ${{dis}}></td>
          <td><input type="text" id="ovC1_${{dk}}" value="${{w1.closeTime||''}}" placeholder="HH:MM" class="ov-time-input" ${{dis}}></td>
          <td><input type="text" id="ovO2_${{dk}}" value="${{w2.openTime||''}}" placeholder="HH:MM" class="ov-time-input" ${{dis}}></td>
          <td><input type="text" id="ovC2_${{dk}}" value="${{w2.closeTime||''}}" placeholder="HH:MM" class="ov-time-input" ${{dis}}></td>
        </tr>`;
      }}

      const appKeys   = OV_DOOR_KEYS.filter(dk => applicableDoors.has(dk));
      const otherKeys = OV_DOOR_KEYS.filter(dk => !applicableDoors.has(dk));
      let rows = appKeys.map(dk => buildDoorRow(dk, true)).join('');
      if (otherKeys.length) {{
        rows += `<tr><td colspan="7" style="padding:5px 8px;font-size:11px;color:#9ca3af;background:#f9fafb;border-top:2px solid #e5e7eb;text-transform:uppercase;letter-spacing:.05em">Other doors — not in this event's rooms</td></tr>`;
        rows += otherKeys.map(dk => buildDoorRow(dk, false)).join('');
      }}
      document.getElementById('ovDoorRows').innerHTML = rows;
      document.getElementById('ovModalOverlay').className = 'ov-modal-overlay open';
    }}

    function closeOverrideModal() {{
      _ovCurrentName = null;
      document.getElementById('ovModalOverlay').className = 'ov-modal-overlay';
    }}

    function ovToggleDoor(dk) {{
      const chk = document.getElementById('ovChk_' + dk);
      ['ovO1_','ovC1_','ovO2_','ovC2_'].forEach(p => {{
        const el = document.getElementById(p + dk);
        if (el) el.disabled = !chk.checked;
      }});
      if (chk.checked) {{
        const first = document.getElementById('ovO1_' + dk);
        if (first && !first.value) first.focus();
      }}
    }}

    async function saveOverrideModal() {{
      if (!_ovCurrentName) return;
      const timeRe = /^\\d{{1,2}}:\\d{{2}}$/;
      const doorOverrides = {{}};
      for (const dk of OV_DOOR_KEYS) {{
        const chk = document.getElementById('ovChk_' + dk);
        if (!chk || !chk.checked) continue;
        const o1 = (document.getElementById('ovO1_' + dk).value || '').trim();
        const c1 = (document.getElementById('ovC1_' + dk).value || '').trim();
        const o2 = (document.getElementById('ovO2_' + dk).value || '').trim();
        const c2 = (document.getElementById('ovC2_' + dk).value || '').trim();
        const label = (OV_DOORS_MAP[dk] && OV_DOORS_MAP[dk].label) ? OV_DOORS_MAP[dk].label : dk;
        if (!o1 && !c1 && !o2 && !c2) {{ doorOverrides[dk] = {{ windows: [] }}; continue; }}
        if (!timeRe.test(o1) || !timeRe.test(c1)) {{
          _showToast('Invalid Window 1 time for ' + label + '. Use HH:MM (24h) or leave blank to suppress.', false);
          return;
        }}
        const windows = [{{ openTime: o1, closeTime: c1 }}];
        if (o2 || c2) {{
          if (!timeRe.test(o2) || !timeRe.test(c2)) {{
            _showToast('Invalid Window 2 time for ' + label + '. Use HH:MM or leave both blank.', false);
            return;
          }}
          windows.push({{ openTime: o2, closeTime: c2 }});
        }}
        doorOverrides[dk] = {{ windows }};
      }}
      if (!Object.keys(doorOverrides).length) {{
        _showToast('Check at least one door, or use Remove Override to clear.', false);
        return;
      }}
      const newOverrides = Object.assign({{}}, OV_OVERRIDES);
      const existingKey  = Object.keys(newOverrides).find(k => k.toLowerCase() === _ovCurrentName.toLowerCase());
      newOverrides[existingKey || _ovCurrentName] = {{ doorOverrides }};
      await _ovPost(newOverrides, 'Override saved.');
    }}

    async function removeOverrideModal() {{
      if (!_ovCurrentName) return;
      const newOverrides = Object.assign({{}}, OV_OVERRIDES);
      const existingKey  = Object.keys(newOverrides).find(k => k.toLowerCase() === _ovCurrentName.toLowerCase());
      if (existingKey) delete newOverrides[existingKey];
      await _ovPost(newOverrides, 'Override removed.');
    }}

    async function _ovPost(overrides, msg) {{
      try {{
        const resp = await fetch('/api/event-overrides', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ overrides }}),
        }});
        const data = await resp.json();
        if (!resp.ok || data.error) throw new Error(data.error || 'Save failed');
        closeOverrideModal();
        _showToast(msg, true);
        setTimeout(() => location.reload(), 1500);
      }} catch(err) {{
        _showToast('Error: ' + err.message, false);
      }}
    }}

    // ── Custom schedule bar tooltip ──────────────────────────────────────────
    window._schedTips = {{}};
    window._schedBarId = 0;

    function showSchedTip(el, e) {{
      const data = window._schedTips[el.dataset.tipId];
      if (!data) return;
      const tip = document.getElementById('sched-tip');
      const dot = '<span style="display:inline-block;width:8px;height:8px;border-radius:2px;'
        + 'background:' + data.color + ';margin-right:5px;vertical-align:middle"></span>';
      let h = '<div class="stip-door">' + dot + _tipEsc(data.door) + '</div>'
            + '<div class="stip-time">' + _tipEsc(data.time) + '</div>';
      if (data.evts && data.evts.length) {{
        h += '<div class="stip-ev-header">'
          + (data.evts.length === 1 ? 'Event' : data.evts.length + ' Events') + '</div>';
        for (const ev of data.evts) h += '<div class="stip-ev">' + _tipEsc(ev) + '</div>';
      }}
      tip.innerHTML = h;
      tip.style.display = 'block';
      _posSchedTip(e.clientX, e.clientY);
    }}
    function moveSchedTip(e) {{
      if (document.getElementById('sched-tip').style.display !== 'none')
        _posSchedTip(e.clientX, e.clientY);
    }}
    function hideSchedTip() {{ document.getElementById('sched-tip').style.display = 'none'; }}
    function _posSchedTip(x, y) {{
      const tip = document.getElementById('sched-tip');
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      let lx = x + 16, ly = y + 16;
      if (lx + tw > window.innerWidth  - 8) lx = x - tw - 12;
      if (ly + th > window.innerHeight - 8) ly = y - th - 12;
      tip.style.left = lx + 'px';
      tip.style.top  = ly + 'px';
    }}
    function _tipEsc(s) {{
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }}

    refreshDoorStatus();
    if (DS_REFRESH_MS > 0) {{ setInterval(refreshDoorStatus, DS_REFRESH_MS); }}

    async function restoreEvent(btn) {{
      const id = btn.dataset.id;
      const name = btn.dataset.name || 'Event';
      btn.disabled = true;
      try {{
        const resp = await fetch('/api/events/restore', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{ id }}),
        }});
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        _showToast('"' + name + '" restored.', true);
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

    @app.get("/schedule-board", response_class=HTMLResponse)
    async def schedule_board_page(days: int = 7, view: str = "all", q: str = "") -> HTMLResponse:
        board = await _build_schedule_board(days, view_key=view, query=q)
        day_count = int(board["days"])
        day_rows = board["dayRows"]
        timeline_rows = board["timelineRows"]
        summary = board["summary"]
        room_conflicts = board["roomConflicts"]
        shared_door_windows = board["sharedDoorWindows"]
        selected_view = board["selectedView"]
        available_views = board["availableViews"]
        board_query = str(board.get("query") or "")

        def _board_url(*, days_value: int, view_value: str, query_value: str) -> str:
            params = {"days": str(days_value)}
            if view_value and view_value != "all":
                params["view"] = view_value
            if query_value:
                params["q"] = query_value
            return "/schedule-board?" + urlencode(params)

        day_controls = []
        for option in (3, 7, 14):
            cls = "pill-link active" if option == day_count else "pill-link"
            day_controls.append(f'<a href="{_esc(_board_url(days_value=option, view_value=str(selected_view.get("key") or "all"), query_value=board_query), quote=True)}" class="{cls}">{option} Days</a>')
        day_controls_html = "".join(day_controls)

        view_controls = []
        for item in available_views:
            cls = "pill-link active" if item["key"] == selected_view.get("key") else "pill-link"
            view_controls.append(
                f'<a href="{_esc(_board_url(days_value=day_count, view_value=str(item["key"]), query_value=board_query), quote=True)}" class="{cls}">{_esc(str(item["label"]))}</a>'
            )
        view_controls_html = "".join(view_controls)

        event_columns = []
        for day in day_rows:
            event_cards = []
            for event in day["events"]:
                badge_cls = {
                    "event": "evt-badge evt-badge-event",
                    "office_hours": "evt-badge evt-badge-office",
                    "manual_access": "evt-badge evt-badge-manual",
                    "exception_closure": "evt-badge evt-badge-closure",
                    "exception_open": "evt-badge evt-badge-exception-open",
                }.get(str(event["type"]), "evt-badge")
                doors_html = "".join(
                    f'<span class="door-chip">{_esc(label)}</span>'
                    for label in event["doorLabels"]
                ) or '<span class="muted-inline">No doors mapped</span>'
                event_cards.append(
                    '<div class="evt-card">'
                    f'<div class="evt-top"><span class="{badge_cls}">{_esc(event["typeLabel"])}</span>'
                    f'<span class="evt-time">{_esc(event["startLabel"])} - {_esc(event["endLabel"])}</span></div>'
                    f'<div class="evt-name">{_esc(event["name"])}</div>'
                    f'<div class="evt-room">{_esc(event["roomText"])}</div>'
                    f'<div class="evt-doors">{doors_html}</div>'
                    '</div>'
                )
            events_html = "".join(event_cards) or '<div class="empty-state">No scheduled items.</div>'
            today_badge = '<span class="today-badge">Today</span>' if day["isToday"] else ""
            event_columns.append(
                '<div class="day-column">'
                f'<div class="day-column-head"><div class="day-title">{_esc(day["longLabel"])}</div>{today_badge}</div>'
                f'<div class="day-event-count">{len(day["events"])} scheduled item{"s" if len(day["events"]) != 1 else ""}</div>'
                f'<div class="evt-list">{events_html}</div>'
                '</div>'
            )
        event_columns_html = "".join(event_columns)

        matrix_header = ['<div class="matrix-corner">Door</div>']
        for day in day_rows:
            today_cls = " matrix-day-today" if day["isToday"] else ""
            matrix_header.append(
                f'<div class="matrix-day{today_cls}"><div>{_esc(day["label"])}</div>'
                '<div class="matrix-hours">12a  6a  12p  6p</div></div>'
            )

        matrix_rows = ["".join(matrix_header)]
        for row in timeline_rows:
            row_cells = [f'<div class="matrix-label"><span class="door-dot" style="background:{_esc(row["color"])}"></span>{_esc(row["label"])}</div>']
            for day in day_rows:
                segments = row["days"].get(day["date"]) or []
                bars = []
                for segment in segments:
                    left = (float(segment["startMin"]) / 1440.0) * 100.0
                    width = ((float(segment["endMin"]) - float(segment["startMin"])) / 1440.0) * 100.0
                    bar_text = ""
                    if width >= 18:
                        bar_text = (
                            f'{int(segment["startMin"]) // 60:02d}:{int(segment["startMin"]) % 60:02d}'
                            f' - '
                            f'{int(segment["endMin"]) // 60 % 24:02d}:{int(segment["endMin"]) % 60:02d}'
                        )
                    bars.append(
                        f'<div class="time-bar" title="{_esc(segment["title"], quote=True)}" '
                        f'style="left:{left:.3f}%;width:max({width:.3f}%, 3px);background:{_esc(row["color"])}">'
                        f'{_esc(bar_text)}</div>'
                    )
                row_cells.append(
                    '<div class="matrix-cell"><div class="time-track">'
                    '<div class="tick tick-0"></div><div class="tick tick-6"></div>'
                    '<div class="tick tick-12"></div><div class="tick tick-18"></div>'
                    f'{"".join(bars)}'
                    '</div></div>'
                )
            matrix_rows.append("".join(row_cells))
        matrix_html = "".join(matrix_rows)

        room_conflicts_html = "".join(
            '<div class="warn-row">'
            f'<strong>{_esc(item["room"])}</strong>: {_esc(item["firstEvent"])} overlaps '
            f'{_esc(item["secondEvent"])}<br><span class="warn-meta">{_esc(item["startLabel"])} - {_esc(item["endLabel"])}</span>'
            '</div>'
            for item in room_conflicts[:12]
        ) or '<div class="empty-state">No overlapping room bookings in this range.</div>'

        shared_door_html = "".join(
            '<div class="warn-row">'
            f'<strong>{_esc(item["doorLabel"])}</strong>: {_esc(", ".join(item["eventNames"]))}<br>'
            f'<span class="warn-meta">{_esc(item["startLabel"])} - {_esc(item["endLabel"])}</span>'
            '</div>'
            for item in shared_door_windows[:12]
        ) or '<div class="empty-state">No shared door coverage windows in this range.</div>'

        html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Schedule Board — PCO UniFi Sync</title>
{_PWA_HEAD}  <style>{_SHARED_CSS}
    .top-row {{ display:flex; align-items:flex-start; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom:16px; }}
    .board-actions {{ display:flex; gap:8px; flex-wrap:wrap; }}
    .board-toolbar {{ display:flex; align-items:flex-end; justify-content:space-between; gap:16px; flex-wrap:wrap; margin:0 0 16px; }}
    .view-strip {{ display:flex; gap:8px; flex-wrap:wrap; }}
    .board-search {{ display:flex; gap:8px; align-items:flex-end; flex-wrap:wrap; }}
    .board-search label {{ font-size:12px; color:#64748b; font-weight:700; display:block; margin-bottom:4px; }}
    .board-search input {{ min-width:220px; }}
    .pill-link {{
      display:inline-flex; align-items:center; justify-content:center;
      padding:7px 12px; border-radius:999px; border:1px solid #cbd5e1;
      color:#475569; text-decoration:none; font-size:13px; font-weight:600; background:#fff;
    }}
    .pill-link.active {{ background:#0f172a; border-color:#0f172a; color:#fff; }}
    .board-note {{ font-size:13px; color:#64748b; max-width:760px; }}
    .summary-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; margin-bottom:16px; }}
    .summary-card {{ background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:16px; }}
    .summary-card .label {{ font-size:11px; color:#94a3b8; text-transform:uppercase; letter-spacing:.06em; }}
    .summary-card .value {{ font-size:24px; font-weight:700; color:#0f172a; margin-top:4px; }}
    .summary-card .meta {{ font-size:12px; color:#64748b; margin-top:4px; }}
    .warning-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:16px; margin-bottom:16px; }}
    .warn-card {{ background:#fff; border:1px solid #fde68a; border-radius:12px; padding:16px; }}
    .warn-card.secondary {{ border-color:#bfdbfe; }}
    .warn-card .card-title {{ margin-bottom:10px; }}
    .warn-row {{ font-size:13px; color:#334155; padding:10px 0; border-bottom:1px solid #f1f5f9; }}
    .warn-row:last-child {{ border-bottom:none; padding-bottom:0; }}
    .warn-meta {{ color:#64748b; font-size:12px; }}
    .day-grid {{ display:grid; grid-template-columns:repeat({day_count}, minmax(220px, 1fr)); gap:16px; min-width:max-content; }}
    .day-grid-wrap {{ overflow:auto; padding-bottom:4px; }}
    .day-column {{ background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:16px; min-height:240px; }}
    .day-column-head {{ display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:4px; }}
    .day-title {{ font-size:15px; font-weight:700; color:#0f172a; }}
    .today-badge {{ font-size:10px; font-weight:700; padding:2px 8px; border-radius:999px; background:#dbeafe; color:#1d4ed8; }}
    .day-event-count {{ font-size:12px; color:#64748b; margin-bottom:12px; }}
    .evt-list {{ display:flex; flex-direction:column; gap:10px; }}
    .evt-card {{ border:1px solid #e2e8f0; border-radius:10px; padding:12px; background:#f8fafc; }}
    .evt-top {{ display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:6px; }}
    .evt-badge {{ display:inline-block; font-size:10px; font-weight:700; padding:2px 8px; border-radius:999px; }}
    .evt-badge-event {{ background:#dbeafe; color:#1d4ed8; }}
    .evt-badge-office {{ background:#dcfce7; color:#166534; }}
    .evt-badge-manual {{ background:#fef3c7; color:#b45309; }}
    .evt-badge-closure {{ background:#fee2e2; color:#b91c1c; }}
    .evt-badge-exception-open {{ background:#ede9fe; color:#6d28d9; }}
    .evt-time {{ font-size:12px; color:#475569; font-weight:600; }}
    .evt-name {{ font-size:14px; font-weight:700; color:#0f172a; margin-bottom:4px; }}
    .evt-room {{ font-size:12px; color:#64748b; margin-bottom:8px; }}
    .evt-doors {{ display:flex; flex-wrap:wrap; gap:6px; }}
    .door-chip {{ font-size:11px; color:#1e3a8a; background:#eff6ff; border:1px solid #bfdbfe; border-radius:999px; padding:2px 8px; }}
    .muted-inline {{ font-size:11px; color:#94a3b8; }}
    .matrix-wrap {{ overflow:auto; }}
    .schedule-matrix {{
      display:grid;
      grid-template-columns: 160px repeat({day_count}, minmax(220px, 1fr));
      gap:10px;
      min-width:max-content;
      align-items:center;
    }}
    .matrix-corner, .matrix-day, .matrix-label, .matrix-cell {{
      background:#fff; border:1px solid #e2e8f0; border-radius:12px; min-height:74px;
    }}
    .matrix-corner {{ display:flex; align-items:center; justify-content:center; font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:.06em; }}
    .matrix-day {{ padding:12px 14px; font-size:14px; font-weight:700; color:#0f172a; }}
    .matrix-day-today {{ border-color:#93c5fd; box-shadow:0 0 0 1px #bfdbfe inset; }}
    .matrix-hours {{ margin-top:6px; font-size:11px; color:#94a3b8; letter-spacing:.08em; }}
    .matrix-label {{ display:flex; align-items:center; gap:10px; padding:0 14px; font-size:14px; font-weight:600; color:#0f172a; }}
    .door-dot {{ width:10px; height:10px; border-radius:999px; flex-shrink:0; }}
    .matrix-cell {{ padding:12px; }}
    .time-track {{ position:relative; height:48px; border-radius:10px; background:linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%); overflow:hidden; }}
    .tick {{ position:absolute; top:0; bottom:0; width:1px; background:#cbd5e1; opacity:.8; }}
    .tick-0 {{ left:0; }}
    .tick-6 {{ left:25%; }}
    .tick-12 {{ left:50%; }}
    .tick-18 {{ left:75%; }}
    .time-bar {{
      position:absolute; top:8px; bottom:8px; border-radius:8px; color:#fff;
      font-size:10px; font-weight:700; line-height:32px; padding:0 8px; white-space:nowrap;
      overflow:hidden; text-overflow:ellipsis; box-shadow:0 4px 12px rgba(15,23,42,.16);
    }}
    .empty-state {{ font-size:13px; color:#94a3b8; padding:12px 0; }}
    @media (max-width: 640px) {{
      .top-row {{ margin-bottom:12px; }}
      .summary-card .value {{ font-size:20px; }}
      .schedule-matrix {{ grid-template-columns: 130px repeat({day_count}, minmax(180px, 1fr)); }}
      .matrix-corner, .matrix-day, .matrix-label, .matrix-cell {{ min-height:68px; }}
      .matrix-cell {{ padding:10px; }}
    }}
  </style>
</head>
<body>
  {_nav("schedule-board")}
  <div class="page">
    <div class="top-row">
      <div>
        <h2 class="page-heading">Schedule Board</h2>
        <p class="page-subtitle-text">Weekly planning view for church operations. Review upcoming events, door coverage, and schedule conflicts without leaving this app.</p>
        <div class="board-note">Showing the next <strong>{day_count} days</strong> in <strong>{_esc(board["timezone"])}</strong>. View: <strong>{_esc(str(selected_view.get("label") or "All Church"))}</strong>. Generated { _esc(board["generatedAt"]) }.</div>
      </div>
      <div class="board-actions">
        {day_controls_html}
        <a href="{_esc('/api/schedule-board?' + urlencode({k: v for k, v in [('days', str(day_count)), ('view', str(selected_view.get('key') or 'all')), ('q', board_query)] if v and not (k == 'view' and v == 'all')}), quote=True)}" class="pill-link">JSON</a>
      </div>
    </div>

    <div class="board-toolbar">
      <div class="view-strip">
        {view_controls_html}
      </div>
      <form method="get" action="/schedule-board" class="board-search">
        <input type="hidden" name="days" value="{day_count}" />
        <input type="hidden" name="view" value="{_esc(str(selected_view.get('key') or 'all'), quote=True)}" />
        <div>
          <label for="boardQuery">Filter Board</label>
          <input id="boardQuery" type="text" name="q" value="{_esc(board_query, quote=True)}" placeholder="Search event, room, or door" />
        </div>
        <button type="submit" class="sm">Apply</button>
        {'' if not board_query else f'<a class="pill-link" href="{_esc(_board_url(days_value=day_count, view_value=str(selected_view.get("key") or "all"), query_value=""), quote=True)}">Clear</a>'}
      </form>
    </div>

    <div class="summary-grid">
      <div class="summary-card"><div class="label">PCO Events</div><div class="value">{summary["eventCount"]}</div><div class="meta">Scheduled through Planning Center</div></div>
      <div class="summary-card"><div class="label">Total Board Items</div><div class="value">{summary["totalItems"]}</div><div class="meta">PCO events, office hours, and manual access</div></div>
      <div class="summary-card"><div class="label">Doors With Coverage</div><div class="value">{summary["activeDoors"]}</div><div class="meta">Door groups with at least one unlock window</div></div>
      <div class="summary-card"><div class="label">Warnings</div><div class="value">{summary["roomConflictCount"] + summary["sharedDoorCount"]}</div><div class="meta">{summary["roomConflictCount"]} room conflicts, {summary["sharedDoorCount"]} shared door windows</div></div>
    </div>

    <div class="warning-grid">
      <div class="warn-card">
        <span class="card-title">Room Conflicts</span>
        {room_conflicts_html}
      </div>
      <div class="warn-card secondary">
        <span class="card-title">Shared Door Coverage</span>
        {shared_door_html}
      </div>
    </div>

    <div class="card">
      <span class="card-title">Daily Schedule</span>
      <div class="day-grid-wrap">
        <div class="day-grid">
          {event_columns_html}
        </div>
      </div>
    </div>

    <div class="card">
      <span class="card-title">Door Timeline</span>
      <p style="font-size:13px;color:#64748b;margin:0 0 12px;">Each row is a door group. Each cell is one day, with bars showing exactly when that door will be unlocked. Hover for the events driving that window.</p>
      <div class="matrix-wrap">
        <div class="schedule-matrix">
          {matrix_html}
        </div>
      </div>
    </div>
  </div>
</body>
</html>"""
        return HTMLResponse(content=html_out, status_code=200)

    @app.get("/exception-calendar", response_class=HTMLResponse)
    async def exception_calendar_page() -> HTMLResponse:
        return RedirectResponse(url="/office-hours#office-hours-calendar", status_code=307)

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
        zone_views = data.get("zoneViews")
        if zone_views is not None:
            if not isinstance(zone_views, dict):
                return "'zoneViews' must be an object"
            for zk, zv in zone_views.items():
                if not isinstance(zv, dict):
                    return f"zoneViews['{zk}'] must be an object"
                if "doorKeys" in zv and not isinstance(zv.get("doorKeys"), list):
                    return f"zoneViews['{zk}'].doorKeys must be an array"
                if "roomNames" in zv and not isinstance(zv.get("roomNames"), list):
                    return f"zoneViews['{zk}'].roomNames must be an array"
                for dk in (zv.get("doorKeys") or []):
                    if dk not in data["doors"]:
                        return f"zoneViews['{zk}'] references unknown door key '{dk}'"
        return None

    @app.get("/api/mapping")
    async def api_mapping_get() -> dict:
        return _read_mapping()

    @app.post("/api/mapping")
    async def api_mapping_save(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        err = _validate_mapping(payload)
        if err:
            _audit(request, action="mapping.save", result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        _write_mapping(payload)
        _audit(request, action="mapping.save", note=f"rooms={len(payload.get('rooms') or {})}")
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
        def _help_tip(text: str) -> str:
            tip = _esc(text)
            return (
                f'<span class="help-tip" tabindex="0" role="note" '
                f'aria-label="{tip}" data-tip="{tip}">?</span>'
            )
        help_assignments = _help_tip(
            "Each row is a PCO room. Check the door groups that should unlock when that room is used."
        )

        html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Room Mapping — PCO UniFi Sync</title>
{_PWA_HEAD}  <style>{_SHARED_CSS}
    .room-name {{ font-weight: 500; white-space: nowrap; }}
  </style>
</head>
<body>
  {_nav("settings")}
  <div class="page">
    <div id="toast" class="toast"></div>
    <h2 class="page-heading">Room → Door Mapping</h2>
    <p class="page-subtitle-text">Check which doors unlock when an event is scheduled in each room. Changes take effect on the next sync cycle.</p>
    <div class="card" style="background:#f8fafc;border-color:#cbd5e1;">
      <span class="card-title">How This Page Works</span>
      <p style="font-size:13px;color:#475569;margin:0;">
        1) Find the room row from Planning Center. 2) Check the matching door groups. 3) Save mapping.
        If a room has no checked doors, events in that room will not unlock any doors.
      </p>
    </div>

    <form id="mappingForm">
      <div class="card" style="overflow:auto;">
        <span class="card-title">Room Assignments {help_assignments}</span>
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
    async def api_office_hours_save(request: Request, payload: dict = Body(...)) -> dict:
        err = validate_office_hours(payload)
        if err:
            from fastapi.responses import JSONResponse
            _audit(request, action="office_hours.save", result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        save_office_hours(settings.office_hours_file, payload)
        _audit(
            request,
            action="office_hours.save",
            note=f"enabled={str(bool(payload.get('enabled'))).lower()}",
        )
        return {"ok": True}

    @app.get("/api/office-hours/cancelled")
    async def api_oh_cancelled() -> dict:
        return {"dates": sorted(load_cancelled_office_hours(settings.cancelled_office_hours_file))}

    @app.post("/api/office-hours/cancel")
    async def api_oh_cancel(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        date_str = str(payload.get("date") or "").strip()
        if not date_str:
            _audit(request, action="office_hours.cancel_day", result="error", error="date required")
            return JSONResponse(status_code=422, content={"ok": False, "error": "date required"})
        add_cancelled_office_hours_date(settings.cancelled_office_hours_file, date_str)
        _audit(request, action="office_hours.cancel_day", target=date_str)
        await _notify(request, f"Office Hours cancelled for {date_str}")
        return {"ok": True}

    @app.post("/api/office-hours/restore")
    async def api_oh_restore(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        date_str = str(payload.get("date") or "").strip()
        if not date_str:
            _audit(request, action="office_hours.restore_day", result="error", error="date required")
            return JSONResponse(status_code=422, content={"ok": False, "error": "date required"})
        remove_cancelled_office_hours_date(settings.cancelled_office_hours_file, date_str)
        _audit(request, action="office_hours.restore_day", target=date_str)
        await _notify(request, f"Office Hours restored for {date_str}")
        return {"ok": True}

    # ── Office Hours settings page ────────────────────────────────────────

    @app.get("/office-hours", response_class=HTMLResponse)
    async def office_hours_page() -> HTMLResponse:
        from py_app.office_hours import DAYS, parse_time_ranges
        from py_app.mapping import load_room_door_mapping

        oh = load_office_hours(settings.office_hours_file)
        oh_enabled = bool(oh.get("enabled"))
        oh_schedule = oh.get("schedule") or {}
        local_tz = ZoneInfo(settings.display_timezone)

        try:
            mapping = load_room_door_mapping(settings.room_door_mapping_file)
        except Exception:
            mapping = {}
        doors_map = mapping.get("doors") or {}
        door_groups_cfg = mapping.get("doorGroups") or {}
        door_keys = list(doors_map.keys())

        exception_entries = list_exception_entries(exception_calendar_file)
        today_local = datetime.now(timezone.utc).astimezone(local_tz).date()
        grid_start = today_local - timedelta(days=today_local.weekday())
        grid_days: list[date] = [grid_start + timedelta(days=i) for i in range(42)]
        entries_by_date: dict[str, list[dict]] = {}
        for entry in exception_entries:
            start_text = str(entry.get("fromDate") or entry.get("date") or "").strip()
            end_text = str(entry.get("toDate") or start_text).strip()
            try:
                start_date = date.fromisoformat(start_text)
                end_date = date.fromisoformat(end_text)
            except Exception:
                continue
            if end_date < start_date:
                continue
            cursor = start_date
            while cursor <= end_date:
                entries_by_date.setdefault(cursor.isoformat(), []).append(entry)
                cursor += timedelta(days=1)

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
        def _help_tip(text: str) -> str:
            tip = _esc(text)
            return (
                f'<span class="help-tip" tabindex="0" role="note" '
                f'aria-label="{tip}" data-tip="{tip}">?</span>'
            )
        help_enabled = _help_tip(
            "Turns recurring office-hours unlocks on or off without deleting the schedule table."
        )
        help_weekly = _help_tip(
            "For each day, set one or more time ranges and select which door groups unlock in those ranges."
        )
        _indiv_opts = "".join(
            f'<option value="single:{_esc(dk, quote=True)}">{_esc(str((doors_map.get(dk) or {}).get("label") or dk))}</option>'
            for dk in doors_map.keys()
        )
        _group_opts = "".join(
            f'<option value="group:{_esc(gk, quote=True)}" data-keys="{_esc(",".join(str(k) for k in (gv.get("doorKeys") or [])), quote=True)}">'
            f'{_esc(str(gv.get("label") or gk))}</option>'
            for gk, gv in door_groups_cfg.items()
        )
        target_options_html = (
            '<option value="all">All Doors</option>'
            + (f'<optgroup label="Individual Doors">{_indiv_opts}</optgroup>' if _indiv_opts else "")
            + (f'<optgroup label="Door Groups">{_group_opts}</optgroup>' if _group_opts else "")
        )

        def _exception_target_label(entry: dict) -> str:
            keys = [str(k).strip() for k in (entry.get("doorKeys") or []) if str(k).strip()]
            if not keys:
                return "All doors"
            labels = [str((doors_map.get(k) or {}).get("label") or k) for k in keys]
            return ", ".join(labels)

        def _exception_range_label(entry: dict) -> str:
            start_text = str(entry.get("fromDate") or entry.get("date") or "").strip()
            end_text = str(entry.get("toDate") or start_text).strip()
            return start_text if not end_text or end_text == start_text else f"{start_text} → {end_text}"

        exception_rows_html = ""
        for entry in exception_entries:
            kind = str(entry.get("kind") or "")
            badge_cls = "evt-badge evt-badge-closure" if kind == "closure" else "evt-badge evt-badge-exception-open"
            badge_label = "Office Closed" if kind == "closure" else "Extra Hours"
            when_text = _exception_range_label(entry)
            if kind == "special_open":
                when_text += f" · {str(entry.get('startTime') or '')}-{str(entry.get('endTime') or '')}"
            note_html = _esc(str(entry.get("note") or "")) or '<span style="color:#94a3b8">—</span>'
            exception_rows_html += (
                "<tr>"
                f'<td><span class="{badge_cls}">{badge_label}</span></td>'
                f'<td style="white-space:nowrap">{_esc(when_text)}</td>'
                f'<td>{_esc(str(entry.get("label") or ""))}</td>'
                f'<td>{_esc(_exception_target_label(entry))}</td>'
                f'<td>{note_html}</td>'
                f'<td><button class="sm danger" data-id="{_esc(str(entry.get("id") or ""), quote=True)}" onclick="deleteExceptionEntry(this)">Delete</button></td>'
                "</tr>"
            )

        calendar_grid_parts: list[str] = [
            "".join(f'<div class="cal-head">{label}</div>' for label in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        ]
        for cell_date in grid_days:
            date_key = cell_date.isoformat()
            cell_entries = entries_by_date.get(date_key) or []
            today_cls = " cal-cell-today" if cell_date == today_local else ""
            month_cls = "" if cell_date.month == today_local.month else " cal-cell-muted"
            item_html = ""
            for entry in cell_entries[:3]:
                kind = str(entry.get("kind") or "")
                badge_cls = "mini-entry mini-entry-closure" if kind == "closure" else "mini-entry mini-entry-open"
                time_label = ""
                if kind == "special_open":
                    time_label = f'{_esc(str(entry.get("startTime") or ""))}-{_esc(str(entry.get("endTime") or ""))} '
                item_html += f'<div class="{badge_cls}" title="{_esc(str(entry.get("label") or ""), quote=True)}">{time_label}{_esc(str(entry.get("label") or ""))}</div>'
            if len(cell_entries) > 3:
                item_html += f'<div class="mini-more">+{len(cell_entries) - 3} more</div>'
            calendar_grid_parts.append(
                f'<div class="cal-cell{today_cls}{month_cls}">'
                f'<div class="cal-date">{cell_date.day}</div>'
                f'<div class="cal-items">{item_html}</div>'
                '</div>'
            )
        office_calendar_grid_html = "".join(calendar_grid_parts)

        html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Office Hours — PCO UniFi Sync</title>
{_PWA_HEAD}  <style>{_SHARED_CSS}
    .day-name {{ font-weight: 600; white-space: nowrap; width: 100px; }}
    .ranges-input {{ padding: 7px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; width: 100%; }}
    .ranges-input:focus {{ outline: none; border-color: #2563eb; box-shadow: 0 0 0 2px #bfdbfe; }}
    .toggle-row {{ display: flex; align-items: center; gap: 10px; }}
    .toggle-label {{ font-size: 15px; font-weight: 600; cursor: pointer; }}
    .summary-row {{ display:flex; gap:10px; flex-wrap:wrap; margin:0 0 14px; }}
    .summary-pill {{ font-size:12px; font-weight:700; padding:4px 10px; border-radius:999px; background:#eff6ff; color:#1d4ed8; }}
    .planner-grid {{ display:grid; grid-template-columns: minmax(320px, 420px) 1fr; gap:16px; align-items:start; }}
    .form-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
    .form-grid .full {{ grid-column:1 / -1; }}
    .field-label {{ font-size:12px; color:#64748b; font-weight:700; margin-bottom:4px; display:block; }}
    .field-help {{ font-size:12px; color:#64748b; margin-top:8px; line-height:1.5; }}
    .calendar-grid {{ display:grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap:8px; }}
    .cal-head {{ font-size:11px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:.06em; padding:0 4px; }}
    .cal-cell {{ min-height:124px; background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:10px; }}
    .cal-cell-today {{ border-color:#93c5fd; box-shadow:0 0 0 1px #bfdbfe inset; }}
    .cal-cell-muted {{ background:#f8fafc; }}
    .cal-date {{ font-size:13px; font-weight:700; color:#0f172a; margin-bottom:8px; }}
    .cal-items {{ display:flex; flex-direction:column; gap:6px; }}
    .mini-entry {{ font-size:11px; line-height:1.35; border-radius:8px; padding:5px 7px; font-weight:600; }}
    .mini-entry-closure {{ background:#fef2f2; color:#991b1b; border:1px solid #fecaca; }}
    .mini-entry-open {{ background:#f5f3ff; color:#6d28d9; border:1px solid #ddd6fe; }}
    .mini-more {{ font-size:11px; color:#64748b; padding:2px 2px 0; }}
    @media (max-width: 980px) {{
      .planner-grid {{ grid-template-columns:1fr; }}
    }}
    @media (max-width: 640px) {{
      .calendar-grid {{ min-width:760px; }}
    }}
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
    <div class="card" style="background:#f8fafc;border-color:#cbd5e1;">
      <span class="card-title">How This Page Works</span>
      <p style="font-size:13px;color:#475569;margin:0;">
        1) Enable Office Hours. 2) Enter day/time ranges. 3) Check doors for each day. 4) Save.
        These windows are merged with event windows during sync.
      </p>
      <p style="font-size:13px;color:#64748b;margin:10px 0 0;">
        For holidays, closures, or one-time extra office openings, use the <a href="#office-hours-calendar">Office Hours Calendar</a> section below.
      </p>
    </div>

    <form id="officeHoursForm">
      <div class="card">
        <div class="toggle-row">
          <input type="checkbox" name="enabled" id="enabledToggle" {enabled_checked} />
          <label for="enabledToggle" class="toggle-label">Enable Office Hours {help_enabled}</label>
        </div>
        <p style="font-size:13px;color:#64748b;margin:8px 0 0;">
          When unchecked, office hours are ignored during sync (your schedule below is preserved).
        </p>
      </div>

      <div class="card" style="overflow:auto;">
        <span class="card-title">Weekly Schedule {help_weekly}</span>
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

    <div id="office-hours-calendar" class="card" style="margin-top:18px;background:#fff7ed;border-color:#fed7aa;scroll-margin-top:72px;">
      <span class="card-title">Office Hours Calendar Overrides</span>
      <p style="font-size:13px;color:#9a3412;margin:0;">
        Use this only for office-hours exceptions. It removes or adds office-hours access windows and does <strong>not</strong> cancel anything scheduled through Planning Center.
      </p>
    </div>

    <div class="summary-row">
      <span class="summary-pill">{len(exception_entries)} saved office-hours exception{"s" if len(exception_entries) != 1 else ""}</span>
      <span class="summary-pill" style="background:#ecfdf5;color:#166534;">Timezone: {_esc(settings.display_timezone)}</span>
      <span class="summary-pill" style="background:#fff7ed;color:#c2410c;">Office Closed removes office-hours windows. Extra Hours adds one-off office-hours access.</span>
    </div>

    <div class="planner-grid">
      <div>
        <div class="card">
          <span class="card-title">Add Office Hours Exception</span>
          <form id="exceptionForm">
            <div class="form-grid">
              <div>
                <label class="field-label" for="exKind">Type</label>
                <select id="exKind" required onchange="toggleExceptionFields()">
                  <option value="closure">Office Closed</option>
                  <option value="special_open">Extra Office Hours</option>
                </select>
              </div>
              <div>
                <label class="field-label" for="exFromDate">From Date</label>
                <input id="exFromDate" type="date" value="{_esc(today_local.isoformat(), quote=True)}" required />
              </div>
              <div>
                <label class="field-label" for="exToDate">To Date</label>
                <input id="exToDate" type="date" value="{_esc(today_local.isoformat(), quote=True)}" required />
                <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;">
                  <button type="button" class="sm" onclick="setExceptionSpan(1)">1 Day</button>
                  <button type="button" class="sm" onclick="setExceptionSpan(3)">3 Days</button>
                  <button type="button" class="sm" onclick="setExceptionSpan(7)">1 Week</button>
                </div>
              </div>
              <div class="full">
                <label class="field-label" for="exTarget">Affected Doors</label>
                <select id="exTarget">
                  {target_options_html}
                </select>
              </div>
              <div class="full">
                <label class="field-label" for="exLabel">Title</label>
                <input id="exLabel" type="text" maxlength="120" placeholder="e.g. Christmas Office Closed" required />
              </div>
              <div id="exStartWrap">
                <label class="field-label" for="exStartTime">Start Time</label>
                <input id="exStartTime" type="time" value="08:00" />
              </div>
              <div id="exEndWrap">
                <label class="field-label" for="exEndTime">End Time</label>
                <input id="exEndTime" type="time" value="12:00" />
              </div>
              <div class="full">
                <label class="field-label" for="exNote">Notes</label>
                <input id="exNote" type="text" maxlength="160" placeholder="Optional operator note" />
              </div>
            </div>
            <p class="field-help">
              <strong>Office Closed</strong>: removes recurring office-hours unlock windows for each day in the selected date range.
              Leave “Affected Doors” on <strong>All Doors</strong> to close all office-hours doors for those dates.
              <br />
              <strong>Extra Office Hours</strong>: adds the same office-hours unlock window to each day in the selected date range.
            </p>
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
              <button type="submit" class="primary">Save Office Hours Exception</button>
              <a href="/schedule-board" style="font-size:14px;color:#64748b;">View Schedule Board</a>
            </div>
          </form>
        </div>

        <div class="card">
          <span class="card-title">Upcoming Office Hours Exceptions</span>
          <div style="overflow:auto;">
            <table>
              <thead>
                <tr><th>Type</th><th>Date / Time</th><th>Title</th><th>Doors</th><th>Notes</th><th></th></tr>
              </thead>
              <tbody>
                {exception_rows_html or '<tr><td colspan="6" style="padding:12px;color:#94a3b8;">No office-hours exceptions yet.</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="card">
        <span class="card-title">Rolling Calendar</span>
        <p style="font-size:13px;color:#64748b;margin:0 0 12px;">The next 6 weeks of office-hours closures and extra-hours entries. Use this as the long-range planning surface for holidays and weekday office changes.</p>
        <div style="overflow:auto;">
          <div class="calendar-grid">
            {office_calendar_grid_html}
          </div>
        </div>
      </div>
    </div>
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

    function resolveExceptionDoorKeys() {{
      const sel = document.getElementById('exTarget');
      const value = sel.value || 'all';
      if (value === 'all') return [];
      if (value.startsWith('single:')) return [value.slice(7)];
      if (value.startsWith('group:')) {{
        const opt = sel.options[sel.selectedIndex];
        return (opt.dataset.keys || '').split(',').filter(Boolean);
      }}
      return [];
    }}

    function setExceptionSpan(days) {{
      const fromInput = document.getElementById('exFromDate');
      const toInput = document.getElementById('exToDate');
      if (!fromInput.value) return;
      const start = new Date(fromInput.value + 'T00:00:00');
      start.setDate(start.getDate() + Math.max(0, Number(days || 1) - 1));
      toInput.value = start.toISOString().slice(0, 10);
      syncExceptionDateBounds();
    }}

    function syncExceptionDateBounds() {{
      const fromInput = document.getElementById('exFromDate');
      const toInput = document.getElementById('exToDate');
      if (!fromInput || !toInput) return;
      const fromValue = fromInput.value;
      if (!fromValue) return;
      toInput.min = fromValue;
      if (!toInput.value || toInput.value < fromValue) {{
        toInput.value = fromValue;
      }}
    }}

    function toggleExceptionFields() {{
      const kind = document.getElementById('exKind').value;
      const showTimes = kind === 'special_open';
      document.getElementById('exStartWrap').style.display = showTimes ? 'block' : 'none';
      document.getElementById('exEndWrap').style.display = showTimes ? 'block' : 'none';
      document.getElementById('exStartTime').required = showTimes;
      document.getElementById('exEndTime').required = showTimes;
    }}

    document.getElementById('exceptionForm').addEventListener('submit', async (event) => {{
      event.preventDefault();
      const kind = document.getElementById('exKind').value;
      const payload = {{
        kind,
        fromDate: document.getElementById('exFromDate').value,
        toDate: document.getElementById('exToDate').value,
        doorKeys: resolveExceptionDoorKeys(),
        label: document.getElementById('exLabel').value.trim(),
        note: document.getElementById('exNote').value.trim(),
        startTime: kind === 'special_open' ? document.getElementById('exStartTime').value : '',
        endTime: kind === 'special_open' ? document.getElementById('exEndTime').value : '',
      }};
      try {{
        const resp = await fetch('/api/exception-calendar', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload),
        }});
        const data = await resp.json();
        if (!resp.ok || data.error) throw new Error(data.error || ('HTTP ' + resp.status));
        if (data.syncWarning) {{
          showToast('Saved, but sync failed: ' + data.syncWarning, true);
        }} else {{
          showToast('Office-hours exception saved.', false);
        }}
        setTimeout(() => location.reload(), 1400);
      }} catch (err) {{
        showToast('Save failed: ' + err.message, true);
      }}
    }});

    async function deleteExceptionEntry(btn) {{
      if (!confirm('Delete this office-hours exception?')) return;
      btn.disabled = true;
      try {{
        const resp = await fetch('/api/exception-calendar/delete', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ id: btn.dataset.id }}),
        }});
        const data = await resp.json();
        if (!resp.ok || data.error) throw new Error(data.error || ('HTTP ' + resp.status));
        if (data.syncWarning) {{
          showToast('Deleted, but sync failed: ' + data.syncWarning, true);
        }} else {{
          showToast('Office-hours exception deleted.', false);
        }}
        setTimeout(() => location.reload(), 1400);
      }} catch (err) {{
        showToast('Delete failed: ' + err.message, true);
        btn.disabled = false;
      }}
    }}

    function showToast(msg, isError) {{
      const t = document.getElementById("toast");
      t.textContent = msg;
      t.className = isError ? "toast error" : "toast";
      t.style.display = "block";
      setTimeout(() => {{ t.style.display = "none"; }}, 3500);
    }}

    document.getElementById('exFromDate').addEventListener('change', syncExceptionDateBounds);
    document.getElementById('exToDate').addEventListener('change', syncExceptionDateBounds);
    syncExceptionDateBounds();
    toggleExceptionFields();
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
    async def api_event_overrides_save(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        err = validate_event_overrides(payload)
        if err:
            _audit(request, action="event_overrides.save", result="error", error=err)
            return JSONResponse(status_code=422, content={"ok": False, "error": err})
        save_event_overrides(settings.event_overrides_file, payload)
        ov_names = list((payload.get("overrides") or {}).keys())
        _audit(
            request,
            action="event_overrides.save",
            note=f"events={len(ov_names)}",
        )
        if ov_names:
            await _notify(request, f"Event time overrides saved for: {', '.join(ov_names[:5])}" + (" …" if len(ov_names) > 5 else ""))
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
        def _help_tip(text: str) -> str:
            tip = _esc(text)
            return (
                f'<span class="help-tip" tabindex="0" role="note" '
                f'aria-label="{tip}" data-tip="{tip}">?</span>'
            )
        help_event_list = _help_tip(
            "Choose an event name and set exact open/close windows by door for that event only."
        )
        help_editor = _help_tip(
            "Checked + times: exact windows. Checked + blank: suppress this door for this event. Unchecked: use global defaults."
        )

        html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Event Overrides — PCO UniFi Sync</title>
{_PWA_HEAD}  <style>{_SHARED_CSS}
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
    <div class="card" style="background:#f8fafc;border-color:#cbd5e1;">
      <span class="card-title">How This Page Works</span>
      <p style="font-size:13px;color:#475569;margin:0;">
        1) Find an event in the list. 2) Click Set Override/Edit. 3) Configure per-door windows. 4) Save.
        Overrides apply by event name across future occurrences.
      </p>
    </div>

    <div style="margin-bottom:16px;">
      <input type="text" id="search" placeholder="Search events…" oninput="filterRows(this.value)" />
      <span style="margin-left:8px;font-size:13px;color:#64748b;">Event List {help_event_list}</span>
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
      <h3>Editing: <span id="editEventName" style="font-style:italic;"></span> {help_editor}</h3>
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
    async def api_general_settings_save(request: Request, payload: dict = Body(...)) -> dict:
        from fastapi.responses import JSONResponse
        # Validate and save lead/lag into mapping file.
        try:
            lead = int(payload.get("unlockLeadMinutes") or 15)
            lag = int(payload.get("unlockLagMinutes") or 15)
        except (ValueError, TypeError):
            _audit(request, action="general_settings.save", result="error", error="Lead/lag must be integers")
            return JSONResponse(status_code=422, content={"ok": False, "error": "Lead/lag must be integers"})
        if not (0 <= lead <= 120) or not (0 <= lag <= 120):
            _audit(request, action="general_settings.save", result="error", error="Lead/lag must be 0–120 minutes")
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
                _audit(request, action="general_settings.save", result="error", error=f"{day} start must be HH:MM format")
                return JSONResponse(status_code=422, content={"ok": False, "error": f"{day} start must be HH:MM format"})
            if not _time_pat.match(ev):
                _audit(request, action="general_settings.save", result="error", error=f"{day} end must be HH:MM format")
                return JSONResponse(status_code=422, content={"ok": False, "error": f"{day} end must be HH:MM format"})
            per_day[start_key] = sv
            per_day[end_key]   = ev

        save_safe_hours(settings.safe_hours_file, per_day)
        _audit(request, action="general_settings.save", note=f"lead={lead} lag={lag}")
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
    async def api_system_settings_save(request: Request, payload: dict = Body(...)) -> dict:
        import asyncio
        from fastapi.responses import JSONResponse

        cron = str(payload.get("syncCron") or "").strip()
        if cron:
            try:
                CronTrigger.from_crontab(cron, timezone="UTC")
            except Exception:
                _audit(request, action="system_settings.save", result="error", error="Invalid cron expression")
                return JSONResponse(status_code=422, content={"ok": False, "error": "Invalid cron expression"})
        try:
            lookahead = int(payload.get("lookaheadHours") or 168)
        except (ValueError, TypeError):
            _audit(request, action="system_settings.save", result="error", error="Lookahead must be an integer")
            return JSONResponse(status_code=422, content={"ok": False, "error": "Lookahead must be an integer"})
        if not (1 <= lookahead <= 720):
            _audit(request, action="system_settings.save", result="error", error="Lookahead must be between 1 and 720 hours")
            return JSONResponse(status_code=422, content={"ok": False, "error": "Lookahead must be between 1 and 720 hours"})
        try:
            door_refresh = int(payload.get("doorStatusRefreshSeconds") or 30)
        except (ValueError, TypeError):
            _audit(request, action="system_settings.save", result="error", error="Door status refresh must be an integer")
            return JSONResponse(status_code=422, content={"ok": False, "error": "Door status refresh must be an integer"})
        if door_refresh != 0 and not (10 <= door_refresh <= 3600):
            _audit(
                request,
                action="system_settings.save",
                result="error",
                error="Door status refresh must be 0 or between 10 and 3600 seconds",
            )
            return JSONResponse(
                status_code=422,
                content={"ok": False, "error": "Door status refresh must be 0 or between 10 and 3600 seconds"},
            )

        updates: dict[str, str] = {}
        if cron:
            updates["SYNC_CRON"] = cron
        updates["SYNC_LOOKAHEAD_HOURS"] = str(lookahead)
        updates["DOOR_STATUS_REFRESH_SECONDS"] = str(door_refresh)
        tz = str(payload.get("timezone") or "").strip()
        if tz:
            try:
                ZoneInfo(tz)
            except Exception:
                _audit(request, action="system_settings.save", result="error", error="Display timezone must be a valid IANA timezone")
                return JSONResponse(
                    status_code=422,
                    content={"ok": False, "error": "Display timezone must be a valid IANA timezone"},
                )
            updates["DISPLAY_TIMEZONE"] = tz
        token = str(payload.get("telegramBotToken") or "").strip()
        if token:
            updates["TELEGRAM_BOT_TOKEN"] = token
        chat_ids = str(payload.get("telegramChatIds") or "").strip()
        updates["TELEGRAM_CHAT_IDS"] = chat_ids

        if updates:
            _write_env_vars(updates)
        _audit(
            request,
            action="system_settings.save",
            note=f"updates={','.join(sorted(updates.keys())) or 'none'}",
        )

        # Restart the service after sending the response.
        async def _restart():
            await asyncio.sleep(1.5)
            proc = await asyncio.create_subprocess_exec("systemctl", "restart", "pco-unifi-sync")
            await proc.wait()

        asyncio.create_task(_restart())
        return {"ok": True, "restarting": True}

    @app.post("/api/notifications/test")
    async def api_notifications_test(request: Request) -> dict:
        from fastapi.responses import JSONResponse
        err = await sync_service.telegram.send_test()
        if err:
            _audit(request, action="notifications.test", result="error", error=err)
            return JSONResponse(status_code=502, content={"ok": False, "error": err})
        _audit(request, action="notifications.test")
        return {"ok": True}

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
        def _help_tip(text: str) -> str:
            tip = _esc(text)
            return (
                f'<span class="help-tip" tabindex="0" role="note" '
                f'aria-label="{tip}" data-tip="{tip}">?</span>'
            )
        help_door_timing = _help_tip("Global default unlock lead/lag minutes for all events unless overridden.")
        help_after_hours = _help_tip("Events outside these safe windows are held for manual approval on Dashboard.")
        help_sync = _help_tip("How often sync runs and key system behavior. Save & Restart is required here.")
        help_telegram = _help_tip("Configure Telegram bot/chat IDs for alert notifications.")

        html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Settings — PCO UniFi Sync</title>
{_PWA_HEAD}  <style>{_SHARED_CSS}
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
    <div class="card" style="background:#f8fafc;border-color:#cbd5e1;">
      <span class="card-title">How This Page Works</span>
      <p style="font-size:13px;color:#475569;margin:0;">
        The first form saves immediately (timing + approval windows). The second form updates system settings
        and restarts the service. Use Save for form 1 and Save &amp; Restart for form 2.
      </p>
    </div>

    <!-- Form 1: door timing + safe hours (instant save, no restart) -->
    <form id="timingForm">
      <div class="card">
        <span class="card-title">Door Timing {help_door_timing}</span>
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
        <span class="card-title">After-Hours Approval Policy {help_after_hours}</span>
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
        <span class="card-title">Sync Schedule {help_sync}</span>
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
          <input type="number" name="doorStatusRefreshSeconds" value="{settings.door_status_refresh_seconds}" min="0" max="3600" />
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
        <span class="card-title">Telegram Notifications {help_telegram}</span>
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
        {"" if not telegram_ok else """
        <div style="margin-top:10px;">
          <button type="button" class="sm" onclick="sendTelegramTest(this)">Send test message</button>
          <span id="telegramTestNote" style="font-size:13px;margin-left:8px;"></span>
        </div>
        <p class="field-note" style="margin-top:6px;">
          Sends a test message to all configured chat IDs using the <em>currently saved</em> token and chat IDs.
          Save first if you just made changes.
        </p>
        """}
        <p style="font-size:13px;color:#64748b;margin:8px 0 0;"><strong>Notifications sent:</strong>
          after-hours event flagged for approval (one message per sync cycle with new flags);
          sync errors.
        </p>
      </div>

      <div style="display:flex;gap:10px;align-items:center;margin-bottom:24px;">
        <button type="submit" id="sysBtn" class="primary">Save &amp; Restart</button>
        <span id="sysNote" style="font-size:13px;color:#92400e;display:none;">Service restarting — page will reload in a few seconds…</span>
      </div>
    </form>

  </div>
  <script>
    function parseNumberInput(value, fallback) {{
      const parsed = Number.parseInt(value, 10);
      return Number.isNaN(parsed) ? fallback : parsed;
    }}

    document.getElementById("timingForm").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const form = e.target;
      const toast = document.getElementById("toast");
      const payload = {{
        unlockLeadMinutes:    parseNumberInput(form.unlockLeadMinutes.value, 15),
        unlockLagMinutes:     parseNumberInput(form.unlockLagMinutes.value, 15),
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
        lookaheadHours:             parseNumberInput(form.lookaheadHours.value, 168),
        timezone:                   form.timezone.value.trim(),
        doorStatusRefreshSeconds:   parseNumberInput(form.doorStatusRefreshSeconds.value, 30),
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

    async function sendTelegramTest(btn) {{
      btn.disabled = true;
      const note = document.getElementById("telegramTestNote");
      note.style.color = "#64748b";
      note.textContent = "Sending…";
      try {{
        const resp = await fetch("/api/notifications/test", {{ method: "POST" }});
        const data = await resp.json();
        if (!resp.ok || !data.ok) throw new Error(data.error || "HTTP " + resp.status);
        note.style.color = "#16a34a";
        note.textContent = "✓ Message sent successfully.";
      }} catch (err) {{
        note.style.color = "#dc2626";
        note.textContent = "✗ " + err.message;
      }} finally {{
        btn.disabled = false;
      }}
    }}
  </script>
</body>
</html>"""
        return HTMLResponse(content=html_out, status_code=200)

    return app


app = create_app()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
