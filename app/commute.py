"""Trip planning against SEPTA's NextToArrive and TrainView APIs: direct and
transfer trips, the next three options, live tracks, delay-aware connections,
a fallback cache and stall detection."""
import logging
import time
from datetime import datetime, timedelta
import requests

from app.config import (
    SEPTA_BASE, WALK_TIMES, DEFAULT_WALK_TIME_MINUTES, RISK_BUFFER_MINUTES,
    SIGNIFICANT_DELAY_MINUTES, LEAVE_NOW_THRESHOLD_MINUTES, LEAVE_BY_GRACE_MINUTES,
    MISSED_CONNECTION_BUFFER_MINUTES, DEFAULT_TRANSFER_STATION,
)
from app.time_utils import (
    parse_time, parse_delay_minutes, format_time_no_leading_zero, anchor_to_today,
    is_cancelled_delay, is_delay_unknown,
)

log = logging.getLogger(__name__)

# In-Memory Caches
_TRIP_CACHE = {}          # (origin, destination) -> (data, timestamp)
_TRAIN_POSITIONS = {}     # train_no -> {"stop": str, "lat": float, "lon": float, "first_seen_at": float}
_TRAINVIEW_CACHE = ([], 0.0) # (trains_list, timestamp)

CACHE_TTL_SECONDS = 300
TRAINVIEW_TTL_SECONDS = 15
STALL_THRESHOLD_SECONDS = 600

# NextToArrive reads the result count from req3. It ignores "top" and
# defaults to 4 trains (~2.5 hours), too short to reach an evening connection
# when polled early in the afternoon.
TRIP_RESULT_COUNT = 12


def _now() -> datetime:
    """Wall-clock time; a separate function so tests can freeze it."""
    return datetime.now()


def _fetch_trips(origin: str, destination: str) -> tuple[list | None, bool]:
    """Returns (trips, is_stale). On an upstream failure, serves the cached
    trips (is_stale=True) if they are younger than CACHE_TTL_SECONDS;
    otherwise returns (None, False) so callers can tell 'SEPTA is down'
    apart from 'no trains'."""
    cache_key = (origin, destination)
    now = time.time()
    params = {"req1": origin, "req2": destination, "req3": TRIP_RESULT_COUNT}

    try:
        response = requests.get(f"{SEPTA_BASE}/NextToArrive/index.php", params=params, timeout=8)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError(f"unexpected payload type {type(data).__name__}")
    except (requests.RequestException, ValueError) as e:
        log.warning("SEPTA NextToArrive %s -> %s failed: %s", origin, destination, e)
        cached = _TRIP_CACHE.get(cache_key)
        if cached and (now - cached[1]) <= CACHE_TTL_SECONDS:
            return cached[0], True
        return None, False

    if data:
        _TRIP_CACHE[cache_key] = (data, now)
        # /api/commute accepts any station pair, so drop expired entries to
        # keep the cache from growing without bound.
        for key in [k for k, (_, ts) in _TRIP_CACHE.items() if now - ts > CACHE_TTL_SECONDS]:
            del _TRIP_CACHE[key]
    return data, False


def _get_trainview_feed() -> list:
    """Fetches TrainView with a 15-second cache to prevent multi-download storms."""
    global _TRAINVIEW_CACHE
    now = time.time()
    cached_data, cached_at = _TRAINVIEW_CACHE

    if cached_data and (now - cached_at) <= TRAINVIEW_TTL_SECONDS:
        return cached_data

    try:
        response = requests.get(
            f"{SEPTA_BASE}/TrainView/index.php",
            params={"req1": "TrainView", "req2": "TrainView"},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            _TRAINVIEW_CACHE = (data, now)
            return data
    except (requests.RequestException, ValueError) as e:
        log.warning("SEPTA TrainView failed: %s", e)

    return cached_data


def _fetch_live_train_metadata(train_number: str) -> dict:
    """Extracts telemetry from TrainView and evaluates GPS stall without 24-hr leakage."""
    if not train_number or not str(train_number).isdigit():
        return {}

    now = time.time()
    trains = _get_trainview_feed()
    match = next((t for t in trains if str(t.get("trainno")) == str(train_number)), None)

    if not match:
        return {}

    for stale_train in [k for k, v in _TRAIN_POSITIONS.items() if now - v["first_seen_at"] > 3600]:
        del _TRAIN_POSITIONS[stale_train]

    current_stop = match.get("currentstop", "Unknown")
    lat = match.get("lat")
    lon = match.get("lon")
    track = match.get("track", "TBD")

    is_stalled = False
    stall_minutes = 0

    if train_number in _TRAIN_POSITIONS:
        prev = _TRAIN_POSITIONS[train_number]
        time_since_first_seen = now - prev["first_seen_at"]

        # Reset if stale (e.g. from yesterday or > 1 hour ago)
        if time_since_first_seen > 3600:
            _TRAIN_POSITIONS[train_number] = {"stop": current_stop, "lat": lat, "lon": lon, "first_seen_at": now}
        elif prev["stop"] == current_stop and current_stop != "Unknown":
            if time_since_first_seen >= STALL_THRESHOLD_SECONDS:
                is_stalled = True
                stall_minutes = round(time_since_first_seen / 60)
        else:
            _TRAIN_POSITIONS[train_number] = {"stop": current_stop, "lat": lat, "lon": lon, "first_seen_at": now}
    else:
        _TRAIN_POSITIONS[train_number] = {"stop": current_stop, "lat": lat, "lon": lon, "first_seen_at": now}

    try:
        late_minutes = max(0, int(match.get("late")))
    except (TypeError, ValueError):
        late_minutes = None  # no usable delay; callers must not read this as on time

    return {
        "track": track,
        "current_stop": current_stop,
        "is_stalled": is_stalled,
        "stall_minutes": stall_minutes,
        "consist": match.get("consist"),
        "late_minutes": late_minutes,
    }


def _build_walk_and_leave_by(chosen: dict, walk_time: int) -> dict:
    delay_str = chosen.get("orig_delay", "On time")
    is_cancelled = is_cancelled_delay(delay_str)
    origin_delay = parse_delay_minutes(delay_str)

    origin_actual_departure = parse_time(chosen["orig_departure_time"]) + timedelta(minutes=origin_delay)
    leave_by = origin_actual_departure - timedelta(minutes=walk_time)

    now = _now()
    leave_by_today = anchor_to_today(now, leave_by)
    minutes_until_leave_by = round((leave_by_today - now).total_seconds() / 60)

    meta = _fetch_live_train_metadata(chosen.get("orig_train", ""))

    return {
        "origin_train": chosen.get("orig_train"),
        "origin_line": chosen.get("orig_line"),
        "origin_departure_time": chosen.get("orig_departure_time"),
        "origin_actual_departure_time": format_time_no_leading_zero(origin_actual_departure),
        "origin_delay_minutes": origin_delay,
        "origin_delay_text": delay_str,
        "origin_delay_unknown": is_delay_unknown(delay_str),
        "origin_track": meta.get("track", "TBD"),
        "origin_current_stop": meta.get("current_stop", "En route"),
        "is_stalled": meta.get("is_stalled", False),
        "stall_minutes": meta.get("stall_minutes", 0),
        "is_cancelled": is_cancelled,
        "walk_time_minutes": walk_time,
        "leave_by_time": format_time_no_leading_zero(leave_by),
        "minutes_until_leave_by": minutes_until_leave_by,
        "already_departed": minutes_until_leave_by < -walk_time,
        "leave_now": (not (minutes_until_leave_by < -walk_time))
        and minutes_until_leave_by <= LEAVE_NOW_THRESHOLD_MINUTES,
    }


def _is_cancelled_trip(trip: dict) -> bool:
    return is_cancelled_delay(trip.get("orig_delay")) or is_cancelled_delay(trip.get("term_delay"))


def _next_backup_departure(results: list, chosen: dict) -> str | None:
    """Scheduled departure of the next train after `chosen` from the same
    station, skipping cancelled ones. `results` is the list `chosen` was
    picked from, in SEPTA's (departure) order, so the backup is never an
    earlier train or one from a different station."""
    start = results.index(chosen) + 1
    for trip in results[start:]:
        if trip.get("orig_train") != chosen.get("orig_train") and not _is_cancelled_trip(trip):
            return trip.get("orig_departure_time")
    return None


def _build_direct_trip(chosen: dict, walk_time: int, alternatives: list, backup_departure: str | None) -> dict:
    base = _build_walk_and_leave_by(chosen, walk_time)
    origin_delay = base["origin_delay_minutes"]

    scheduled_final_arrival = parse_time(chosen["orig_arrival_time"])
    actual_final_arrival = scheduled_final_arrival + timedelta(minutes=origin_delay)

    delayed = (
        origin_delay >= SIGNIFICANT_DELAY_MINUTES
        or base["is_cancelled"]
        or base["is_stalled"]
        or base["origin_delay_unknown"]
    )

    return {
        "trip_type": "direct",
        **base,
        "connection_station": None,
        "at_risk": base["is_stalled"],
        "delayed": delayed,
        "missed_connection": False,
        "planned_connection_cancelled": False,
        "total_delay_minutes": origin_delay,
        "arrival_time": chosen.get("orig_arrival_time"),
        "actual_arrival_time": format_time_no_leading_zero(actual_final_arrival),
        "backup_train_departure": backup_departure,
        "alternatives": alternatives,
    }


def _build_transfer_trip(
    chosen: dict,
    walk_time: int,
    alternatives: list,
    backup_departure: str | None,
    planned_arrival: datetime | None = None,
) -> dict:
    """planned_arrival is when the trip would reach the destination if every
    train ran on schedule. Delay is measured against it, so a late first leg
    that pushes the rider onto a later connection counts as delay even when
    that later train is itself on time."""
    base = _build_walk_and_leave_by(chosen, walk_time)
    origin_delay = base["origin_delay_minutes"]

    term_delay_str = chosen.get("term_delay", "On time")
    connection_cancelled = is_cancelled_delay(term_delay_str)
    connection_delay = parse_delay_minutes(term_delay_str)
    connection_delay_unknown = is_delay_unknown(term_delay_str)

    scheduled_arrival = parse_time(chosen["orig_arrival_time"])
    actual_arrival = scheduled_arrival + timedelta(minutes=origin_delay)

    scheduled_departure = parse_time(chosen["term_depart_time"])
    actual_departure = scheduled_departure + timedelta(minutes=connection_delay)
    transfer_buffer = (actual_departure - actual_arrival).total_seconds() / 60

    scheduled_final_arrival = parse_time(chosen["term_arrival_time"])
    actual_final_arrival = scheduled_final_arrival + timedelta(minutes=connection_delay)

    baseline_arrival = planned_arrival or scheduled_final_arrival
    total_delay_minutes = max(
        0, round((actual_final_arrival - baseline_arrival).total_seconds() / 60)
    )

    conn_meta = _fetch_live_train_metadata(chosen.get("term_train", ""))
    connection_stalled = conn_meta.get("is_stalled", False)

    at_risk = transfer_buffer < RISK_BUFFER_MINUTES or base["is_stalled"] or connection_stalled
    delayed = (
        total_delay_minutes >= SIGNIFICANT_DELAY_MINUTES
        or base["is_cancelled"]
        or connection_cancelled
        or at_risk
        or base["origin_delay_unknown"]
        or connection_delay_unknown
    )

    return {
        "trip_type": "transfer",
        **base,
        "origin_arrival_time": chosen.get("orig_arrival_time"),  # first leg at the transfer station, scheduled
        "connection_station": chosen.get("Connection"),
        "connection_train": chosen.get("term_train"),
        "connection_line": chosen.get("term_line"),
        "connection_departure_time": chosen.get("term_depart_time"),
        "connection_actual_departure_time": format_time_no_leading_zero(actual_departure),
        "connection_delay_minutes": connection_delay,
        "connection_delay_text": term_delay_str,
        "connection_delay_unknown": connection_delay_unknown,
        "connection_track": conn_meta.get("track", "TBD"),
        "connection_current_stop": conn_meta.get("current_stop", "En route"),
        "connection_is_stalled": connection_stalled,
        "connection_stall_minutes": conn_meta.get("stall_minutes", 0),
        "connection_cancelled": connection_cancelled,
        "transfer_buffer_minutes": round(transfer_buffer, 1),
        "arrival_time": chosen.get("term_arrival_time"),
        "planned_arrival_time": format_time_no_leading_zero(baseline_arrival),
        "actual_arrival_time": format_time_no_leading_zero(actual_final_arrival),
        "total_delay_minutes": total_delay_minutes,
        "at_risk": at_risk,
        "delayed": delayed,
        "missed_connection": False,
        "planned_connection_cancelled": False,
        "backup_train_departure": backup_departure,
        "alternatives": alternatives,
    }


def _first_catchable_leg2(
    leg2_results: list, arrival: datetime, use_delays: bool = True
) -> tuple[dict | None, float | None]:
    """Earliest connecting train whose departure is no more than
    MISSED_CONNECTION_BUFFER_MINUTES before `arrival`. With use_delays=False
    it answers 'which train would we catch if everything ran on schedule?',
    so cancelled trains still count there; with live data they are skipped,
    because nobody can board them.
    Returns (train, buffer_minutes) or (None, None)."""
    for leg2 in leg2_results:
        departure = parse_time(leg2["orig_departure_time"])
        if use_delays:
            if is_cancelled_delay(leg2.get("orig_delay")):
                continue
            departure += timedelta(minutes=parse_delay_minutes(leg2.get("orig_delay", "On time")))
        buffer_min = (departure - arrival).total_seconds() / 60
        if buffer_min >= MISSED_CONNECTION_BUFFER_MINUTES:
            return leg2, buffer_min
    return None, None


def _delay_of(train: dict) -> int:
    return parse_delay_minutes(train.get("orig_delay", "On time"))


def _plan_connection(leg1: dict, leg2_results: list) -> dict | None:
    """Pairs a first-leg train with the connection the rider will actually
    make, and compares it with the one they'd make if everything ran on
    schedule. The main pick and the itinerary list both use this, so they
    always agree. Returns None when no listed connection is catchable (the
    list of connecting trains doesn't reach that far yet)."""
    scheduled_arrival = parse_time(leg1["orig_arrival_time"])
    actual_arrival = scheduled_arrival + timedelta(minutes=_delay_of(leg1))

    leg2, buffer_min = _first_catchable_leg2(leg2_results, actual_arrival)
    if leg2 is None:
        return None

    planned_leg2, _ = _first_catchable_leg2(leg2_results, scheduled_arrival, use_delays=False)
    final_arrival = parse_time(leg2["orig_arrival_time"]) + timedelta(minutes=_delay_of(leg2))
    planned_arrival = parse_time((planned_leg2 or leg2)["orig_arrival_time"])
    on_later_train = planned_leg2 is not None and (
        parse_time(leg2["orig_departure_time"]) > parse_time(planned_leg2["orig_departure_time"])
    )
    # The on-schedule connection is cancelled, so the rider takes `leg2`
    # instead. Reported as a cancellation, not a missed connection.
    planned_cancelled = on_later_train and is_cancelled_delay(planned_leg2.get("orig_delay"))

    return {
        "leg1": leg1,
        "leg2": leg2,
        "buffer_minutes": buffer_min,
        "planned_arrival": planned_arrival,
        "actual_arrival": final_arrival,
        "total_delay_minutes": max(0, round((final_arrival - planned_arrival).total_seconds() / 60)),
        # Delays pushed the rider onto a later train than the on-schedule plan.
        "missed_connection": on_later_train and not planned_cancelled,
        "planned_connection_cancelled": planned_cancelled,
        "planned_connection_train": planned_leg2.get("orig_train") if planned_cancelled else None,
    }


def _option_status(
    total_delay: int,
    buffer_min: float | None = None,
    cancelled: bool = False,
    delay_unknown: bool = False,
) -> str:
    if cancelled:
        return "cancelled"
    if buffer_min is not None and buffer_min < RISK_BUFFER_MINUTES:
        return "at_risk"
    if delay_unknown:
        return "unclear"
    return "delayed" if total_delay >= SIGNIFICANT_DELAY_MINUTES else "ok"


def _direct_itineraries(direct_trips: list, chosen: dict) -> list:
    """The chosen direct train and the next two after it."""
    start = direct_trips.index(chosen)
    itineraries = []
    for n, trip in enumerate(direct_trips[start:start + 3], start=1):
        delay = _delay_of(trip)
        itineraries.append({
            "option": n,
            "trip_type": "direct",
            "origin_train": trip.get("orig_train"),
            "origin_line": trip.get("orig_line"),
            "origin_actual_departure": format_time_no_leading_zero(
                parse_time(trip["orig_departure_time"]) + timedelta(minutes=delay)),
            "origin_delay_minutes": delay,
            "connection_train": None,
            "transfer_buffer_minutes": 0,
            "actual_arrival_time": format_time_no_leading_zero(
                parse_time(trip["orig_arrival_time"]) + timedelta(minutes=delay)),
            "total_delay_minutes": delay,
            "missed_connection": False,
            "status": _option_status(
                delay,
                cancelled=is_cancelled_delay(trip.get("orig_delay")),
                delay_unknown=is_delay_unknown(trip.get("orig_delay")),
            ),
        })
    return itineraries


def _transfer_itineraries(leg1_results: list, chosen_leg1: dict, leg2_results: list) -> list:
    """The chosen first-leg train and the next two after it, each paired with
    its connection. Option 1 is always the trip shown at the top level.
    First-leg trains with no visible connection are left out."""
    start = leg1_results.index(chosen_leg1)
    itineraries = []
    for leg1 in leg1_results[start:start + 3]:
        plan = _plan_connection(leg1, leg2_results)
        if plan is None:
            continue
        leg2 = plan["leg2"]
        itineraries.append({
            "option": len(itineraries) + 1,
            "trip_type": "transfer",
            "origin_train": leg1.get("orig_train"),
            "origin_line": leg1.get("orig_line"),
            "origin_actual_departure": format_time_no_leading_zero(
                parse_time(leg1["orig_departure_time"]) + timedelta(minutes=_delay_of(leg1))),
            "origin_delay_minutes": _delay_of(leg1),
            "connection_train": leg2.get("orig_train"),
            "connection_line": leg2.get("orig_line"),
            "connection_actual_departure": format_time_no_leading_zero(
                parse_time(leg2["orig_departure_time"]) + timedelta(minutes=_delay_of(leg2))),
            "transfer_buffer_minutes": round(plan["buffer_minutes"], 1),
            "actual_arrival_time": format_time_no_leading_zero(plan["actual_arrival"]),
            "total_delay_minutes": plan["total_delay_minutes"],
            "missed_connection": plan["missed_connection"],
            "planned_connection_cancelled": plan["planned_connection_cancelled"],
            "status": _option_status(
                plan["total_delay_minutes"],
                plan["buffer_minutes"],
                cancelled=is_cancelled_delay(leg1.get("orig_delay")),
                delay_unknown=is_delay_unknown(leg1.get("orig_delay"))
                or is_delay_unknown(leg2.get("orig_delay")),
            ),
        })
    return itineraries


def _pick_closest_to_target(results: list, target_time_str: str) -> dict:
    target = parse_time(target_time_str)
    return min(results, key=lambda r: abs((parse_time(r["orig_departure_time"]) - target).total_seconds()))


def _first_reachable(results: list, walk_time: int) -> dict:
    """First train the rider can still make on foot: its leave-by time (live
    departure minus walk) is no more than LEAVE_BY_GRACE_MINUTES ago. Falls
    back to the first train if none qualify."""
    now = _now()
    for r in results:
        departure = parse_time(r["orig_departure_time"]) + timedelta(
            minutes=parse_delay_minutes(r.get("orig_delay", "On time"))
        )
        leave_by = anchor_to_today(now, departure - timedelta(minutes=walk_time))
        if (leave_by - now).total_seconds() / 60 >= -LEAVE_BY_GRACE_MINUTES:
            return r
    return results[0]


def _pick(results: list, target_time: str | None, walk_time: int) -> dict:
    """The train nearest the target time, or else the first one still reachable."""
    if target_time:
        return _pick_closest_to_target(results, target_time)
    return _first_reachable(results, walk_time)


def get_commute(
    origin: str,
    destination: str,
    target_time: str | None = None,
    preferred_transfer: str | None = None,
) -> dict:
    walk_time = WALK_TIMES.get(origin, DEFAULT_WALK_TIME_MINUTES)
    transfer_station = preferred_transfer or DEFAULT_TRANSFER_STATION

    raw_results, raw_stale = _fetch_trips(origin, destination)
    if raw_results is None:
        return {"error": "SEPTA trip data unavailable"}

    # 1. Direct Trips
    direct_trips = [r for r in raw_results if r.get("isdirect") == "true"]
    if direct_trips:
        chosen_direct = _pick(direct_trips, target_time, walk_time)
        trip = _build_direct_trip(
            chosen_direct, walk_time, alternatives=raw_results,
            backup_departure=_next_backup_departure(direct_trips, chosen_direct),
        )
        trip["itineraries"] = _direct_itineraries(direct_trips, chosen_direct)
        trip["data_stale"] = raw_stale
        return trip

    def septa_fallback(error: str, stale: bool) -> dict:
        if not raw_results:
            return {"error": error}
        pick = _pick(raw_results, target_time, walk_time)
        trip = _build_transfer_trip(
            pick, walk_time, alternatives=raw_results,
            backup_departure=_next_backup_departure(raw_results, pick),
        )
        trip["itineraries"] = []
        trip["data_stale"] = stale
        return trip

    # 2. Transfer Trips (Pre-fetch once)
    leg1_results, leg1_stale = _fetch_trips(origin, transfer_station)
    if not leg1_results:
        return septa_fallback("No trips found", raw_stale or leg1_stale)

    leg2_results, leg2_stale = _fetch_trips(transfer_station, destination)
    data_stale = raw_stale or leg1_stale or leg2_stale
    if not leg2_results:
        return septa_fallback(f"No connecting trains found at {transfer_station}", data_stale)

    leg1 = _pick(leg1_results, target_time, walk_time)
    plan = _plan_connection(leg1, leg2_results)
    if plan is None:
        # SEPTA's list of connecting trains ends before leg 1 reaches the
        # transfer station, so the real connection isn't visible yet. Don't
        # call that a missed connection; use SEPTA's own pick instead.
        return septa_fallback("No viable connections found", data_stale)

    trip = _assemble_transfer_trip(
        leg1, plan, leg2_results, transfer_station, walk_time,
        backup_departure=_next_backup_departure(leg1_results, leg1),
        data_stale=data_stale,
    )
    trip["itineraries"] = _transfer_itineraries(leg1_results, leg1, leg2_results)
    return trip


def get_connection_update(trip: dict, destination: str, fallback_late: int | None = None) -> dict:
    """Re-plans the connection while the rider is on the first train.

    NextToArrive stops listing a train once it leaves the rider's station, so
    get_commute can't follow it any more. Instead this takes the first train's
    live delay from TrainView and pairs it with the connecting trains now
    listed at the transfer station. `trip` is an earlier get_commute result
    for a transfer trip. Returns a trip dict like get_commute's, or
    {"error": ...} when the live delay or the connecting trains aren't known
    (never guessed as on time). `fallback_late` is used only when TrainView
    has no delay for the train; the caller passes it once the train should
    already have reached the transfer station."""
    station = trip["connection_station"]
    late = _fetch_live_train_metadata(trip.get("origin_train", "")).get("late_minutes")
    if late is None:
        late = fallback_late
    if late is None:
        return {"error": f"Train {trip.get('origin_train')} has no live position"}

    leg2_results, data_stale = _fetch_trips(station, destination)
    if not leg2_results:
        return {"error": f"No connecting trains listed at {station}"}

    leg1 = {
        "orig_train": trip.get("origin_train"),
        "orig_line": trip.get("origin_line"),
        "orig_departure_time": trip["origin_departure_time"],
        "orig_arrival_time": trip["origin_arrival_time"],
        "orig_delay": f"{late} min" if late else "On time",
    }
    plan = _plan_connection(leg1, leg2_results)
    if plan is None:
        return {"error": f"No catchable connection listed at {station}"}

    trip = _assemble_transfer_trip(leg1, plan, leg2_results, station, walk_time=0,
                                   backup_departure=None, data_stale=data_stale)
    trip["itineraries"] = []
    return trip


def _assemble_transfer_trip(
    leg1: dict, plan: dict, leg2_results: list, transfer_station: str, walk_time: int,
    backup_departure: str | None, data_stale: bool,
) -> dict:
    """Turns a first-leg train and its planned connection into the trip dict
    the dashboard and the pushes read."""
    leg2_chosen = plan["leg2"]
    chosen = {
        "orig_train": leg1.get("orig_train"),
        "orig_line": leg1.get("orig_line"),
        "orig_departure_time": leg1["orig_departure_time"],
        "orig_delay": leg1.get("orig_delay", "On time"),
        "orig_arrival_time": leg1["orig_arrival_time"],
        "term_train": leg2_chosen.get("orig_train"),
        "term_line": leg2_chosen.get("orig_line"),
        "term_depart_time": leg2_chosen["orig_departure_time"],
        "term_delay": leg2_chosen.get("orig_delay", "On time"),
        "term_arrival_time": leg2_chosen["orig_arrival_time"],
        "Connection": transfer_station,
        "isdirect": "false",
    }

    trip = _build_transfer_trip(
        chosen, walk_time, alternatives=leg2_results,
        backup_departure=backup_departure,
        planned_arrival=plan["planned_arrival"],
    )
    trip["missed_connection"] = plan["missed_connection"]
    trip["planned_connection_cancelled"] = plan["planned_connection_cancelled"]
    trip["planned_connection_train"] = plan["planned_connection_train"]
    if plan["planned_connection_cancelled"]:
        trip["delayed"] = True
    trip["data_stale"] = data_stale
    return trip


def get_commute_leg(origin: str, destination: str) -> dict:
    return get_commute(origin, destination)
