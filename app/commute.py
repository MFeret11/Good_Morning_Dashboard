"""Universal Transit Resolver: Direct & Transfer routing, Next-3 Itinerary Matrix,
live platform tracking via TrainView, circuit-breaker fallback cache, and GPS stall detection."""
import time
from datetime import datetime, timedelta
import requests

from app.config import (
    SEPTA_BASE, WALK_TIMES, DEFAULT_WALK_TIME_MINUTES, RISK_BUFFER_MINUTES,
    SIGNIFICANT_DELAY_MINUTES, LEAVE_NOW_THRESHOLD_MINUTES,
    MISSED_CONNECTION_BUFFER_MINUTES, DEFAULT_TRANSFER_STATION,
)
from app.time_utils import parse_time, parse_delay_minutes, format_time_no_leading_zero, anchor_to_today

# In-Memory Caches
_TRIP_CACHE = {}          # (origin, destination) -> (data, timestamp)
_TRAIN_POSITIONS = {}     # train_no -> {"stop": str, "lat": float, "lon": float, "first_seen_at": float}
_TRAINVIEW_CACHE = ([], 0.0) # (trains_list, timestamp)

CACHE_TTL_SECONDS = 300
TRAINVIEW_TTL_SECONDS = 15
STALL_THRESHOLD_SECONDS = 600


def _fetch_trips(origin: str, destination: str) -> list:
    cache_key = (origin, destination)
    now = time.time()
    params = {"req1": origin, "req2": destination, "top": 6}

    try:
        response = requests.get(f"{SEPTA_BASE}/NextToArrive/index.php", params=params, timeout=8)
        data = response.json()
        if isinstance(data, list):
            if len(data) > 0:
                _TRIP_CACHE[cache_key] = (data, now)
            return data
    except Exception:
        if cache_key in _TRIP_CACHE:
            cached_data, cached_at = _TRIP_CACHE[cache_key]
            if (now - cached_at) <= CACHE_TTL_SECONDS:
                return cached_data

    return []


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
        data = response.json()
        if isinstance(data, list):
            _TRAINVIEW_CACHE = (data, now)
            return data
    except Exception:
        pass

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

    return {
        "track": track,
        "current_stop": current_stop,
        "is_stalled": is_stalled,
        "stall_minutes": stall_minutes,
        "consist": match.get("consist"),
    }


def _build_walk_and_leave_by(chosen: dict, walk_time: int) -> dict:
    delay_str = chosen.get("orig_delay", "On time")
    is_cancelled = "cancel" in str(delay_str).lower()
    origin_delay = parse_delay_minutes(delay_str)

    origin_actual_departure = parse_time(chosen["orig_departure_time"]) + timedelta(minutes=origin_delay)
    leave_by = origin_actual_departure - timedelta(minutes=walk_time)

    now = datetime.now()
    leave_by_today = anchor_to_today(now, leave_by)
    minutes_until_leave_by = round((leave_by_today - now).total_seconds() / 60)

    meta = _fetch_live_train_metadata(chosen.get("orig_train", ""))

    return {
        "origin_train": chosen.get("orig_train"),
        "origin_line": chosen.get("orig_line"),
        "origin_departure_time": chosen.get("orig_departure_time"),
        "origin_actual_departure_time": format_time_no_leading_zero(origin_actual_departure),
        "origin_delay_minutes": origin_delay,
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


def _build_direct_trip(chosen: dict, walk_time: int, alternatives: list) -> dict:
    base = _build_walk_and_leave_by(chosen, walk_time)
    origin_delay = base["origin_delay_minutes"]

    scheduled_final_arrival = parse_time(chosen["orig_arrival_time"])
    actual_final_arrival = scheduled_final_arrival + timedelta(minutes=origin_delay)

    next_alt = next((alt for alt in alternatives if alt.get("orig_train") != chosen.get("orig_train")), None)
    backup_departure = next_alt.get("orig_departure_time") if next_alt else None

    delayed = origin_delay >= SIGNIFICANT_DELAY_MINUTES or base["is_cancelled"] or base["is_stalled"]

    return {
        "trip_type": "direct",
        **base,
        "connection_station": None,
        "at_risk": base["is_stalled"],
        "delayed": delayed,
        "total_delay_minutes": origin_delay,
        "arrival_time": chosen.get("orig_arrival_time"),
        "actual_arrival_time": format_time_no_leading_zero(actual_final_arrival),
        "backup_train_departure": backup_departure,
        "alternatives": alternatives,
    }


def _build_transfer_trip(chosen: dict, walk_time: int, alternatives: list) -> dict:
    base = _build_walk_and_leave_by(chosen, walk_time)
    origin_delay = base["origin_delay_minutes"]

    term_delay_str = chosen.get("term_delay", "On time")
    connection_cancelled = "cancel" in str(term_delay_str).lower()
    connection_delay = parse_delay_minutes(term_delay_str)

    scheduled_arrival = parse_time(chosen["orig_arrival_time"])
    actual_arrival = scheduled_arrival + timedelta(minutes=origin_delay)

    scheduled_departure = parse_time(chosen["term_depart_time"])
    actual_departure = scheduled_departure + timedelta(minutes=connection_delay)
    transfer_buffer = (actual_departure - actual_arrival).total_seconds() / 60

    scheduled_final_arrival = parse_time(chosen["term_arrival_time"])
    actual_final_arrival = scheduled_final_arrival + timedelta(minutes=connection_delay)

    total_delay_minutes = round(
        (actual_final_arrival - scheduled_final_arrival).total_seconds() / 60
    )

    conn_meta = _fetch_live_train_metadata(chosen.get("term_train", ""))
    connection_stalled = conn_meta.get("is_stalled", False)

    at_risk = transfer_buffer < RISK_BUFFER_MINUTES or base["is_stalled"] or connection_stalled
    delayed = (
        total_delay_minutes >= SIGNIFICANT_DELAY_MINUTES
        or base["is_cancelled"]
        or connection_cancelled
        or at_risk
    )

    # Resolves the next available connecting train if primary is cancelled/missed
    # Handles both SEPTA direct query keys (orig_*) and transfer query keys (term_*)
    current_term_train = chosen.get("term_train")
    next_alt = next(
        (
            alt for alt in alternatives
            if (alt.get("term_train") or alt.get("orig_train")) != current_term_train
        ),
        None,
    )
    backup_departure = (
        (next_alt.get("term_depart_time") or next_alt.get("orig_departure_time"))
        if next_alt
        else None
    )

    return {
        "trip_type": "transfer",
        **base,
        "connection_station": chosen.get("Connection"),
        "connection_train": chosen.get("term_train"),
        "connection_line": chosen.get("term_line"),
        "connection_departure_time": chosen.get("term_depart_time"),
        "connection_actual_departure_time": format_time_no_leading_zero(actual_departure),
        "connection_delay_minutes": connection_delay,
        "connection_track": conn_meta.get("track", "TBD"),
        "connection_current_stop": conn_meta.get("current_stop", "En route"),
        "connection_is_stalled": connection_stalled,
        "connection_stall_minutes": conn_meta.get("stall_minutes", 0),
        "connection_cancelled": connection_cancelled,
        "transfer_buffer_minutes": round(transfer_buffer, 1),
        "arrival_time": chosen.get("term_arrival_time"),
        "actual_arrival_time": format_time_no_leading_zero(actual_final_arrival),
        "total_delay_minutes": total_delay_minutes,
        "at_risk": at_risk,
        "delayed": delayed,
        "backup_train_departure": backup_departure,
        "alternatives": alternatives,
    }


def _build_itinerary_matrix(
    raw_results: list,
    leg1_results: list,
    leg2_results: list,
) -> list:
    """Constructs Next-3 Feasible Commute Itineraries using pre-fetched leg data."""
    itineraries = []

    direct_trips = [r for r in raw_results if r.get("isdirect") == "true"]
    direct_train_numbers = {r.get("orig_train") for r in direct_trips}
    candidates = leg1_results if leg1_results else raw_results

    for idx, leg1 in enumerate(candidates[:3]):
        train_no = leg1.get("orig_train")

        if train_no in direct_train_numbers:
            matching_direct = next((r for r in direct_trips if r.get("orig_train") == train_no), leg1)
            delay = parse_delay_minutes(matching_direct.get("orig_delay", "On time"))
            act_dep = parse_time(matching_direct["orig_departure_time"]) + timedelta(minutes=delay)
            act_arr = parse_time(matching_direct["orig_arrival_time"]) + timedelta(minutes=delay)
            itineraries.append({
                "option": idx + 1,
                "trip_type": "direct",
                "origin_train": matching_direct.get("orig_train"),
                "origin_line": matching_direct.get("orig_line"),
                "origin_actual_departure": format_time_no_leading_zero(act_dep),
                "origin_delay_minutes": delay,
                "connection_train": None,
                "transfer_buffer_minutes": 0,
                "actual_arrival_time": format_time_no_leading_zero(act_arr),
                "status": "delayed" if delay >= SIGNIFICANT_DELAY_MINUTES else "ok",
            })
            continue

        leg1_delay = parse_delay_minutes(leg1.get("orig_delay", "On time"))
        leg1_act_dep = parse_time(leg1["orig_departure_time"]) + timedelta(minutes=leg1_delay)
        leg1_act_arr = parse_time(leg1["orig_arrival_time"]) + timedelta(minutes=leg1_delay)

        catchable_leg2 = None
        best_buffer = -999.0

        for leg2 in leg2_results:
            leg2_delay = parse_delay_minutes(leg2.get("orig_delay", "On time"))
            leg2_act_dep = parse_time(leg2["orig_departure_time"]) + timedelta(minutes=leg2_delay)
            buf = (leg2_act_dep - leg1_act_arr).total_seconds() / 60
            if buf >= MISSED_CONNECTION_BUFFER_MINUTES:
                catchable_leg2 = leg2
                best_buffer = buf
                break

        if catchable_leg2:
            conn_delay = parse_delay_minutes(catchable_leg2.get("orig_delay", "On time"))
            final_arr = parse_time(catchable_leg2["orig_arrival_time"]) + timedelta(minutes=conn_delay)
            tot_delay = max(leg1_delay, conn_delay)

            status = "ok"
            if best_buffer < RISK_BUFFER_MINUTES:
                status = "at_risk"
            elif tot_delay >= SIGNIFICANT_DELAY_MINUTES:
                status = "delayed"

            itineraries.append({
                "option": idx + 1,
                "trip_type": "transfer",
                "origin_train": leg1.get("orig_train"),
                "origin_line": leg1.get("orig_line"),
                "origin_actual_departure": format_time_no_leading_zero(leg1_act_dep),
                "origin_delay_minutes": leg1_delay,
                "connection_train": catchable_leg2.get("orig_train"),
                "connection_line": catchable_leg2.get("orig_line"),
                "connection_actual_departure": format_time_no_leading_zero(parse_time(catchable_leg2["orig_departure_time"]) + timedelta(minutes=conn_delay)),
                "transfer_buffer_minutes": round(best_buffer, 1),
                "actual_arrival_time": format_time_no_leading_zero(final_arr),
                "status": status,
            })

    return itineraries


def _pick_closest_to_target(results: list, target_time_str: str) -> dict:
    target = parse_time(target_time_str)
    return min(results, key=lambda r: abs((parse_time(r["orig_departure_time"]) - target).total_seconds()))


def get_commute(
    origin: str,
    destination: str,
    target_time: str | None = None,
    preferred_transfer: str | None = None,
) -> dict:
    walk_time = WALK_TIMES.get(origin, DEFAULT_WALK_TIME_MINUTES)
    raw_results = _fetch_trips(origin, destination)
    transfer_station = preferred_transfer or DEFAULT_TRANSFER_STATION

    # 1. Direct Trips
    direct_trips = [r for r in raw_results if r.get("isdirect") == "true"]
    if direct_trips:
        chosen_direct = _pick_closest_to_target(direct_trips, target_time) if target_time else direct_trips[0]
        trip = _build_direct_trip(chosen_direct, walk_time, alternatives=raw_results)
        trip["itineraries"] = _build_itinerary_matrix(raw_results, [], [])
        return trip

    # 2. Transfer Trips (Pre-fetch once)
    leg1_results = _fetch_trips(origin, transfer_station)
    if not leg1_results:
        if raw_results:
            trip = _build_transfer_trip(raw_results[0], walk_time, alternatives=raw_results)
            trip["itineraries"] = []
            return trip
        return {"error": "No trips found"}

    leg2_results = _fetch_trips(transfer_station, destination)
    if not leg2_results:
        if raw_results:
            trip = _build_transfer_trip(raw_results[0], walk_time, alternatives=raw_results)
            trip["itineraries"] = []
            return trip
        return {"error": f"No connecting trains found at {transfer_station}"}

    leg1 = _pick_closest_to_target(leg1_results, target_time) if target_time else leg1_results[0]
    leg1_delay = parse_delay_minutes(leg1.get("orig_delay", "On time"))
    leg1_actual_arrival = parse_time(leg1["orig_arrival_time"]) + timedelta(minutes=leg1_delay)

    catchable = []
    for leg2 in leg2_results:
        leg2_delay = parse_delay_minutes(leg2.get("orig_delay", "On time"))
        leg2_actual_depart = parse_time(leg2["orig_departure_time"]) + timedelta(minutes=leg2_delay)
        buffer_min = (leg2_actual_depart - leg1_actual_arrival).total_seconds() / 60
        if buffer_min >= MISSED_CONNECTION_BUFFER_MINUTES:
            catchable.append((leg2, buffer_min))

    if not catchable:
        if target_time:
            missed_connection = True
            leg2_chosen = leg2_results[0]
        elif raw_results:
            trip = _build_transfer_trip(raw_results[0], walk_time, alternatives=raw_results)
            trip["itineraries"] = []
            return trip
        else:
            return {"error": "No viable connections found"}
    else:
        missed_connection = False
        leg2_chosen = catchable[0][0]

    chosen = {
        "orig_train": leg1["orig_train"],
        "orig_line": leg1["orig_line"],
        "orig_departure_time": leg1["orig_departure_time"],
        "orig_delay": leg1["orig_delay"],
        "orig_arrival_time": leg1["orig_arrival_time"],
        "term_train": leg2_chosen["orig_train"],
        "term_line": leg2_chosen["orig_line"],
        "term_depart_time": leg2_chosen["orig_departure_time"],
        "term_delay": leg2_chosen["orig_delay"],
        "term_arrival_time": leg2_chosen["orig_arrival_time"],
        "Connection": transfer_station,
        "isdirect": "false",
    }

    trip = _build_transfer_trip(chosen, walk_time, alternatives=leg2_results)
    trip["missed_connection"] = missed_connection
    # Pass pre-fetched legs directly to avoid duplicate HTTP requests
    trip["itineraries"] = _build_itinerary_matrix(raw_results, leg1_results, leg2_results)
    return trip


def get_commute_leg(origin: str, destination: str) -> dict:
    return get_commute(origin, destination)