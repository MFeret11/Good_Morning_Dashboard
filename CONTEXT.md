# Good Morning Dashboard — Agent Context

## What this is
A commute + weather + alerts dashboard for a two-person household. Backend
is FastAPI (Python), frontend is a single static HTML/CSS/JS page served
at `/dashboard`. Built to run on a wall-mounted display (Amazon Fire 7
tablet in kiosk mode), fed by a Raspberry Pi 4 running the app in Docker.

Primary user: girlfriend, commuting via SEPTA Regional Rail, Media PA
(home) → East Falls (office), with a transfer required most trips (the
two lines don't share track). A separate notification (ntfy.sh push) is
sent once daily on weekdays for the afternoon return commute.

## Repo structure
```
app/
  main.py            — FastAPI routes, ties everything together
  config.py           — constants: stations, walk times, thresholds, ntfy settings
  time_utils.py        — time/delay parsing helpers
  commute.py           — core trip-finding logic against SEPTA's NextToArrive API
  alerts.py            — SEPTA service alerts, filtered to relevant lines
  weather.py           — Open-Meteo forecast + attire suggestions
  notifications.py      — ntfy.sh push notifications
  afternoon_check.py     — builds/sends the one daily afternoon notification
  scheduler.py          — APScheduler background job (fires afternoon check)
static/
  index.html           — single-file dashboard frontend (dark theme, kiosk-oriented)
tests/                  — pytest, 52 tests, all external API calls mocked
Dockerfile               — added for containerized deployment
requirements.txt          — accurate runtime deps (consolidated; the old
                            requirements-docker.txt / stale requirements.txt
                            split has been merged into this one file)
.env                     — NTFY_TOPIC (secret, gitignored, not in repo)
```

## Key API endpoints
- `GET /dashboard` — serves the frontend HTML
- `GET /api/dashboard` — combined payload the frontend polls (commute + weather + alerts + active_window + overall_status + poll_interval_ms)
- `GET /api/commute_morning`, `/api/commute_return` — one-directional commute lookups (renamed from `/api/commute` -> `/api/commute_morning` since the old name was misleading: it always showed the morning direction regardless of time of day)
- `GET /api/alerts`, `/api/weather` — individual data sources
- `POST /api/test_notification` — manually trigger the afternoon check without waiting for the scheduled time

(The old `/api/leg1`/`/api/leg2` debug routes, hardcoded to "30th Street
Station" and unused by real app logic, have been removed - the
`alternatives` field on `/api/commute_morning`/`/api/commute_return`
already exposes the raw SEPTA data they were used for.)

## How the commute logic actually works
`get_commute_leg(origin, destination)` in `commute.py` first checks a
single origin→destination `NextToArrive` call for a direct trip. If one
exists, that's used - no transfer logic runs at all.

If no direct trip exists, `PREFERRED_TRANSFER_STATION` (Jefferson Station)
is now honored for real: the app queries origin→transfer_station and
transfer_station→destination as two separate SEPTA calls and stitches
them together, picking the earliest leg-2 train that's realistically
catchable (buffer at or above `MISSED_CONNECTION_BUFFER_MINUTES`, -5 min).
If no leg-2 train clears that floor, it falls back to SEPTA's own
single-call transfer pick rather than show an unreachable connection.
This resolves the item below that used to say Jefferson-forcing was
shelved - it's now implemented (see `_get_commute_via_transfer` in
`commute.py`).

Either way, the chosen trip computes:
- `leave_by_time` / `minutes_until_leave_by` (walk-time aware)
- `total_delay_minutes` (accounts for delay on either leg of a transfer, not just origin)
- `at_risk` — transfer buffer below `RISK_BUFFER_MINUTES` (see Known Issues)
- `delayed` — total delay above `SIGNIFICANT_DELAY_MINUTES`
- `leave_now` — boolean, drives a pulsing UI state on the frontend

## Known issues / deliberate decisions (read before changing logic)
- **`RISK_BUFFER_MINUTES` is intentionally 0, not a bug to "fix" upward.**
  The user confirmed trains share the same platform/track at Jefferson
  Station, so no physical buffer is needed beyond zero. A test comment
  that referenced an old value of 5 was stale and has been corrected -
  the config value itself was always correct.
- **Jefferson Station transfer-forcing is now implemented** (see above) -
  this used to be shelved as "not a bug, just not built yet," but it's
  done. `MISSED_CONNECTION_BUFFER_MINUTES` (-5) is the hard floor below
  which the stitched connection is abandoned in favor of SEPTA's own
  pick; `RISK_BUFFER_MINUTES` (0) is a separate, looser threshold that
  only controls the "at_risk" UI warning on an otherwise-valid connection.
  Don't conflate the two when touching this logic.
- **`requirements.txt` is now accurate and is the single canonical
  dependency file** (fastapi, uvicorn[standard], apscheduler, requests,
  python-dotenv). The old stale Flask/thefuzz/etc. version and the
  separate `requirements-docker.txt` have both been removed/merged.
- **Frontend polling intervals**: 1 min during commute windows (5-9am,
  2-7pm per `config.py`), 30 min otherwise. This used to be a second,
  independently-hardcoded copy of the hour ranges inside `index.html`'s
  `getPollInterval()`, which had drifted out of sync with `config.py`
  (6-9am/3-7pm vs. the real 5-9am/2-7pm). Fixed by making the backend
  the single source of truth: `/api/dashboard` now returns
  `poll_interval_ms` directly, and the frontend just uses that instead
  of computing its own copy of the schedule. The daily 4:30pm afternoon
  check via APScheduler runs independently of all of this.
- **SEPTA's delay estimates are known to be optimistic and creep upward**
  (e.g. reported as 10 min late, later revised to 20, then 40). This is a
  structural property of SEPTA's own data, not something fixable via a
  better data source — there is no independent/alternative SEPTA transit
  API. A planned mitigation (NOT YET IMPLEMENTED) is cross-referencing
  SEPTA's `TrainView` endpoint (real-time train positions, includes a
  `currentstop` field) against `NextToArrive`'s delay estimate, only when
  a delay is reported (to avoid extra API calls on the common on-time
  case). This would allow messaging like "train has been at Wissahickon
  for 20+ minutes" using `currentstop` comparisons across polls. Requires
  in-memory state (last known position + timestamp per train number) —
  first architectural exception to an otherwise fully stateless app.
  **This is the most important pending reliability improvement** — the
  user has expressed real concern about SEPTA's optimism causing missed
  trains for their girlfriend, and wants this built out.

## Frontend notes
- Single-page dark theme, 4 status tiers: ok / delayed / at_risk / alert
  (color-coded, drives `.status-banner` and card pulsing via `leave-now`)
- Cards: Commute, Weather, Alerts (alerts card spans full width)
- `render(data)` in the `<script>` block does all DOM updates from the
  `/api/dashboard` JSON payload — no framework, plain JS
- A "Good morning!" / "Welcome back" greeting (keyed off `active_window`
  from the API: `"morning"` / `"afternoon"` / `null`) was recently added
  above the status banner — implemented and confirmed working locally.
- Planned but NOT YET BUILT: swipe navigation to a calendar page and a
  persistent "notebook" page (quick shared notes between the two users).
  Notebook is the one place the app will need real persistence (a small
  JSON file or SQLite row) — everything else is intentionally stateless.
  Also backlogged: a lightweight ephemeral photo-booth feature using the
  kiosk tablet's camera (no save/storage), and time-based profile
  switching (different dashboard views depending on who's likely
  commuting, switched via tappable icons — the girlfriend's SEPTA/weather/
  notebook view vs. the user's separate driving-commute-to-Wilmington-DE
  view with traffic/ETA, not yet built, would use Google Maps Directions
  API).

## Deployment
- Runs in Docker on a Raspberry Pi 4 (Pi OS Bookworm... actually Trixie,
  Debian 13), alongside Home Assistant and Portainer containers on the
  same host, orchestrated via a single `docker-compose.yml` (NOT in this
  repo — lives in a separate `~/septa-project` directory on the Pi, since
  it's host-level infra, not app-specific).
- Container built from the `Dockerfile` in this repo, exposes port 8000,
  `NTFY_TOPIC` loaded via `.env` (never committed).
- Displayed on an Amazon Fire 7 tablet running Fully Kiosk Browser,
  pointed at `http://<pi-ip>:8000/dashboard`.
- To redeploy after a code change: `git pull` on the Pi, then
  `docker compose up -d --build septa-dashboard` from the compose file's
  directory.

## Testing
46 pytest tests, all external API calls (SEPTA, Open-Meteo) mocked, run
instantly offline. Known good coverage: delay math, direct-vs-transfer
selection, walk times, alerts filtering, time parsing, notification
message building. **Not yet covered** (flagged as a gap, worth adding):
SEPTA API returning an error/empty response, a train disappearing
between polls, delay appearing on the connecting leg only. These three
map directly to "the dashboard gave confidently wrong information,"
which is the failure mode the user is most concerned about avoiding.

## User context worth knowing
User is technically strong (comfortable with Linux/Docker/Python/AWS),
prefers direct answers without excessive hedging, and has been very
explicit that reliability of the commute/notification logic matters more
than polish — a wrong "on time" reading that causes a missed train is
the scenario to design defensively against throughout this codebase.
