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

FastAPI service polled by a Home Assistant REST sensor (`/api/dashboard`; HA imports only the `commute` section, so `get_dashboard` copies plain-text line alerts into it as `service_alerts`), plus in-process APScheduler jobs (`scheduler.py`, one worker thread): `afternoon_check.run_afternoon_check` on weekdays at `NOTIFY_TIME` pushes via ntfy.sh, then `run_follow_up_check` runs every 5 minutes and pushes `Update: ...` only when the push ranks higher in `SEVERITY`, is a serious one not yet sent, or the delay grew by 10+ minutes. Until the first train leaves the rider's station (or SEPTA lists a different train) it re-runs `get_commute`. After that, a direct trip stops, but a transfer trip keeps going until the connecting train leaves: NextToArrive no longer lists the train the rider is on, so `commute.get_connection_update` takes its delay from TrainView's `late` field and re-pairs it with the trains now listed at the transfer station via `_plan_connection`. If TrainView has no delay for the train, that check fails (never read as on time), except once the train should already have reached the transfer station, when the last known delay is used. Every push also lists up to two later trains from `itineraries` ("If you take a later train: ..."), but only options whose status is "ok" or "delayed", so a tight, cancelled or unclear connection is never offered (`_other_trains`); the pushes don't use the rider's GPS yet, so they don't know the rider's real train. A late first train that still makes the planned connection is pushed as "Late but still connecting", which ranks with "Commute looks good", so it doesn't trigger a follow-up. Follow-up state is in memory, so a restart mid-afternoon ends follow-ups for that day. There is no auth; the container port is bound to `127.0.0.1`.

**Trip planning (`commute.py`, `get_commute`)**
1. Query SEPTA NextToArrive origin→destination. If any result is direct, pick one and return.
2. Otherwise query origin→transfer and transfer→destination separately, pick leg 1 (`_pick`: closest to `target_time` in the afternoon, else the first train whose leave-by is still within `LEAVE_BY_GRACE_MINUTES`), and pair it with leg 2 via `_plan_connection`.
3. `_plan_connection` is the single source of truth for pairing: it compares the on-schedule connection with the delay-adjusted one, and sets `missed_connection` and `total_delay_minutes` relative to the planned arrival. `_transfer_itineraries` reuses it, so option 1 in `itineraries` always matches the main pick.
   Cancelled connecting trains are skipped when pairing; if the on-schedule connection was cancelled, `planned_connection_cancelled` is set instead of `missed_connection`. `backup_train_departure` is the next non-cancelled train after the chosen one from the rider's own station.
4. If no connection works at the preferred station (no trains listed, all cancelled, or none catchable), `_plan_via` tries each of `TRANSFER_FALLBACK_STATIONS` in order (Suburban, then 30th Street by default, skipping the origin and destination) and marks the trip `transfer_fallback` with `preferred_transfer_station`; the push says where to change instead, the kitchen card shows it, and a follow-up is sent if the station changes after the afternoon push. Only if every station fails does it use SEPTA's own combined transfer pick (`septa_fallback`), rather than report a missed connection. While the rider is on the first train, `get_connection_update` stays with the station already chosen.

TrainView positions add stall detection and a live delay (`_fetch_live_train_metadata`: stall after `STALL_THRESHOLD_SECONDS` at the same stop; `late_minutes` from the `late` field, `None` when missing or unparseable). TrainView spells the track field `TRACK`.

**Rider GPS (`rider.py`, optional).** Home Assistant can POST the rider's position to `/api/location` (latitude, longitude, accuracy, `last_seen`, and its zone state such as `home`). The fix lives in memory only and is never logged or returned. Each new fix is compared with TrainView positions: two fixes in a row beside the same train, with the rider having moved between them, mean the rider is on that train (`ON_TRAIN_RADIUS_M` is wide because both positions can be a minute or two old; one missed fix is forgiven; a fix older than `FIX_MAX_AGE_SECONDS` counts for nothing). In the afternoon window `get_dashboard` then replaces the target-time trip with `commute.get_trip_for_boarded_train`, which rebuilds the trip for that train from `_SEEN_ROWS` (every NextToArrive row listed today, since a train drops off the list once it leaves) plus TrainView's live delay: a first-leg train is re-paired with the connections now listed, a connecting or direct train is just the ride to the destination. The `commute` section always carries `rider_on_train`, `rider_home` and `rider_location_age_minutes`. A train passing the other way is never matched (TrainView's `heading` against the rider's own course). TrainView lists a train it has no GPS for at a fixed placeholder position, still at its first stop, with a delay that climbs each minute; `has_live_position` spots that, such a train is never called stalled (`origin_position_unknown`) and never matched by distance. A rider moving at train speed with no located train alongside is `riding_untracked`; if exactly one of today's listed trains has no position, `get_dashboard` takes the rider to be on it (morning too), drops the stall and marks the delay unknown. With no fix posted, everything behaves as before. The pushes don't use the rider's position yet.

**SEPTA API quirks**
- NextToArrive's result count parameter is `req3`; `top` is ignored (default is 4 trains). We request `TRIP_RESULT_COUNT`.
- Error responses can be a dict instead of a list; `_fetch_trips` treats non-list payloads as failures.
- Delay text is free-form: "On time", "5 min", "Cancelled", "Suspended", or something unparseable. Use `time_utils.is_cancelled_delay` / `is_delay_unknown`, and never treat unknown text as on time.

**SEPTA call volume.** Home Assistant polls `/api/dashboard` every 30 s around the clock. `_fetch_trips` reuses any answer (empty included) younger than `TRIP_FRESH_SECONDS` (60 s) from `_TRIP_RECENT`, and TrainView is reused for `TRAINVIEW_TTL_SECONDS` (60 s), so a commute window costs about 4 SEPTA calls a minute. `get_dashboard` looks up trains only in `DASHBOARD_MORNING_*` / `DASHBOARD_AFTERNOON_*` (6-8 AM, 2-6 PM: the kitchen card shows 6-8 AM and 4-6 PM), narrower than `MORNING_*` / `AFTERNOON_*`, which still set the weather windows and let follow-up pushes run until 7 PM; outside those hours it makes no SEPTA calls at all (`no_alerts()` instead of `get_alerts()`). Tests run with `TRIP_FRESH_SECONDS = 0` (conftest) because many swap SEPTA's answer between two lookups; `tests/test_api_usage.py` covers the reuse.

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
