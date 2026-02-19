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
│   ├── utils.py                   # Shared utilities (parse_iso)
│   ├── logger.py                  # JSON-structured stdout logger
│   └── vendors/
│       ├── pco.py                 # Planning Center Online API client (async, with caching)
│       └── unifi_access.py        # UniFi Access API client (async, schedule/policy management)
├── config/
│   ├── room-door-mapping.json     # Maps PCO room names → UniFi door groups (EDIT THIS to configure rooms)
│   ├── office-hours.json          # Weekly recurring office hours schedule (managed via /office-hours UI)
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
| `UNIFI_ACCESS_API_TOKEN` | _(required if api_token)_ | API token value |
| `UNIFI_ACCESS_API_KEY_HEADER` | `X-API-Key` | Header name for the token (use `Authorization` to send as Bearer) |
| `APPLY_TO_UNIFI` | `false` | Startup default for apply mode (overridden by `config/sync-state.json` if present) |
| `SYNC_CRON` | `*/5 * * * *` | Cron expression for sync schedule |
| `SYNC_INTERVAL_SECONDS` | `300` | Fallback interval if `SYNC_CRON` is empty |
| `SYNC_LOOKAHEAD_HOURS` | `168` | How far ahead (hours) to fetch events (7 days) |
| `SYNC_LOOKBEHIND_HOURS` | `24` | How far back (hours) to include already-started events |
| `ROOM_DOOR_MAPPING_FILE` | `./config/room-door-mapping.json` | Path to room→door mapping config |
| `OFFICE_HOURS_FILE` | `./config/office-hours.json` | Path to office hours config |
| `DISPLAY_TIMEZONE` | `America/New_York` | Timezone for dashboard display AND for converting door schedule times |

### `config/room-door-mapping.json`

This is the primary configuration file. **Edit this to add/remove rooms or change which doors open for each room.**

```json
{
  "doors": {
    "front_lobby": {
      "label": "Front Lobby",
      "unifiDoorIds": ["<uuid from UniFi>"]
    }
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
      {
        "eventNameContains": "worship service",
        "doorKeys": ["gym_front"]
      }
    ]
  }
}
```

- **`doors`**: defines every physical door group. Each key is a slug (`front_lobby`), `unifiDoorIds` is a list of UniFi door UUIDs.
- **`rooms`**: maps PCO room names (exactly as they appear in PCO resource bookings) to a list of door keys.
- **`defaults`**: minutes before/after each event to keep doors unlocked.
- **`rules.excludeDoorKeysByEventName`**: array of rules that prevent specific doors from unlocking for matching events. Matching is case-insensitive substring.

**Important:** Room names must exactly match what PCO returns in resource booking `name` fields (case-sensitive). If an event has no resource bookings, it falls back to the `location` field.

**Important:** For every door key used here, a UniFi Access schedule named exactly `PCO Sync {door_key}` (e.g. `PCO Sync front_lobby`) **must be pre-created in the UniFi UI**. The sync service will update that schedule's time windows but will NOT auto-create new schedules. This is intentional.

### `config/office-hours.json`

Managed via the `/office-hours` web page. Do not edit by hand unless necessary.

```json
{
  "enabled": false,
  "schedule": {
    "monday":    { "ranges": "9:00-17:00", "doors": ["front_lobby"] },
    "tuesday":   { "ranges": "9:00-17:00", "doors": ["front_lobby"] },
    "wednesday": { "ranges": "", "doors": [] },
    ...
  }
}
```

- **`enabled`**: master on/off switch. When false, office hours are completely ignored during sync.
- **`schedule.<day>.ranges`**: time range string. Supports `9:00-17:00`, `8:00-12:00, 13:00-17:00`, `8-12` (whole hours), en-dash. Multiple ranges separated by commas or semicolons. Empty = that day is closed.
- **`schedule.<day>.doors`**: list of door keys (must exist in `room-door-mapping.json`).

Office hours windows are **merged with PCO event windows** before being applied to UniFi. The same `PCO Sync {door_key}` UniFi schedule gets both event-based and office-hours-based time windows combined.

### `config/sync-state.json`

Auto-created when the apply/dry-run mode is toggled on the dashboard. Persists the mode across service restarts. Format:
```json
{ "applyToUnifi": true }
```
Do not commit this file. Add it to `.gitignore`.

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
| `/dashboard` | Main status page: last sync, PCO/UniFi status, errors, stats, event preview table, sync/mode buttons |
| `/settings` | Room → Door mapping editor (checkbox grid) |
| `/office-hours` | Office hours schedule editor (7 rows × door checkboxes + time range text inputs) |
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
| `GET` | `/api/mapping` | Read room-door mapping JSON |
| `POST` | `/api/mapping` | Save room-door mapping JSON (validated before write) |
| `GET` | `/api/office-hours` | Read office hours config JSON |
| `POST` | `/api/office-hours` | Save office hours config JSON (validated before write) |
| `GET` | `/api/unifi/ping` | Test UniFi connectivity |
| `GET` | `/api/unifi/doors` | Probe UniFi for door list |
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
  ├─ build_desired_schedule()          # mapping.py
  │    └─ for each event × room × door:
  │         create unlock window (start-lead → end+lag)
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
- Uses deprecated `@app.on_event("startup/shutdown")` — should be migrated to `lifespan` context manager in a future cleanup

**`py_app/settings.py`**
- Single `Settings` class using `pydantic_settings.BaseSettings`
- Reads from `.env` file and environment variables
- All fields have explicit `alias` = the uppercase env var name

**`py_app/sync_service.py`**
- `SyncService` class holds all in-memory state: last sync result, PCO/UniFi status, error log, apply mode flag
- `run_once()` is the main sync method called by the scheduler and the manual trigger endpoint
- `get_preview()` / `get_upcoming_preview()` build the schedule without applying it (used by dashboard and `/api/preview`)
- Apply mode is persisted to `config/sync-state.json` on every toggle via `_save_apply_state()`

**`py_app/mapping.py`**
- Pure functions, no I/O except `load_room_door_mapping()` which reads a file
- `build_desired_schedule()` is the core algorithm — takes events + mapping config, returns `{items, doorWindows}`
- `_merge_windows()` merges overlapping time windows for a single door (sorted by start, greedy merge)
- `items` = one entry per event-room-door combination (used for display)
- `doorWindows` = merged time windows per door (used for UniFi application)

**`py_app/office_hours.py`**
- `parse_time_ranges()` handles flexible time string parsing (regex-based, silently skips invalid)
- `build_office_hours_windows()` generates door windows by iterating dates in the sync window
- `merge_office_hours_into_desired()` combines office hours windows with PCO event windows and re-runs `_merge_windows()` per door

**`py_app/utils.py`**
- `parse_iso(value)`: shared ISO-8601 → UTC datetime parser. Used by `mapping.py`, `sync_service.py`. Always returns UTC-aware datetime or None.

**`py_app/vendors/pco.py`**
- `PcoClient`: async httpx client for PCO API
- Caches results by normalized time window (truncated to minute precision)
- Tracks stats: cache hits, live fetches, 429 fallbacks (viewable on dashboard)
- `get_events()` makes one API call per page + one `resource_bookings` call per event instance — for large calendars this can be many requests; the caching mitigates this
- `_get_instance_room_names()`: fetches resource bookings for a single event instance, returns room names. Returns `[]` silently on any error.

**`py_app/vendors/unifi_access.py`**
- `UnifiAccessClient`: async httpx client for UniFi Access API
- `apply_desired_schedule()`: the only write path. For each door group:
  1. Finds the pre-existing schedule named `PCO Sync {door_key}` — **raises RuntimeError if not found** (intentional: prevents auto-creating schedules)
  2. Builds weekly schedule from UTC windows (converted to `DISPLAY_TIMEZONE` local time)
  3. PUTs the schedule only if it differs from existing
  4. DELETEs old policy if resources changed, POSTs new policy
- `_build_week_schedule()`: converts list of UTC datetime windows → `{monday: [{start_time, end_time}], ...}` using `DISPLAY_TIMEZONE`
- `list_doors()`: probes multiple candidate paths since UniFi's API surface varies by version

**`py_app/logger.py`**
- JSON-structured logging to stdout. Each line is a JSON object with `ts`, `level`, `msg`, `logger`, and optionally `exc_info`.

---

## UniFi Access — Required Pre-Setup

Before the service can apply any schedules, you must **manually create schedules in the UniFi Access UI** with these exact names:

```
PCO Sync front_lobby
PCO Sync rear_lobby
PCO Sync gym_front
```

(One per door key defined in `config/room-door-mapping.json`.)

The service will update the time windows in those schedules but will never create new ones. This is intentional to prevent accidental schedule proliferation.

You also do not need to create access policies manually — the service creates/updates a policy named `PCO Sync Policy {door_key}` for each door group automatically.

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

1. Get the UniFi door UUID from the UniFi UI or `GET /api/unifi/doors`
2. Add to `config/room-door-mapping.json` → `"doors"`:
   ```json
   "new_door_key": { "label": "Human Label", "unifiDoorIds": ["<uuid>"] }
   ```
3. Create a schedule named `PCO Sync new_door_key` in the UniFi Access UI
4. Map rooms to it as needed

### Adding an exclusion rule

In `config/room-door-mapping.json` → `"rules"`:
```json
"excludeDoorKeysByEventName": [
  { "eventNameContains": "some event name", "doorKeys": ["gym_front"] }
]
```
Matching is case-insensitive substring (not exact match).

### Changing the sync interval

Set `SYNC_CRON` in `.env`, then `systemctl restart pco-unifi-sync`.

### Adjusting unlock lead/lag times

Edit `config/room-door-mapping.json` → `"defaults"`:
```json
"defaults": { "unlockLeadMinutes": 15, "unlockLagMinutes": 15 }
```
These apply globally. Per-room or per-event overrides are not currently supported.

### Changing the timezone

Set `DISPLAY_TIMEZONE` in `.env` (e.g. `America/Chicago`). This controls both the dashboard display timezone and the timezone used when converting UTC windows to UniFi's weekly `HH:MM:SS` format. Restart required.

---

## Key Design Decisions & Constraints

1. **No database.** All state is in memory (sync status, errors) or flat JSON files (mapping, office hours, sync state). This keeps deployment simple.

2. **UniFi schedules must be pre-created.** The service refuses to auto-create UniFi schedules. This prevents it from accumulating orphaned schedules and makes the UniFi side auditable.

3. **Weekly schedule model.** UniFi Access uses a weekly repeating schedule (day-of-week + time range), not specific dates. PCO events are specific dates — but because the sync window is a rolling 7-day lookahead, they all get flattened into day-of-week slots. This means "an event every other Tuesday" shows up in UniFi as "every Tuesday."

4. **One schedule + one policy per door key.** Schedules are named `PCO Sync {door_key}`, policies `PCO Sync Policy {door_key}`. Policies are deleted and recreated if the door IDs change.

5. **Office hours merge into PCO sync schedules.** There are no separate "office hours" schedules in UniFi. Both PCO event windows and office hours windows are combined and applied to the same `PCO Sync {door_key}` schedule.

6. **PCO API rate limiting handled gracefully.** On HTTP 429, the client falls back to the most recent cached result for that time window and increments a `pco429FallbackReturns` counter visible on the dashboard.

7. **Per-event resource booking requests.** For every event instance fetched, a separate PCO API call is made to get its resource bookings (room names). This is O(n) API calls per sync. The result is cached for the full window TTL, so repeated syncs within the cache window make zero live API calls.

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
