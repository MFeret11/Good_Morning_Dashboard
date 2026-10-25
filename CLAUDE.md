# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                                   # full suite (CI runs this)
python -m pytest tests/test_commute_regressions.py    # one file
python -m pytest -k missed_connection                 # tests matching a name
uvicorn app.main:app --port 8000                      # run locally (also starts the scheduler)
curl -X POST localhost:8000/api/test_notification     # fire the afternoon push now
```

CI (`.github/workflows/ci.yml`) runs pytest plus a gitleaks scan over the full history.

## This repo is public

Rider-specific settings (stations, walk times, target train home, push time, alert lines, coordinates, `NTFY_TOPIC`) live only in `.env`, which is gitignored. `app/config.py` and `.env.example` hold generic placeholder defaults. Keep real stations, times and coordinates out of every tracked file, tests included. When adding a setting, read it with the `_env*` helpers in `config.py` (they validate at import, so a bad value fails at startup rather than at push time) and add it to `.env.example` and `tests/conftest.py`.

## Architecture

FastAPI service polled by a Home Assistant REST sensor (`/api/dashboard`), plus an in-process APScheduler job (`scheduler.py`) that runs `afternoon_check.run_afternoon_check` on weekdays at `NOTIFY_TIME` and pushes via ntfy.sh. There is no auth; the container port is bound to `127.0.0.1`.

**Trip planning (`commute.py`, `get_commute`)**
1. Query SEPTA NextToArrive origin→destination. If any result is direct, pick one and return.
2. Otherwise query origin→transfer and transfer→destination separately, pick leg 1 (`_pick`: closest to `target_time` in the afternoon, else the first train whose leave-by is still within `LEAVE_BY_GRACE_MINUTES`), and pair it with leg 2 via `_plan_connection`.
3. `_plan_connection` is the single source of truth for pairing: it compares the on-schedule connection with the delay-adjusted one, and sets `missed_connection` and `total_delay_minutes` relative to the planned arrival. `_transfer_itineraries` reuses it, so option 1 in `itineraries` always matches the main pick.
   Cancelled connecting trains are skipped when pairing; if the on-schedule connection was cancelled, `planned_connection_cancelled` is set instead of `missed_connection`. `backup_train_departure` is the next non-cancelled train after the chosen one from the rider's own station.
4. If no leg-2 train is catchable (SEPTA's list ends too early), fall back to SEPTA's own combined transfer pick (`septa_fallback`) rather than report a missed connection.

TrainView positions add live delay and stall detection (`_fetch_live_train_metadata`, stall after `STALL_THRESHOLD_SECONDS` at the same stop).

**SEPTA API quirks**
- NextToArrive's result count parameter is `req3`; `top` is ignored (default is 4 trains). We request `TRIP_RESULT_COUNT`.
- Error responses can be a dict instead of a list; `_fetch_trips` treats non-list payloads as failures.
- Delay text is free-form: "On time", "5 min", "Cancelled", "Suspended", or something unparseable. Use `time_utils.is_cancelled_delay` / `is_delay_unknown`, and never treat unknown text as on time.

**Failing soft.** Every upstream (trips, TrainView, alerts, weather) has a module-level in-memory cache with a TTL. On failure the last good value is served and flagged (`data_stale`, `alerts_stale`, `alerts_unavailable`, `weather_stale`). `afternoon_check._data_quality_notes` appends those caveats to the push, and `run_afternoon_check` sends `CHECK_FAILED_MESSAGE` on any crash, because a missing push reads as "all good".

**Push message priority (`afternoon_check._build_core_message`)**: critical alert > cancelled > planned connection cancelled (rider rerouted onto a later one) > stalled > unclear delay text > missed connection > tight connection > connecting train delayed > overall delay > minor delay / looks good. `main.get_dashboard` computes `overall_status` with a similar order for the dashboard.

## Tests

All HTTP is mocked by patching `app.commute.requests.get` (etc.), usually with a side effect keyed on the `(req1, req2)` station pair (`mock_get_by_station_pair` in `tests/test_commute.py`). `tests/conftest.py`:
- sets neutral env settings before `app` is imported (config is read at import time);
- clears every module cache between tests (otherwise cached responses leak across tests);
- freezes `app.commute._now`. Use `_now()`, not `datetime.now()`, in commute code so tests stay deterministic.

Regression tests for past production bugs live in `tests/test_commute_regressions.py`.

## Deploy

The container runs as the non-root `app` user with `TZ=America/New_York` (commute windows and the push time are local time). In the author's setup it is a service in a separate Docker Compose project; rebuild with `docker compose up -d --build good-morning-dashboard`.
