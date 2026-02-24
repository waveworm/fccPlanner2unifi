# PCO → UniFi Access Sync — Project Reference

This document is the authoritative technical reference for this codebase. It is intended for developers and AI assistants making changes. Read this before touching any file.

---

## What This Project Does

This is a **Python service that automatically manages door-access schedules on a UniFi Access controller based on calendar events from Planning Center Online (PCO)**. It is deployed at a church campus (Mount Laurel, NJ).

When an event is scheduled in PCO (e.g., "Celebrate Recovery" in the Cafe), the sync service:
1. Fetches the event from the PCO Calendar API
2. Looks up which rooms are booked for that event (via PCO resource bookings)
3. Looks up which UniFi door groups those rooms map to (via `config/room-door-mapping.json`)
4. Builds a weekly unlock schedule (start_time − lead_minutes → end_time + lag_minutes)
5. Applies that schedule to pre-existing UniFi Access schedules and policies via the UniFi API

It also supports **Office Hours** — a static recurring weekly schedule that unlocks specific doors during configured times regardless of whether any event is scheduled.

It also supports **Event Time Overrides** — per-event-name rules that replace the global lead/lag with exact clock times per door, optionally across two separate unlock windows per door. A door can also be suppressed (blocked) for a specific event while still opening normally for other events.

It also supports an **Approval Gate** — events that start outside configured "safe hours" are held in a pending queue and require manual approval before their door schedule is applied. Events by pre-approved names bypass the gate automatically.

The service runs as a **systemd service**, exposes a **FastAPI web dashboard** on port 3000, and syncs on a cron schedule (default: every 5 minutes).

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI + Uvicorn |
| HTTP client | httpx (async) |
| Scheduling | APScheduler (AsyncIOScheduler + CronTrigger) |
| Config | Pydantic v2 / pydantic-settings (.env file) |
| Deployment | systemd service |
| Python env | `.venv/` virtualenv |

---

## Directory Structure

```
fccplanner2unifi/
├── py_app/                        # All application code
│   ├── main.py                    # FastAPI app factory, all routes, all HTML pages
│   ├── settings.py                # All configuration (Pydantic BaseSettings, reads .env)
│   ├── sync_service.py            # Orchestrates one sync cycle; holds in-memory status
│   ├── mapping.py                 # Builds desired door schedules from events + mapping config
│   ├── office_hours.py            # Office hours config load/save/parse/build windows
│   ├── event_overrides.py         # Event memory (seen events) + per-event door time overrides
│   ├── approvals.py               # Approval gate: safe hours, pending queue, approve/deny logic
│   ├── utils.py                   # Shared utilities (parse_iso)
│   ├── logger.py                  # JSON-structured stdout logger
│   └── vendors/
│       ├── pco.py                 # Planning Center Online API client (async, with caching)
│       └── unifi_access.py        # UniFi Access API client (async, schedule/policy/status management)
├── config/
│   ├── room-door-mapping.json     # Maps PCO room names → UniFi door groups (EDIT THIS to configure rooms)
│   ├── office-hours.json          # Weekly recurring office hours schedule (managed via /office-hours UI)
│   ├── event-overrides.json       # Per-event-name door time overrides (managed via /event-overrides UI)
│   ├── event-memory.json          # Rolling list of known PCO events (auto-updated by sync, never edit)
│   ├── safe-hours.json            # Per-day safe hours windows for approval gate (managed via /general-settings)
│   ├── pending-approvals.json     # Queue of events awaiting approval (auto-managed, never edit)
│   ├── cancelled-events.json      # Events manually cancelled from the dashboard (auto-managed)
│   └── sync-state.json            # Persisted apply/dry-run toggle state (auto-created, do not commit)
├── deploy/
│   └── pco-unifi-sync.service     # systemd unit file
├── bin/
│   ├── run_server.sh              # Dev launcher (activates .venv, runs uvicorn)
│   └── service.sh                 # Wrapper: install/start/stop/restart/status/logs
├── tools/
│   └── mapping_csv_tool.py        # CLI to export/import room-door-mapping.json as CSV
├── docs/                          # Supporting docs and CSV templates
├── .env                           # Live secrets/config (NOT committed)
├── .env.example                   # Template for .env
├── requirements.txt               # Python dependencies
└── CLAUDE.md                      # This file
```

---

## Configuration

### Environment Variables (`.env`)

All settings are loaded by `py_app/settings.py` via Pydantic `BaseSettings`. Values come from `.env` in the project root.

| Variable | Default | Description |
|---|---|---|
| `PORT` | `3000` | HTTP port for the web dashboard |
| `PCO_BASE_URL` | `https://api.planningcenteronline.com` | PCO API base |
| `PCO_AUTH_TYPE` | `personal_access_token` | `personal_access_token` or `oauth` |
| `PCO_APP_ID` | _(required for PAT)_ | PCO application ID |
| `PCO_SECRET` | _(required for PAT)_ | PCO secret |
| `PCO_ACCESS_TOKEN` | _(required for OAuth)_ | OAuth bearer token |
| `PCO_CALENDAR_ID` | _(empty = all calendars)_ | Scope event fetching to one calendar |
| `PCO_LOCATION_MUST_CONTAIN` | _(empty = no filter)_ | Filter events by location string (e.g. campus name) |
| `PCO_EVENTS_CACHE_SECONDS` | `60` | How long to cache PCO API results |
| `PCO_MIN_FETCH_INTERVAL_SECONDS` | `60` | Minimum time between live PCO fetches per window |
| `PCO_MAX_PAGES` | `40` | Max pagination pages per PCO request |
| `PCO_PER_PAGE` | `100` | Items per PCO API page |
| `UNIFI_ACCESS_BASE_URL` | _(required)_ | UniFi controller URL e.g. `https://192.168.59.9:12445` |
| `UNIFI_ACCESS_VERIFY_TLS` | `false` | Whether to verify the UniFi TLS cert |
| `UNIFI_ACCESS_AUTH_TYPE` | `none` | `api_token` or `none` |
| `UNIFI_ACCESS_USERNAME` | _(empty)_ | Username for session-based auth |
| `UNIFI_ACCESS_PASSWORD` | _(empty)_ | Password for session-based auth |
| `UNIFI_ACCESS_API_TOKEN` | _(required if api_token)_ | API token value |
| `UNIFI_ACCESS_API_KEY_HEADER` | `X-API-Key` | Header name for the token (use `Authorization` to send as Bearer) |
| `APPLY_TO_UNIFI` | `false` | Startup default for apply mode (overridden by `config/sync-state.json` if present) |
| `SYNC_CRON` | `*/5 * * * *` | Cron expression for sync schedule |
| `SYNC_INTERVAL_SECONDS` | `300` | Fallback interval if `SYNC_CRON` is empty |
| `SYNC_LOOKAHEAD_HOURS` | `168` | How far ahead (hours) to fetch events (7 days) |
| `SYNC_LOOKBEHIND_HOURS` | `24` | How far back (hours) to include already-started events |
| `ROOM_DOOR_MAPPING_FILE` | `./config/room-door-mapping.json` | Path to room→door mapping config |
| `OFFICE_HOURS_FILE` | `./config/office-hours.json` | Path to office hours config |
| `EVENT_OVERRIDES_FILE` | `./config/event-overrides.json` | Path to per-event door time overrides |
| `EVENT_MEMORY_FILE` | `./config/event-memory.json` | Path to auto-managed event memory (seen events list) |
| `CANCELLED_EVENTS_FILE` | `./config/cancelled-events.json` | Path to manually cancelled events list |
| `PENDING_APPROVALS_FILE` | `./config/pending-approvals.json` | Path to approval queue |
| `APPROVED_EVENT_NAMES_FILE` | `./config/approved-event-names.json` | Path to pre-approved event name list |
| `SAFE_HOURS_FILE` | `./config/safe-hours.json` | Path to per-day safe hours config |
| `DISPLAY_TIMEZONE` | `America/New_York` | Timezone for dashboard display AND for converting door schedule times |
| `TELEGRAM_BOT_TOKEN` | _(empty)_ | Telegram bot token for approval notifications (optional) |
| `TELEGRAM_CHAT_IDS` | _(empty)_ | Comma-separated Telegram chat IDs to notify |
| `DOOR_STATUS_REFRESH_SECONDS` | `30` | How often the dashboard door status card auto-refreshes (0 = disabled) |

### `config/room-door-mapping.json`

This is the primary configuration file. **Edit this to add/remove rooms or change which doors open for each room.**

Current doors configured (in display order):

```json
{
  "doors": {
    "front_lobby": { "label": "Front Lobby",  "unifiDoorIds": ["b5f778e6-0c3a-49cd-8f4f-06a7011ee8cd"] },
    "rear_lobby":  { "label": "Rear Lobby",   "unifiDoorIds": ["cdf39816-e069-4b4f-8972-918ca1ac9604"] },
    "office":      { "label": "Office",       "unifiDoorIds": ["3dac1155-e5f8-47c3-96e6-d89c914491f6"] },
    "gym_front":   { "label": "Gym Front",    "unifiDoorIds": ["38357452-65f5-4d3e-babc-6e34ce0aa4f7"] }
  },
  "rooms": {
    "Sanctuary": ["front_lobby", "rear_lobby"],
    "Gym": ["gym_front"]
  },
  "defaults": {
    "unlockLeadMinutes": 15,
    "unlockLagMinutes": 15
  },
  "rules": {
    "excludeDoorKeysByEventName": [
      { "eventNameContains": "worship service", "doorKeys": ["gym_front"] }
    ],
    "excludeEventsByRoomContains": ["1520 Hainesport Rd"]
  }
}
```

- **`doors`**: defines every physical door group. Each key is a slug (`front_lobby`), `unifiDoorIds` is a list of UniFi door UUIDs. **The order of keys determines the display order and color assignment** in the door status card and schedule timeline.
- **`rooms`**: maps PCO room names (exactly as they appear in PCO resource bookings) to a list of door keys.
- **`defaults`**: minutes before/after each event to keep doors unlocked.
- **`rules.excludeDoorKeysByEventName`**: array of rules that prevent specific doors from unlocking for matching events. Matching is case-insensitive substring.
- **`rules.excludeEventsByRoomContains`**: array of substring strings. Any event whose `room` field contains any of these strings (case-insensitive) is completely excluded from the sync.

**Important:** Room names must exactly match what PCO returns in resource booking `name` fields (case-sensitive). If an event has no resource bookings, it falls back to the `location` field.

**Important:** For every door key that will receive office hours windows, a UniFi Access schedule named exactly `PCO Sync {door_key}` (e.g. `PCO Sync office`) **must be pre-created in the UniFi UI**. The sync service will update that schedule's time windows but will NOT auto-create new schedules.

**Note:** The `office` door is not mapped to any PCO rooms — it is only used via Office Hours. It still appears in the door status card and schedule timeline.

### `config/office-hours.json`

Managed via the `/office-hours` web page. Do not edit by hand unless necessary.

```json
{
  "enabled": true,
  "schedule": {
    "monday":    { "ranges": "9:00-11:00", "doors": ["rear_lobby", "office"] },
    "tuesday":   { "ranges": "", "doors": [] },
    "wednesday": { "ranges": "9:00-11:00", "doors": ["rear_lobby", "office"] },
    ...
  }
}
```

- **`enabled`**: master on/off switch. When false, office hours are completely ignored during sync.
- **`schedule.<day>.ranges`**: time range string. Supports `9:00-17:00`, `8:00-12:00, 13:00-17:00`, `8-12` (whole hours), en-dash. Multiple ranges separated by commas or semicolons. Empty = that day is closed.
- **`schedule.<day>.doors`**: list of door keys (must exist in `room-door-mapping.json`).

Office hours windows are **merged with PCO event windows** before being applied to UniFi. The same `PCO Sync {door_key}` UniFi schedule gets both event-based and office-hours-based time windows combined.

### `config/safe-hours.json`

Managed via the `/general-settings` web page. Defines per-day windows during which events are automatically approved. Events starting outside these windows are held for manual approval.

```json
{
  "safeStartMonday": "05:00",  "safeEndMonday": "23:00",
  "safeStartTuesday": "05:00", "safeEndTuesday": "23:00",
  ...
  "safeStartFriday": "05:00",  "safeEndFriday": "23:30",
  "safeStartSaturday": "05:00","safeEndSaturday": "23:00",
  "safeStartSunday": "05:00",  "safeEndSunday": "23:00"
}
```

All times are in `DISPLAY_TIMEZONE`. An event is flagged if its start time falls outside `[safeStart{Day}, safeEnd{Day}]` on the day it occurs.

### `config/pending-approvals.json`

Auto-managed by the approval system. **Never edit by hand.** Contains events that have been flagged as outside safe hours and are awaiting approval or denial. Pruned automatically when events expire.

### `config/cancelled-events.json`

Auto-managed when events are manually cancelled from the dashboard. Contains a list of `{id, name, startAt, endAt}` entries. Cancelled events are excluded from sync and shown in a warning card on the dashboard. Can be restored from the dashboard.

### `config/event-overrides.json`

Managed via the `/event-overrides` web page. Per-event-name door time overrides.

```json
{
  "overrides": {
    "Junior High Youth Group": {
      "doorOverrides": {
        "gym_front": {
          "windows": [
            { "openTime": "18:40", "closeTime": "19:20" },
            { "openTime": "21:15", "closeTime": "21:45" }
          ]
        },
        "front_lobby": { "windows": [] }
      }
    }
  }
}
```

- Keys are **exact event names** (case-insensitive match at apply time).
- **`doorOverrides`**: a map of door key → override config.
- **`windows`**: array of `{openTime, closeTime}` pairs in `HH:MM` format (24h, `DISPLAY_TIMEZONE`).
  - One or two windows per door (supports split entry/exit windows).
  - `windows: []` (empty array) means **suppress** — this event will not open this door at all.
- Doors **not listed** in `doorOverrides` use the global `unlockLeadMinutes`/`unlockLagMinutes` defaults.

### `config/event-memory.json`

Auto-managed by the sync service. **Never edit by hand.** Used to drive the `/event-overrides` UI event table.

```json
{
  "events": [
    {
      "name": "Junior High Youth Group",
      "building": "Mount Laurel Campus",
      "rooms": ["Gym", "110 Classroom"],
      "lastSeenAt": "2026-02-14T23:00:00Z",
      "lastEndAt":  "2026-02-15T02:00:00Z",
      "nextAt":     "2026-02-21T23:00:00Z",
      "nextEndAt":  "2026-02-22T02:00:00Z"
    }
  ],
  "updatedAt": "2026-02-20T23:00:00Z"
}
```

Pruning: entries where `lastSeenAt` > 60 days ago AND `nextAt` is null are automatically removed. Do not commit this file.

### `config/sync-state.json`

Auto-created when the apply/dry-run mode is toggled on the dashboard. Persists the mode across service restarts. Format: `{ "applyToUnifi": true }`. Do not commit this file.

---

## Apply Mode vs Dry-Run Mode

The service has two operating modes, toggled from the dashboard:

- **DRY RUN** (default): Fetches events, builds the desired schedule, but does NOT call any UniFi write APIs. Safe for testing.
- **APPLY**: Fully syncs — reads and writes UniFi schedules and policies.

The mode is persisted to `config/sync-state.json` and survives restarts. The `APPLY_TO_UNIFI` env var only sets the initial default if the state file does not exist.

---

## Web Interface

All pages are served inline as HTML from `py_app/main.py` (no separate template files). FastAPI + f-strings. All external data is escaped with `html.escape()` before insertion.

| URL | Description |
|---|---|
| `/` | Redirects to `/dashboard` |
| `/dashboard` | Main status page: door status card, approval queue, upcoming events, sync controls |
| `/settings` | Room → Door mapping editor (checkbox grid) |
| `/office-hours` | Office hours schedule editor (7 rows × door checkboxes + time range text inputs) |
| `/event-overrides` | Event time overrides — table of all known events, inline override editor per event |
| `/general-settings` | Approval gate config: safe hours per day, approved event names, notification settings |
| `/health` | `{"ok": true}` health check |

### API Endpoints

| Method | URL | Description |
|---|---|---|
| `GET` | `/api/status` | Full status snapshot (JSON) |
| `GET` | `/api/config` | Apply mode + cron + UniFi URL |
| `POST` | `/api/config/apply` | Toggle apply mode: `{"applyToUnifi": true}` |
| `POST` | `/api/sync/run` | Trigger an immediate sync cycle |
| `GET` | `/api/preview` | Preview what next sync would apply (JSON) |
| `GET` | `/api/events/upcoming` | Upcoming events list |
| `GET` | `/api/events/cancelled` | List of manually cancelled events |
| `POST` | `/api/events/cancel` | Cancel an event: `{"id", "name", "startAt", "endAt"}` |
| `POST` | `/api/events/restore` | Restore a cancelled event: `{"id"}` |
| `GET` | `/api/approvals/pending` | List events in the approval queue |
| `POST` | `/api/approvals/approve` | Approve a pending event: `{"id"}` |
| `POST` | `/api/approvals/deny` | Deny a pending event: `{"id"}` |
| `GET` | `/api/mapping` | Read room-door mapping JSON |
| `POST` | `/api/mapping` | Save room-door mapping JSON (validated before write) |
| `GET` | `/api/office-hours` | Read office hours config JSON |
| `POST` | `/api/office-hours` | Save office hours config JSON (validated before write) |
| `GET` | `/api/event-overrides` | Read event-overrides config JSON |
| `POST` | `/api/event-overrides` | Save event-overrides config JSON (validated before write) |
| `GET` | `/api/event-memory` | Read event-memory JSON (read-only) |
| `GET` | `/api/general-settings` | Read safe hours + notification settings |
| `POST` | `/api/general-settings` | Save safe hours + approved event names |
| `POST` | `/api/system-settings` | Save system-level `.env` settings (port, credentials, etc.) |
| `GET` | `/api/unifi/ping` | Test UniFi connectivity |
| `GET` | `/api/unifi/doors` | Probe UniFi for full door list |
| `GET` | `/api/unifi/door-status` | Live lock/position status for all configured door groups |
| `POST` | `/api/unifi/door/{door_id}/lock` | Send lock command to a specific UniFi door |
| `GET` | `/api/door-schedule` | Per-door unlock windows (local day + minutes) for the current sync window |
| `GET` | `/api/pco/calendars` | List PCO calendars |
| `GET` | `/api/pco/event-instances/sample` | Raw sample of PCO event_instances |

---

## Code Architecture

### Data Flow (one sync cycle)

```
SyncService.run_once()
  │
  ├─ load_room_door_mapping()          # reads config/room-door-mapping.json
  │
  ├─ asyncio.gather(
  │    pco.check_connectivity(),
  │    unifi.check_connectivity()
  │  )
  │
  ├─ pco.get_events(from, to)          # fetches + caches PCO event_instances
  │    └─ for each event:
  │         _get_instance_room_names() # separate API call per event for resource bookings
  │
  ├─ _filter_events_in_window()        # applies PCO_LOCATION_MUST_CONTAIN filter
  │
  ├─ _apply_mapping_exclusions()       # excludeEventsByRoomContains rules
  │
  ├─ _filter_cancelled_events()        # removes manually cancelled events
  │
  ├─ filter_and_flag_events()          # approvals.py — flags after-hours events, returns approved subset
  │
  ├─ update_event_memory()             # event_overrides.py — updates config/event-memory.json
  │
  ├─ load_event_overrides()            # reads config/event-overrides.json
  │
  ├─ build_desired_schedule()          # mapping.py
  │    └─ for each event × room × door:
  │         check find_door_override() for this event+door
  │         if override found with windows → use exact clock times (1 or 2 windows)
  │         if override found with windows=[] → suppress (skip this door for this event)
  │         if no override → create unlock window (start-lead → end+lag)
  │         merge overlapping windows per door
  │
  ├─ load_office_hours()               # reads config/office-hours.json
  ├─ build_office_hours_windows()      # generates windows for each date in range
  ├─ merge_office_hours_into_desired() # combines with PCO windows, re-merges
  │
  └─ [if apply mode]
       unifi.apply_desired_schedule()
         └─ for each door group:
              find pre-existing "PCO Sync {door_key}" schedule
              _build_week_schedule()   # converts absolute UTC windows → local weekly HH:MM
              compare with existing, PUT if different
              create/update access policy linking schedule → door UUIDs
```

### Module Responsibilities

**`py_app/main.py`**
- Creates the FastAPI app via `create_app()` factory
- Defines all routes as closures inside the factory (so they share `settings`, `sync_service`, etc.)
- Renders all HTML inline using f-strings + `html.escape()` for safety
- The `app = create_app()` at the bottom is what uvicorn imports
- `_SHARED_CSS`: module-level plain Python string constant (not an f-string) containing all shared CSS
- `_DOOR_COLORS`: module-level list of hex colors assigned to door keys in mapping order — `["#3b82f6", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#ec4899"]`. Used both server-side (events table door label coloring) and client-side (schedule timeline bars). **The color for a door key is determined by its position in `config/room-door-mapping.json`'s `doors` object.**
- `_nav(active)`: helper that generates the dark site header HTML with nav links
- Dashboard page layout: Door Status card → Pending Approvals card → Upcoming Events card → collapsible sections (Sync Details, Recent Errors, PCO API Stats, Sync Configuration, Room→Door Mapping)
- Dashboard Door Status card: legend showing each door's color square + name + lock status badge (Locked/Unlocked, clickable to lock when unlocked) + position badge (Door Open/Door Closed from sensor). Below the legend: a compact weekly timeline grid (Mon–Sun rows, stacked thin bars per door, alternating row backgrounds). Clicking the grid opens a full modal with larger bars and time labels. Auto-refreshes every `DOOR_STATUS_REFRESH_SECONDS`.
- Events table: Door Group(s) column renders each door label in its assigned `_DOOR_COLORS` color.

**`py_app/settings.py`**
- Single `Settings` class using `pydantic_settings.BaseSettings`
- Reads from `.env` file and environment variables
- All fields have explicit `alias` = the uppercase env var name

**`py_app/sync_service.py`**
- `SyncService` class holds all in-memory state: last sync result, PCO/UniFi status, error log, apply mode flag
- `run_once()` is the main sync method called by the scheduler and the manual trigger endpoint
- `get_preview()` builds the schedule without applying it (used by dashboard and `/api/preview`)
- `get_upcoming_preview()` uses a fixed 24h lookback; filters out events whose `endAt` has already passed
- `_filter_events_in_window()`: includes in-progress events (started before window but `endAt > now`)
- `_apply_mapping_exclusions()`: filters by `e["room"]` against `excludeEventsByRoomContains`. Never uses `locationRaw`.
- `_filter_cancelled_events()`: removes events whose ID appears in `cancelled-events.json`
- Apply mode persisted to `config/sync-state.json` via `_save_apply_state()`

**`py_app/approvals.py`**
- `load_safe_hours(file_path)`: loads per-day safe hours from `safe-hours.json`. Keys: `safeStart{Day}` and `safeEnd{Day}` for all 7 days. Backward-compatible with old single-field format.
- `is_outside_safe_hours(event, safe_hours, local_tz)`: returns `(bool, reason_str)` — true if the event's start time falls outside the configured window for that day of week.
- `filter_and_flag_events(events, pending_file, approved_names_file, safe_hours_file, local_tz)`: the main gate function. For each event: if name is pre-approved → pass through; if within safe hours → pass through (and clear any stale pending entry); if outside safe hours → add to pending queue and exclude from approved list.
- `approve_pending(pending_file, approved_names_file, event_id)`: moves event from pending queue to approved names list.
- `deny_pending(pending_file, event_id)`: removes event from pending queue (it will be re-evaluated next sync and re-flagged if still outside safe hours).
- **Auto-clear**: when an event passes the safe hours check or is auto-approved by name, any stale pending entry for that event ID is automatically removed so the approval card doesn't show stale items.

**`py_app/mapping.py`**
- Pure functions, no I/O except `load_room_door_mapping()` which reads a file
- `build_desired_schedule(events, mapping, now_iso, overrides, local_tz)` → `{items, doorWindows}`
- `_merge_windows()` merges overlapping time windows per door (sorted, greedy merge)

**`py_app/office_hours.py`**
- `parse_time_ranges()` handles flexible time string parsing (regex-based, silently skips invalid)
- `build_office_hours_windows()` generates door windows by iterating dates in the sync window
- `merge_office_hours_into_desired()` combines office hours windows with PCO event windows

**`py_app/event_overrides.py`**
- `load_event_memory()` / `update_event_memory()`: rolling list of PCO event names. Tracks `lastSeenAt`/`lastEndAt` and `nextAt`/`nextEndAt` per event name.
- `load_event_overrides()` / `save_event_overrides()` / `validate_event_overrides()`
- `find_door_override(event_name, door_key, overrides)`: case-insensitive lookup

**`py_app/utils.py`**
- `parse_iso(value)`: shared ISO-8601 → UTC datetime parser

**`py_app/vendors/pco.py`**
- `PcoClient`: async httpx client for PCO API with caching and 429 fallback
- `get_events()`: paginated fetch + per-event resource booking calls
- `_get_instance_room_names()`: fetches resource bookings for one event instance

**`py_app/vendors/unifi_access.py`**
- `UnifiAccessClient`: async httpx client for UniFi Access API
- `apply_desired_schedule()`: the only write path — finds `PCO Sync {door_key}` schedule (raises RuntimeError if not found), builds weekly schedule, PUTs if changed, manages access policies
- `_build_week_schedule()`: converts UTC windows → `{monday: [{start_time, end_time}], ...}` in `DISPLAY_TIMEZONE`
- `get_door_statuses(door_ids)`: calls `GET /api/v1/developer/doors`, returns `{door_id: {status, name, position}}`. Normalizes UniFi's `"LOCK"/"UNLOCK"` → `"LOCKED"/"UNLOCKED"` and `"CLOSE"` → `"CLOSED"`.
- `lock_door(door_id)`: sends lock command via multiple candidate API paths with fallback
- `list_doors()`: probes multiple candidate paths since UniFi's API surface varies by version

**`py_app/logger.py`**
- JSON-structured logging to stdout. Each line is a JSON object with `ts`, `level`, `msg`, `logger`, and optionally `exc_info`.

---

## UniFi Access — Required Pre-Setup

Before the service can apply any schedules, you must **manually create schedules in the UniFi Access UI** with these exact names:

```
PCO Sync front_lobby
PCO Sync rear_lobby
PCO Sync office
PCO Sync gym_front
```

(One per door key defined in `config/room-door-mapping.json`.)

The service will update the time windows in those schedules but will never create new ones. This is intentional to prevent accidental schedule proliferation.

You also do not need to create access policies manually — the service creates/updates a policy named `PCO Sync Policy {door_key}` for each door group automatically.

**Note on the `office` door:** The `office` door is not mapped to any PCO rooms. Its UniFi schedule (`PCO Sync office`) will only receive windows from the Office Hours configuration, not from PCO events. The schedule still needs to exist in UniFi for the sync to succeed when office hours are active for that door.

---

## Deployment

### First-time setup

```bash
cd /root/fccplanner2unifi
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# Edit .env with real credentials

# Install systemd service
cp deploy/pco-unifi-sync.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable pco-unifi-sync
systemctl start pco-unifi-sync
```

### Day-to-day operations

```bash
./bin/service.sh start
./bin/service.sh stop
./bin/service.sh restart
./bin/service.sh status
./bin/service.sh logs

# Or directly:
systemctl restart pco-unifi-sync
journalctl -u pco-unifi-sync -f
```

### After any code change

```bash
systemctl restart pco-unifi-sync
```

Config file changes (`.json` files in `config/`) take effect on the next sync cycle without a restart.

---

## Common Tasks for AI Assistants

### Adding a new room

Edit `config/room-door-mapping.json` → add to `"rooms"`:
```json
"New Room Name": ["front_lobby"]
```
The room name must match exactly what PCO returns in resource bookings.

### Adding a new physical door

1. Get the UniFi door UUID from `GET /api/unifi/doors`
2. Add to `config/room-door-mapping.json` → `"doors"` (in the desired display position):
   ```json
   "new_door_key": { "label": "Human Label", "unifiDoorIds": ["<uuid>"] }
   ```
3. Create a schedule named `PCO Sync new_door_key` in the UniFi Access UI
4. Map rooms to it as needed in `"rooms"` (optional — a door can exist just for office hours)
5. The door will automatically appear in the dashboard door status card and schedule timeline

### Changing door display order or colors

Door order and color assignment are both determined by the key order in `config/room-door-mapping.json` → `"doors"`. Colors cycle through `_DOOR_COLORS` in `main.py`: blue, amber, green, red, purple, pink. Reordering the keys changes both the display order and color assignments simultaneously.

### Adding a door exclusion rule (by event name)

```json
"excludeDoorKeysByEventName": [
  { "eventNameContains": "worship service", "doorKeys": ["gym_front"] }
]
```
Matching is case-insensitive substring.

### Excluding events entirely (by room name)

```json
"excludeEventsByRoomContains": ["1520 Hainesport Rd"]
```
An event is excluded if its `room` field contains any of these strings. Never use `locationRaw` for this.

### Configuring the approval gate

Navigate to `/general-settings`. Set per-day safe hours start and end times. Events starting outside those windows are held in the pending queue on the dashboard. Add event names to the "pre-approved" list to bypass the gate entirely for recurring trusted events.

### Adding a pre-approved event name

On the `/general-settings` page, add the exact event name to the approved list. Or directly edit `config/approved-event-names.json`:
```json
["Sunday Service", "Staff Meeting"]
```
Matching is case-insensitive.

### Setting per-event door time overrides

Navigate to `/event-overrides`. The table shows all PCO events seen in the last 60 days. Click **Set Override** or **Edit** on any row.

### Changing the sync interval

Set `SYNC_CRON` in `.env`, then `systemctl restart pco-unifi-sync`.

### Adjusting unlock lead/lag times

Edit `config/room-door-mapping.json` → `"defaults"`:
```json
"defaults": { "unlockLeadMinutes": 15, "unlockLagMinutes": 15 }
```

### Changing the timezone

Set `DISPLAY_TIMEZONE` in `.env`. Restart required.

---

## Key Design Decisions & Constraints

1. **No database.** All state is in memory (sync status, errors) or flat JSON files (mapping, office hours, sync state, approvals). This keeps deployment simple.

2. **UniFi schedules must be pre-created.** The service refuses to auto-create UniFi schedules. This prevents it from accumulating orphaned schedules and makes the UniFi side auditable.

3. **Weekly schedule model.** UniFi Access uses a weekly repeating schedule (day-of-week + time range), not specific dates. PCO events are specific dates — but because the sync window is a rolling 7-day lookahead, they all get flattened into day-of-week slots. This means "an event every other Tuesday" shows up in UniFi as "every Tuesday."

4. **One schedule + one policy per door key.** Schedules are named `PCO Sync {door_key}`, policies `PCO Sync Policy {door_key}`. Policies are deleted and recreated if the door IDs change.

5. **Office hours merge into PCO sync schedules.** There are no separate "office hours" schedules in UniFi. Both PCO event windows and office hours windows are combined and applied to the same `PCO Sync {door_key}` schedule.

6. **PCO API rate limiting handled gracefully.** On HTTP 429, the client falls back to the most recent cached result for that time window and increments a counter visible on the dashboard.

7. **Per-event resource booking requests.** For every event instance fetched, a separate PCO API call is made to get its resource bookings. This is O(n) API calls per sync. The result is cached for the full window TTL.

8. **Approval gate is non-blocking.** A denied or pending event is simply excluded from that sync cycle. On the next sync it is re-evaluated — if it now falls within safe hours (or was approved), it passes through. Deny does not permanently block an event.

9. **Door status is read-only except for lock.** The dashboard can read live lock/position status and send a lock command. It cannot unlock doors — that is intentional to prevent accidental unlocking from the web UI.

10. **Door color assignment is order-based.** `_DOOR_COLORS` in `main.py` assigns colors by the position of door keys in `room-door-mapping.json`. Reordering keys changes colors. If colors need to be stable, do not reorder the `doors` object.

---

## Dependencies (`requirements.txt`)

| Package | Purpose |
|---|---|
| `fastapi` | Web framework |
| `uvicorn[standard]` | ASGI server |
| `httpx` | Async HTTP client (PCO + UniFi API calls) |
| `pydantic` | Data validation |
| `pydantic-settings` | `.env` file loading |
| `python-dotenv` | `.env` loading fallback |
| `apscheduler` | Cron/interval job scheduler |
| `Jinja2` | Installed as FastAPI transitive dep; not yet used for templates |
