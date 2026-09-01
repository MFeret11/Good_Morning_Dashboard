"""Universal Transit Resolver: Direct & Transfer routing, live platform tracking,
in-memory circuit-breaker fallback cache, and GPS stall detection."""
import time
from datetime import datetime, timedelta
import requests

from app.config import (
    SEPTA_BASE, WALK_TIMES, DEFAULT_WALK_TIME_MINUTES, RISK_BUFFER_MINUTES,
    SIGNIFICANT_DELAY_MINUTES, LEAVE_NOW_THRESHOLD_MINUTES,
    MISSED_CONNECTION_BUFFER_MINUTES,
)
from app.time_utils import parse_time, parse_delay_minutes, format_time_no_leading_zero, anchor_to_today

# In-Memory Cache Structures
_TRIP_CACHE = {}          # (origin, destination) -> (data, timestamp)
_TRAIN_POSITIONS = {}     # train_no -> {"stop": str, "lat": float, "lon": float, "first_seen_at": float}
CACHE_TTL_SECONDS = 300   # 5-minute fallback cache window
STALL_THRESHOLD_SECONDS = 600  # 10 minutes at the same stop = stalled


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


def _fetch_live_train_metadata(train_number: str) -> dict:
    """Fetches TrainView telemetry and runs GPS stall analysis."""
    if not train_number or not str(train_number).isdigit():
        return {}
    
    now = time.time()
    try:
        response = requests.get(
            f"{SEPTA_BASE}/TrainView/index.php",
            params={"req1": "TrainView", "req2": "TrainView"},
            timeout=5,
        )
        trains = response.json()
        if not isinstance(trains, list):
            return {}

        match = next((t for t in trains if str(t.get("trainno")) == str(train_number)), None)
        if match:
            current_stop = match.get("currentstop", "Unknown")
            lat = match.get("lat")
            lon = match.get("lon")
            track = match.get("track", "TBD")

            is_stalled = False
            stall_minutes = 0

            if train_number in _TRAIN_POSITIONS:
                prev = _TRAIN_POSITIONS[train_number]
                if prev["stop"] == current_stop and current_stop != "Unknown":
                    duration = now - prev["first_seen_at"]
                    if duration >= STALL_THRESHOLD_SECONDS:
                        is_stalled = True
                        stall_minutes = round(duration / 60)
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
    except Exception:
        pass
    return {}


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
    delayed = total_delay_minutes >= SIGNIFICANT_DELAY_MINUTES or base["is_cancelled"] or connection_cancelled or at_risk

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
        "alternatives": alternatives,
    }


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

    # 1. Direct Trips
    direct_trips = [r for r in raw_results if r.get("isdirect") == "true"]
    if direct_trips:
        chosen_direct = _pick_closest_to_target(direct_trips, target_time) if target_time else direct_trips[0]
        return _build_direct_trip(chosen_direct, walk_time, alternatives=raw_results)

    # 2. Transfer Trips
    transfer_station = preferred_transfer or "Jefferson Station"
    leg1_results = _fetch_trips(origin, transfer_station)
    if not leg1_results:
        if raw_results:
            return _build_transfer_trip(raw_results[0], walk_time, alternatives=raw_results)
        return {"error": f"No trips found"}

    leg2_results = _fetch_trips(transfer_station, destination)
    if not leg2_results:
        if raw_results:
            return _build_transfer_trip(raw_results[0], walk_time, alternatives=raw_results)
        return {"error": f"No connecting trains found at {transfer_station}"}

    leg1 = _pick_closest_to_target(leg1_results, target_time) if target_time else leg1_results[0]
    leg1_delay = parse_delay_minutes(leg1.get("orig_delay", "On time"))
    leg1_actual_arrival = parse_time(leg1["orig_arrival_time"]) + timedelta(minutes=leg1_delay)

    # Filter catchable leg2 options
    catchable = []
    for leg2 in leg2_results:
        leg2_delay = parse_delay_minutes(leg2.get("orig_delay", "On time"))
        leg2_actual_depart = parse_time(leg2["orig_departure_time"]) + timedelta(minutes=leg2_delay)
        buffer_min = (leg2_actual_depart - leg1_actual_arrival).total_seconds() / 60
        if buffer_min >= MISSED_CONNECTION_BUFFER_MINUTES:
            catchable.append((leg2, buffer_min))

    # Connection uncatchable: Fall back to SEPTA single-call if not anchored to target_time
    if not catchable:
        if target_time:
            missed_connection = True
            leg2_chosen = leg2_results[0]
        elif raw_results:
            return _build_transfer_trip(raw_results[0], walk_time, alternatives=raw_results)
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
    return trip


def get_commute_leg(origin: str, destination: str) -> dict:
    """Preserved for test suite backwards compatibility."""
    return get_commute(origin, destination)