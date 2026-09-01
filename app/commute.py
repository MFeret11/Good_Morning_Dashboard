"""Commute trip-finding logic: talks to SEPTA's NextToArrive endpoint and
builds a clean, delay-aware trip summary (direct or transfer)."""
from datetime import datetime, timedelta
import requests

from app.config import (
    SEPTA_BASE, WALK_TIMES, DEFAULT_WALK_TIME_MINUTES, RISK_BUFFER_MINUTES,
    SIGNIFICANT_DELAY_MINUTES, LEAVE_NOW_THRESHOLD_MINUTES, PREFERRED_TRANSFER_STATION,
    MISSED_CONNECTION_BUFFER_MINUTES,
)
from app.time_utils import parse_time, parse_delay_minutes, format_time_no_leading_zero, anchor_to_today


def _fetch_trips(origin: str, destination: str) -> list:
    params = {"req1": origin, "req2": destination, "top": 6}
    try:
        response = requests.get(f"{SEPTA_BASE}/NextToArrive/index.php", params=params, timeout=10)
        data = response.json()
        return data if isinstance(data, list) else []
    except (requests.RequestException, ValueError):
        return []


def _build_walk_and_leave_by(chosen: dict, walk_time: int) -> dict:
    origin_delay = parse_delay_minutes(chosen["orig_delay"])
    origin_actual_departure = parse_time(chosen["orig_departure_time"]) + timedelta(minutes=origin_delay)
    leave_by = origin_actual_departure - timedelta(minutes=walk_time)

    now = datetime.now()
    leave_by_today = anchor_to_today(now, leave_by)
    minutes_until_leave_by = round((leave_by_today - now).total_seconds() / 60)

    return {
        "origin_train": chosen["orig_train"],
        "origin_line": chosen["orig_line"],
        "origin_departure_time": chosen["orig_departure_time"],
        "origin_actual_departure_time": format_time_no_leading_zero(origin_actual_departure),
        "origin_delay_minutes": origin_delay,
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

    return {
        "trip_type": "direct",
        **base,
        "at_risk": False,
        "delayed": origin_delay >= SIGNIFICANT_DELAY_MINUTES,
        "total_delay_minutes": origin_delay,
        "arrival_time": chosen.get("orig_arrival_time"),
        "actual_arrival_time": format_time_no_leading_zero(actual_final_arrival),
        "alternatives": alternatives,
    }


def _build_transfer_trip(chosen: dict, walk_time: int, alternatives: list) -> dict:
    base = _build_walk_and_leave_by(chosen, walk_time)
    origin_delay = base["origin_delay_minutes"]

    connection_delay = parse_delay_minutes(chosen.get("term_delay", "On time"))
    scheduled_arrival = parse_time(chosen["orig_arrival_time"])
    actual_arrival = scheduled_arrival + timedelta(minutes=origin_delay)
    
    # Live departure of Leg 2 (including its own delay)
    scheduled_departure = parse_time(chosen["term_depart_time"])
    actual_departure = scheduled_departure + timedelta(minutes=connection_delay)
    
    # Real-world platform buffer at transfer station
    transfer_buffer = (actual_departure - actual_arrival).total_seconds() / 60

    scheduled_final_arrival = parse_time(chosen["term_arrival_time"])
    actual_final_arrival = scheduled_final_arrival + timedelta(minutes=connection_delay)

    total_delay_minutes = round(
        (actual_final_arrival - scheduled_final_arrival).total_seconds() / 60
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
        "transfer_buffer_minutes": round(transfer_buffer, 1),
        "arrival_time": chosen.get("term_arrival_time"),
        "actual_arrival_time": format_time_no_leading_zero(actual_final_arrival),
        "total_delay_minutes": total_delay_minutes,
        "at_risk": transfer_buffer < RISK_BUFFER_MINUTES,
        "delayed": total_delay_minutes >= SIGNIFICANT_DELAY_MINUTES,
        "alternatives": alternatives,
    }


def get_commute_leg(origin: str, destination: str) -> dict:
    walk_time = WALK_TIMES.get(origin, DEFAULT_WALK_TIME_MINUTES)
    results = _fetch_trips(origin, destination)
    if not results:
        return {"error": "No trips found"}

    direct_trip = next((r for r in results if r.get("isdirect") == "true"), None)
    if direct_trip:
        return _build_direct_trip(direct_trip, walk_time, alternatives=results)

    chosen = results[0]
    return _build_transfer_trip(chosen, walk_time, alternatives=results)


def _pick_closest_to_target(results: list, target_time_str: str) -> dict:
    target = parse_time(target_time_str)
    return min(results, key=lambda r: abs((parse_time(r["orig_departure_time"]) - target).total_seconds()))


def get_afternoon_commute(origin: str, transfer_station: str, destination: str, target_departure_time: str) -> dict:
    """Evaluates the afternoon return leg. Anchors to the ~4:50 PM train from
    East Falls, calculates live arrival at Jefferson Station, and evaluates the
    connecting train to Media with live delay-awareness."""
    walk_time = WALK_TIMES.get(origin, DEFAULT_WALK_TIME_MINUTES)

    # 1. Fetch Leg 1 (East Falls -> Jefferson)
    leg1_results = _fetch_trips(origin, transfer_station)
    if not leg1_results:
        return {"error": f"No trains found from {origin}"}
    
    leg1 = _pick_closest_to_target(leg1_results, target_departure_time)
    leg1_delay = parse_delay_minutes(leg1["orig_delay"])
    
    # FIXED: Added delay to actual arrival time
    leg1_actual_arrival = parse_time(leg1["orig_arrival_time"]) + timedelta(minutes=leg1_delay)

    # 2. Fetch Leg 2 (Jefferson -> Media)
    leg2_results = _fetch_trips(transfer_station, destination)
    if not leg2_results:
        return {"error": f"No connecting trains found at {transfer_station}"}

    # 3. Find truly catchable connecting trains (must depart >= 2 min after Leg 1 actual arrival)
    catchable = []
    for leg2 in leg2_results:
        leg2_delay = parse_delay_minutes(leg2.get("orig_delay", "On time"))
        leg2_actual_depart = parse_time(leg2["orig_departure_time"]) + timedelta(minutes=leg2_delay)
        buffer_min = (leg2_actual_depart - leg1_actual_arrival).total_seconds() / 60
        if buffer_min >= 2.0:  # Minimum 2-minute physical transfer buffer
            catchable.append((leg2, buffer_min))

    missed_connection = len(catchable) == 0
    leg2_chosen = catchable[0][0] if catchable else leg2_results[0]

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