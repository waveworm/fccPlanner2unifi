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

It also supports **Quick Door Access** — manually created temporary unlock windows for a selected door group, configured from the dashboard without changing PCO events or office hours.

It also supports **Event Time Overrides** — per-event-name rules that replace the global lead/lag with exact clock times per door, optionally across two separate unlock windows per door. A door can also be suppressed (blocked) for a specific event while still opening normally for other events.

It also supports an **Approval Gate** — events that start outside configured "safe hours" are held in a pending queue and require manual approval before their door schedule is applied. Events by pre-approved names bypass the gate automatically.

It also supports a **Schedule Board** — a 3/7/14-day planning view that combines PCO events, office hours, and manual access windows into a single operator-facing board with per-door timelines plus conflict warnings.

It also supports an **Office Hours Calendar** — a web-managed calendar for office-hours closures and one-off extra office-hours windows. This is intended for holidays, weekday office closures, and one-time front-office access adjustments. Planning Center remains the source of truth for event scheduling and event cancellations.

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
│   ├── exception-calendar.json    # Office-hours closures + one-off extra-hours windows (managed from /office-hours)
│   ├── pending-approvals.json     # Queue of events awaiting approval (auto-managed, never edit)
│   ├── cancelled-events.json      # Events manually cancelled from the dashboard (auto-managed)
│   ├── manual-access-windows.json # Temporary manual unlock windows (managed from dashboard)
│   └── sync-state.json            # Persisted apply/dry-run toggle state (auto-created, do not commit)
├── deploy/
│   └── pco-unifi-sync.service     # systemd unit file
├── bin/
│   ├── run_server.sh              # Dev launcher (activates .venv, runs uvicorn)
│   └── service.sh                 # Wrapper: install/start/stop/restart/status/logs
├── scripts/
│   ├── capture_quickstart_screenshots.py # Refreshes quick-start PNG screenshots from the live app with Playwright
│   ├── generate_quickstart_docx.py # Builds the editable Google Docs / Word handout from current quick-start content
│   └── generate_quickstart_pdf.py # Builds the meeting/user handout PDF from docs/quickstart-assets
├── tools/
│   └── mapping_csv_tool.py        # CLI to export/import room-door-mapping.json as CSV
├── docs/                          # Supporting docs and CSV templates
├── FCCPlanner2UniFi-Quick-Start.docx # Generated editable Word handout for Google Docs
├── FCCPlanner2UniFi-Quick-Start.pdf # Generated operator quick-start handout
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
| `EXCEPTION_CALENDAR_FILE` | `./config/exception-calendar.json` | Path to exception calendar entries |
| `PENDING_APPROVALS_FILE` | `./config/pending-approvals.json` | Path to approval queue |
| `APPROVED_EVENT_NAMES_FILE` | `./config/approved-event-names.json` | Path to pre-approved event name list |
| `SAFE_HOURS_FILE` | `./config/safe-hours.json` | Path to per-day safe hours config |
| `DISPLAY_TIMEZONE` | `America/New_York` | Timezone for dashboard display AND for converting door schedule times |
| `TELEGRAM_BOT_TOKEN` | _(empty)_ | Telegram bot token for approval notifications (optional) |
| `TELEGRAM_CHAT_IDS` | _(empty)_ | Comma-separated Telegram chat IDs to notify |
| `DOOR_STATUS_REFRESH_SECONDS` | `30` | How often the dashboard door status card auto-refreshes (0 = disabled) |

Validation enforced by `POST /api/system-settings`:
- `SYNC_CRON` must parse successfully via APScheduler `CronTrigger.from_crontab(...)`
- `SYNC_LOOKAHEAD_HOURS` must be between `1` and `720`
- `DISPLAY_TIMEZONE` must be a valid IANA timezone name (for example `America/New_York`)
- `DOOR_STATUS_REFRESH_SECONDS` must be `0` or between `10` and `3600`

UI behavior:
- The General Settings page now preserves literal `0` values for door lead/lag and door-status refresh fields instead of coercing them back to defaults in JavaScript.

### `config/room-door-mapping.json`

This is the primary configuration file. **Edit this to add/remove rooms or change which doors open for each room.**

Current doors configured (in display order):

```json
{
  "doors": {
    "front_lobby": { "label": "Front Lobby", "unifiDoorIds": ["b5f778e6-0c3a-49cd-8f4f-06a7011ee8cd"] },
    "rear_lobby":  { "label": "Rear Lobby",  "unifiDoorIds": ["cdf39816-e069-4b4f-8972-918ca1ac9604"] },
    "office":      { "label": "Office",      "unifiDoorIds": ["3dac1155-e5f8-47c3-96e6-d89c914491f6"] },
    "gym_front":   { "label": "Gym Front",   "unifiDoorIds": ["38357452-65f5-4d3e-babc-6e34ce0aa4f7"] },
    "gym_rear":    { "label": "Gym Rear",    "unifiDoorIds": ["f975beeb-216d-4bbe-9542-78d5b6c2ca2a"] }
  },
  "doorGroups": {
    "lobby_group": { "label": "Lobby Group", "doorKeys": ["front_lobby", "rear_lobby"] },
    "gym_group":   { "label": "Gym Group",   "doorKeys": ["gym_front", "gym_rear"] },
    "all_doors":   { "label": "All Doors",   "doorKeys": ["front_lobby", "rear_lobby", "office", "gym_front", "gym_rear"] }
  },
  "zoneViews": {
    "sanctuary_lobby": {
      "label": "Sanctuary / Lobby",
      "doorKeys": ["front_lobby", "rear_lobby"],
      "roomNames": ["Sanctuary", "Lobby", "Cafe"]
    },
    "gym_student": {
      "label": "Gym / Student",
      "doorKeys": ["gym_front", "gym_rear"],
      "roomNames": ["Gym", "Gymnasium", "Kitchen"]
    }
  },
  "rooms": {
    "Sanctuary": ["front_lobby", "rear_lobby"],
    "Gym":       ["gym_front", "gym_rear"],
    "Gymnasium": ["gym_front", "gym_rear"]
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

- **`doors`**: defines every physical door group. Each key is a slug (`front_lobby`), `unifiDoorIds` is a list of UniFi door UUIDs. **The order of keys determines the display order and color assignment** in the door status card and schedule timeline. All configured doors always appear in the door status card, even if they have no scheduled windows.
- **`doorGroups`**: named groups of door keys used in the Quick Door Access dropdown. Each group has a `label` and a `doorKeys` array. Groups do not affect PCO sync — they are only used for manual access windows. Add, remove, or rename groups freely without any other code changes.
- **`zoneViews`**: optional named Schedule Board views. Each view can target a set of `doorKeys`, `roomNames`, or both. These drive the filter pills on `/schedule-board` and let staff switch between campus areas like Sanctuary / Lobby, Gym / Student, or Office without editing code.
- **`rooms`**: maps PCO room names (exactly as they appear in PCO resource bookings) to a list of door keys.
- **`defaults`**: minutes before/after each event to keep doors unlocked.
- **`rules.excludeDoorKeysByEventName`**: array of rules that prevent specific doors from unlocking for matching events. Matching is case-insensitive substring.
- **`rules.excludeEventsByRoomContains`**: array of substring strings. Any event whose `room` field contains any of these strings (case-insensitive) is completely excluded from the sync.

**Important:** Room names must exactly match what PCO returns in resource booking `name` fields (case-sensitive). If an event has no resource bookings, it falls back to the `location` field.

**Important:** For every door key defined in `doors`, a UniFi Access schedule named exactly `PCO Sync {door_key}` (e.g. `PCO Sync gym_rear`) **must be pre-created in the UniFi UI**. The sync service will update that schedule's time windows but will NOT auto-create new schedules.

**Note:** The `office` door is not mapped to any PCO rooms — it is only used via Office Hours. The `gym_rear` door is mapped to the Gym/Gymnasium rooms and also appears in Quick Door Access groups.

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

### `config/exception-calendar.json`

Managed from the `/office-hours` page. Contains operator-created office-hours closures and extra office-hours windows.

```json
{
  "entries": [
    {
      "id": "abc123",
      "kind": "closure",
      "fromDate": "2026-12-24",
      "toDate": "2026-12-26",
      "doorKeys": [],
      "label": "Christmas Office Closed",
      "note": "Front office closed for holiday week",
      "startTime": "",
      "endTime": "",
      "createdAt": "2026-03-06T01:00:00Z"
    },
    {
      "id": "def456",
      "kind": "special_open",
      "fromDate": "2026-12-22",
      "toDate": "2026-12-24",
      "doorKeys": ["front_lobby", "rear_lobby"],
      "label": "Year-End Office Hours",
      "note": "Front desk open for pickups",
      "startTime": "09:00",
      "endTime": "13:00",
      "createdAt": "2026-03-06T01:05:00Z"
    }
  ]
}
```

- `fromDate` and `toDate` define an inclusive local-date range. Legacy single-day entries may still have `date`; the app treats that as `fromDate == toDate == date`.
- `kind: "closure"` removes recurring office-hours unlock windows for the selected doors for each day in the range.
- `kind: "special_open"` adds the same extra office-hours unlock window for the selected doors on each day in the range.
- `doorKeys: []` means **all configured doors**.
- These entries are merged into the actual schedule during both preview and live sync as an **office-hours override layer**. They do not cancel or modify PCO event windows.

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
| `/schedule-board` | 3/7/14-day planning board with saved zone-view filters, free-text search, daily schedule columns, per-door timeline, room conflict warnings, and shared-door coverage warnings |
| `/exception-calendar` | Compatibility redirect to `/office-hours#office-hours-calendar` |
| `/settings` | Room → Door mapping editor (checkbox grid) |
| `/office-hours` | Office hours editor with weekly schedule plus embedded Office Hours Calendar overrides |
| `/event-overrides` | Event time overrides — table of all known events, inline override editor per event |
| `/general-settings` | Door timing, safe hours, sync behavior, timezone, and Telegram notification settings |
| `/health` | `{"ok": true}` health check |

### API Endpoints

| Method | URL | Description |
|---|---|---|
| `GET` | `/api/status` | Full status snapshot (JSON) |
| `GET` | `/api/config` | Apply mode + cron + UniFi URL |
| `POST` | `/api/config/apply` | Toggle apply mode: `{"applyToUnifi": true}` |
| `POST` | `/api/sync/run` | Trigger an immediate sync cycle |
| `GET` | `/api/preview` | Preview what next sync would apply (JSON) |
| `GET` | `/api/schedule-board` | Structured 3/7/14-day schedule-board data with optional `view` and `q` filters: day buckets, per-door timeline rows, room conflicts, shared door windows |
| `GET` | `/api/exception-calendar` | Read exception-calendar entries |
| `POST` | `/api/exception-calendar` | Create an office-hours closure or extra-hours entry and trigger a sync |
| `POST` | `/api/exception-calendar/delete` | Delete an exception entry and trigger a sync |
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
| `GET` | `/api/general-settings` | Read door timing, safe hours, and current system settings used by the Settings page |
| `POST` | `/api/general-settings` | Save door timing + safe hours |
| `POST` | `/api/system-settings` | Save validated system-level `.env` settings (cron, lookahead, timezone, door refresh, Telegram) and restart the service |
| `GET` | `/api/unifi/ping` | Test UniFi connectivity |
| `GET` | `/api/unifi/doors` | Probe UniFi for full door list |
| `GET` | `/api/unifi/door-status` | Live lock/position status for all configured door groups |
| `POST` | `/api/unifi/door/{door_id}/lock` | Send lock command to a specific UniFi door |
| `GET` | `/api/door-schedule` | Per-door unlock windows (local day + minutes) for the current sync window. Always returns all configured doors; doors with no windows have an empty `windows` array. |
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
  ├─ load_cancelled_office_hours()     # reads config/cancelled-office-hours.json
  ├─ build_office_hours_windows()      # generates windows for each date in range
  ├─ apply exception-calendar entries  # removes/extends office-hours windows only
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
- Dashboard Door Status card: legend showing each door's color square + name + lock status badge (Locked/Unlocked, clickable to lock when unlocked) + position badge (Door Open/Door Closed from sensor). Below the legend: a compact weekly timeline grid (Mon–Sun rows, stacked thin bars per door, alternating row backgrounds). Clicking the grid opens a full modal with larger bars and time labels. Auto-refreshes every `DOOR_STATUS_REFRESH_SECONDS`; `0` disables polling but leaves manual refresh available. **All configured doors always appear in the legend**, even if they have no scheduled windows — doors with no windows show their live lock status but no timeline bars.
- Schedule Board page: server-built planning board with selectable `3`, `7`, or `14` day ranges. It combines PCO events, office-hours instances, manual access windows, and office-hours exception entries into daily columns, then renders a per-door/per-day unlock timeline matrix. It supports:
  - saved view pills built from `zoneViews`, plus smaller fallback views from `doorGroups`
  - a free-text `q` filter matching event type, event name, room text, and door labels
  - JSON export preserving the active `days`, `view`, and `q` filters
  - two warning buckets:
  - **Room Conflicts**: overlapping PCO events booked into the same room
  - **Shared Door Coverage**: a door unlock window driven by multiple events at once (useful for facilities/security review)
- Office Hours page: server-rendered page with:
  - recurring weekly office-hours schedule editor
- Embedded **Office Hours Calendar** section with:
  - entry form for `Office Closed` and `Extra Office Hours`
  - rolling 6-week calendar grid
  - upcoming entries table with delete actions
  - entries saved to `config/exception-calendar.json`
  - explicit guidance that Planning Center event changes should still be made in Planning Center
- Quick Door Access form: dropdown uses `<optgroup>` — "Individual Doors" lists all configured doors, "Door Groups" lists named groups from `doorGroups` in the mapping config. Description field is **mandatory**. The form posts `doorKeys` (array) to `POST /api/manual-access`.
- Scheduled manual-access rows now support both **Edit** and **Cancel**. Edit reuses the same dashboard form, preloads the saved door group, start/end, and description, and submits to `POST /api/manual-access/update`.
- Events table: Door Group(s) column renders each door label in its assigned `_DOOR_COLORS` color.
- Settings forms: the client-side JavaScript now parses numeric fields without using `||` fallbacks, so explicit `0` values are preserved for lead/lag and door-status refresh.

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
PCO Sync gym_rear
```

(One per door key defined in `config/room-door-mapping.json`.)

The service will update the time windows in those schedules but will never create new ones. This is intentional to prevent accidental schedule proliferation.

You also do not need to create access policies manually — the service creates/updates a policy named `PCO Sync Policy {door_key}` for each door group automatically.

**Note on the `office` door:** The `office` door is not mapped to any PCO rooms. Its UniFi schedule (`PCO Sync office`) will only receive windows from the Office Hours configuration, not from PCO events. The schedule still needs to exist in UniFi for the sync to succeed when office hours are active for that door.

**Note on the `gym_rear` door:** The `gym_rear` door is mapped to the Gym and Gymnasium PCO rooms (opens alongside Gym Front for those events). It is also available in Quick Door Access as an individual door and as part of the Gym Group and All Doors groups.

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
4. Map rooms to it as needed in `"rooms"` (optional — a door can exist just for office hours or manual access)
5. Optionally add it to any `doorGroups` entries (e.g. `all_doors`) in `"doorGroups"`
6. The door will automatically appear in the dashboard door status card, the Quick Door Access dropdown, and the schedule timeline (once it has windows)

### Adding or editing a Quick Door Access group

Edit `config/room-door-mapping.json` → `"doorGroups"`:
```json
"my_group": { "label": "My Group", "doorKeys": ["front_lobby", "rear_lobby"] }
```
Groups appear in the Quick Door Access dropdown under "Door Groups". `doorKeys` must reference valid keys in `"doors"`. Changes take effect immediately without a service restart.

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

Navigate to `/general-settings`. Set per-day safe hours start and end times. Planning Center events starting outside those windows are held in the pending queue on the dashboard. Quick Door Access manual-access windows are not queued, but they do require a second confirmation if the requested access falls outside safe hours. Add event names to the "pre-approved" list to bypass the PCO event gate entirely for recurring trusted events.

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

Set `DISPLAY_TIMEZONE` in `.env` (or use `/general-settings`). Restart required. The value must be a valid IANA timezone; invalid values are rejected by `POST /api/system-settings` before `.env` is written.

---

## Recommended UX Roadmap

These are the highest-value product improvements for making the system easier and safer for new operators.

### 1) Quick Door Access (manual temporary unlock window)

**Implemented.** Staff can create a temporary unlock window from the dashboard without touching PCO or office hours.

Current implementation:
- The dropdown shows **Individual Doors** (all 5 configured doors) and **Door Groups** (Lobby Group, Gym Group, All Doors) via `<optgroup>` sections. Groups are defined in `config/room-door-mapping.json` → `doorGroups`.
- **Description is mandatory** — the form requires a note before submitting (e.g. "Jim Arnold – Wedding"). This is enforced both client-side and server-side.
- Start Now and +15/+30/+60/+120 min quick-fill buttons are available.
- Entries stored in `config/manual-access-windows.json`; merged into the door schedule the same way as office hours.
- Auto-pruned when the end time passes.
- Create/cancel actions are included in the audit log.
- Existing scheduled windows can be edited in place from the dashboard without canceling and recreating them.
- If a manual-access window falls outside configured safe hours, the API returns an approval-required response and the dashboard asks the operator to confirm before saving. Confirmed outside-safe-hours saves are annotated in the audit log and notification text.
- The dashboard mobile layout avoids nested vertical scroll regions where possible so quick-access cards, upcoming events, recent changes, and room-mapping sections scroll with the page instead of trapping touch gestures.
- On mobile, the non-interactive body of Upcoming Events and Scheduled Manual Access cards intentionally yields pointer events so vertical drag gestures start the page scroll more reliably; the actual action buttons remain clickable.
- The API supports both creation and editing:
  - `POST /api/manual-access`
  - `POST /api/manual-access/update`
- Both accept a `doorKeys` array (supports multi-door groups). Legacy single `doorKey` is still accepted for backward compatibility. An optional `overrideApproval: true` flag is used by the dashboard after the operator confirms an outside-safe-hours request.

### 2) Schedule Board

**Implemented.** Staff can review the next `3`, `7`, or `14` days from `/schedule-board`.

Current implementation:
- Daily columns combine three schedule sources in one place: PCO events, Office Hours, and Manual Access windows.
- The lower matrix shows one row per door group and one cell per day. Bars reflect the actual unlock windows that will be applied.
- The board now supports saved zone-view pills driven by `config/room-door-mapping.json` → `zoneViews`. If no custom zone view exists, matching `doorGroups` are also exposed as smaller saved views.
- A free-text filter box narrows the board by event name, room, or door label while keeping the current day range and selected view.
- `/api/schedule-board` returns the same structured board data used by the page and accepts `days`, `view`, and `q` query params, which makes filtering/export work straightforward.
- Conflict review is built in:
  - room overlap warnings highlight double-booked spaces
  - shared door coverage warnings highlight doors being kept open by multiple events at once

### 3) First-run onboarding

### 4) Office Hours Calendar

**Implemented.** Staff can add future office-hours closures and extra office-hours windows from the Office Hours page.

Current implementation:
- Closures remove recurring office-hours unlock windows for the selected doors for each day in the selected date range.
- Extra office-hours entries add the same one-time window across each day in the selected date range.
- Entries are stored in `config/exception-calendar.json`.
- Saving or deleting an entry triggers an immediate sync attempt so the schedule updates quickly.
- These entries affect office-hours access only. PCO event scheduling and cancellation should still be handled in Planning Center.

New users currently need to understand several separate pages before the system feels safe to use.

Add a simple onboarding checklist on the dashboard:
- UniFi reachable
- PCO reachable
- mapping configured
- required UniFi schedules found
- apply mode status
- next sync time

This should be visible before the user starts editing anything.

### 3) Safer manual controls

If the app is used outside a locked-down private network, add protection before expanding manual control features.

Recommended minimums:
- restrict the web UI to Tailscale, LAN allowlist, or reverse-proxy auth
- add a visible banner when the app is exposed without access control
- keep destructive actions behind confirm dialogs

### 4) Better defaults for non-technical operators

To reduce mistakes, add opinionated shortcuts:
- preset manual durations like 15, 30, 60, and 120 minutes
- quick actions like **Open front doors for 30 min**
- human-readable summaries before save: `Front Lobby + Rear Lobby, today 6:00 PM to 7:30 PM`

### 5) Scheduling clarity

Operators should be able to tell *why* a door is opening.

Improve the dashboard schedule display to show:
- source type (`PCO`, `Office Hours`, `Manual`)
- the reason/event name that created each window
- the exact local start/end for the currently active window

### 6) Audit logging for operator actions

This is strongly recommended if the UI will be used over Tailscale by multiple people.

Every manual action should create an audit record, especially:
- switching between dry-run and apply mode
- approving or denying after-hours events
- cancelling or restoring events
- saving office hours, mapping changes, overrides, or general settings
- creating, editing, or cancelling future manual access windows
- sending a direct door command

Recommended audit record fields:
- `timestamp`
- `action`
- `target` (event name, door key, settings section, etc.)
- `requestIp`
- `tailscaleIp` (same as request IP when directly connected)
- `displayName`
- `hostname`
- `note`
- `result` (`ok` or `error`)

Recommended implementation shape:
- Add a new append-only file such as `config/audit-log.jsonl`
- Write one JSON object per line for easy grep/export
- Capture the client IP from FastAPI request context (`request.client.host`)
- If running behind a trusted reverse proxy later, only then honor forwarded headers

For Tailscale-friendly names:
- Easiest path: maintain a small local mapping file like `config/tailscale-peers.json` from IP -> person/device label
- Better path: resolve the IP against the local Tailscale status/API and cache the peer name
- Always log the raw IP even if name resolution fails

Important constraint:
- Never rely only on machine name for identity. Use it as a convenience label, not as the source of truth.

Current implementation:
- Audit logging is now wired into the existing POST routes in `py_app/main.py`
- Logs are written to `config/audit-log.jsonl`
- Friendly labels are resolved from `config/tailscale-peers.json`, with reverse-DNS hostname lookup as a fallback
- The dashboard now shows a recent changes panel for quick review

Quick Door Access implementation:
- Temporary manual unlock windows are stored in `config/manual-access-windows.json`
- They are created and canceled from the dashboard
- They are merged into the normal door schedule during preview and sync
- Create/cancel actions are included in the audit log

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
