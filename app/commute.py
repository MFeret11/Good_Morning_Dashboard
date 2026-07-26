"""Commute trip-finding logic: talks to SEPTA's NextToArrive endpoint and
builds a clean, delay-aware trip summary (direct or transfer).

Transfer handling: SEPTA's NextToArrive picks its own transfer station when
you query origin->destination directly, and there's no way to request a
specific one from that endpoint. To honor PREFERRED_TRANSFER_STATION, we
instead query origin->transfer_station and transfer_station->destination
separately and stitch them together ourselves (see
_get_commute_via_transfer). If that stitched connection isn't realistically
catchable, we fall back to SEPTA's own single-call pick rather than show an
unreachable connection.
"""
from datetime import datetime, timedelta

import requests

from app.config import (
    SEPTA_BASE, WALK_TIMES, DEFAULT_WALK_TIME_MINUTES, RISK_BUFFER_MINUTES,
    SIGNIFICANT_DELAY_MINUTES, LEAVE_NOW_THRESHOLD_MINUTES, PREFERRED_TRANSFER_STATION,
    MISSED_CONNECTION_BUFFER_MINUTES,
)
from app.time_utils import parse_time, parse_delay_minutes, format_time_no_leading_zero, anchor_to_today


def _fetch_trips(origin: str, destination: str) -> list:
    params = {"req1": origin, "req2": destination, "top": 5}
    return requests.get(f"{SEPTA_BASE}/NextToArrive/index.php", params=params).json()


def _build_walk_and_leave_by(chosen: dict, walk_time: int) -> dict:
    """Fields shared by direct and transfer trips: walk-time-aware leave-by
    math based on the origin leg alone."""
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
        "origin_delay": origin_delay,  # used internally below, stripped by callers if unwanted
    }


def _build_direct_trip(chosen: dict, walk_time: int, alternatives: list) -> dict:
    base = _build_walk_and_leave_by(chosen, walk_time)
    origin_delay = base.pop("origin_delay")

    scheduled_final_arrival = parse_time(chosen["orig_arrival_time"])
    actual_final_arrival = scheduled_final_arrival + timedelta(minutes=origin_delay)

    trip = {
        "trip_type": "direct",
        **base,
        "at_risk": False,
        "delayed": origin_delay >= SIGNIFICANT_DELAY_MINUTES,
        "total_delay_minutes": origin_delay,
        "arrival_time": chosen.get("orig_arrival_time"),
        "actual_arrival_time": format_time_no_leading_zero(actual_final_arrival),
        "alternatives": alternatives,
    }
    return trip


def _build_transfer_trip(chosen: dict, walk_time: int, alternatives: list) -> dict:
    """Builds the standard transfer trip dict from a `chosen` dict shaped
    like SEPTA's own transfer result (orig_* / term_* / Connection keys) -
    used both for SEPTA's own single-call pick and for our manually-stitched
    two-leg connections (see _get_commute_via_transfer, which reshapes its
    leg1/leg2 results into this same shape before calling here)."""
    base = _build_walk_and_leave_by(chosen, walk_time)
    origin_delay = base.pop("origin_delay")

    connection_delay = parse_delay_minutes(chosen.get("term_delay", "On time"))
    scheduled_arrival = parse_time(chosen["orig_arrival_time"])
    actual_arrival = scheduled_arrival + timedelta(minutes=origin_delay)
    scheduled_departure = parse_time(chosen["term_depart_time"])
    transfer_buffer = (scheduled_departure - actual_arrival).total_seconds() / 60

    scheduled_final_arrival = parse_time(chosen["term_arrival_time"])
    actual_final_arrival = scheduled_final_arrival + timedelta(minutes=connection_delay)

    # Total delay felt by the rider = however much later she actually
    # arrives vs. the originally scheduled arrival (captures both a
    # delayed origin leg AND a delayed connection leg).
    total_delay_minutes = round(
        (actual_final_arrival - scheduled_final_arrival).total_seconds() / 60
    )

    trip = {
        "trip_type": "transfer",
        **base,
        "connection_station": chosen.get("Connection"),
        "connection_train": chosen.get("term_train"),
        "connection_line": chosen.get("term_line"),
        "connection_departure_time": chosen.get("term_depart_time"),
        "connection_delay_minutes": connection_delay,
        "transfer_buffer_minutes": transfer_buffer,
        "arrival_time": chosen.get("term_arrival_time"),
        "actual_arrival_time": format_time_no_leading_zero(actual_final_arrival),
        "total_delay_minutes": total_delay_minutes,
        "at_risk": transfer_buffer < RISK_BUFFER_MINUTES,
        "delayed": total_delay_minutes >= SIGNIFICANT_DELAY_MINUTES,
        "alternatives": alternatives,
    }
    return trip


def _get_commute_via_transfer(origin: str, transfer_station: str, destination: str) -> dict | None:
    """Manually stitches origin->transfer_station and transfer_station->destination
    into a single transfer trip, honoring PREFERRED_TRANSFER_STATION. Returns
    None if no leg-2 train is realistically catchable (below
    MISSED_CONNECTION_BUFFER_MINUTES), signaling the caller should fall back
    to SEPTA's own single-call pick."""
    leg1_results = _fetch_trips(origin, transfer_station)
    if not leg1_results:
        return None
    leg1 = leg1_results[0]  # SEPTA pre-sorts by soonest departure

    leg1_delay = parse_delay_minutes(leg1["orig_delay"])
    leg1_actual_arrival = parse_time(leg1["orig_arrival_time"]) + timedelta(minutes=leg1_delay)

    leg2_results = _fetch_trips(transfer_station, destination)
    if not leg2_results:
        return None

    catchable = [
        leg2 for leg2 in leg2_results
        if (parse_time(leg2["orig_departure_time"]) - leg1_actual_arrival).total_seconds() / 60
        >= MISSED_CONNECTION_BUFFER_MINUTES
    ]
    if not catchable:
        return None

    leg2_chosen = catchable[0]  # earliest catchable option

    # Reshape into the same orig_*/term_*/Connection structure SEPTA's own
    # transfer results use, so _build_transfer_trip can handle both cases
    # identically.
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
    return chosen


def get_commute_leg(origin: str, destination: str) -> dict:
    walk_time = WALK_TIMES.get(origin, DEFAULT_WALK_TIME_MINUTES)

    results = _fetch_trips(origin, destination)
    if not results:
        return {"error": "No trips found"}

    direct_trip = next((r for r in results if r.get("isdirect") == "true"), None)
    if direct_trip:
        return _build_direct_trip(direct_trip, walk_time, alternatives=results)

    # No direct trip exists - a transfer is required.
    if PREFERRED_TRANSFER_STATION:
        stitched = _get_commute_via_transfer(origin, PREFERRED_TRANSFER_STATION, destination)
        if stitched is not None:
            return _build_transfer_trip(stitched, walk_time, alternatives=results)
        # Stitched connection wasn't catchable - fall back to SEPTA's own pick below.

    chosen = results[0]
    return _build_transfer_trip(chosen, walk_time, alternatives=results)

def _pick_closest_to_target(results: list, target_time_str: str) -> dict:
    """Picks the result whose departure time is closest to a known target
    (e.g. "the ~4:50pm train we always take"), rather than the soonest
    upcoming one - SEPTA's list may include earlier trains that aren't the
    one actually being ridden."""
    target = parse_time(target_time_str)
    return min(results, key=lambda r: abs((parse_time(r["orig_departure_time"]) - target).total_seconds()))


def get_afternoon_commute(origin: str, transfer_station: str, destination: str, target_departure_time: str) -> dict:
    """Anchors to the specific known afternoon train (target_departure_time)
    from origin instead of SEPTA's soonest-next pick, and applies live delay
    data to both legs. If the connection can't realistically be made, does
    NOT silently fall back to SEPTA's own pick (unlike get_commute_leg) -
    instead returns the next realistic leg2 option, flagged as missed, so
    the rider knows their usual train isn't going to make it."""
    walk_time = WALK_TIMES.get(origin, DEFAULT_WALK_TIME_MINUTES)

    leg1_results = _fetch_trips(origin, transfer_station)
    if not leg1_results:
        return {"error": "No trips found for origin leg"}
    leg1 = _pick_closest_to_target(leg1_results, target_departure_time)

    leg1_delay = parse_delay_minutes(leg1["orig_delay"])
    leg1_actual_arrival = parse_time(leg1["orig_arrival_time"])  
    timedelta(minutes=leg1_delay)

    leg2_results = _fetch_trips(transfer_station, destination)
    if not leg2_results:
        return {"error": "No trips found for transfer leg"}

    catchable = [
        leg2 for leg2 in leg2_results
        if (parse_time(leg2["orig_departure_time"]) - leg1_actual_arrival).total_seconds() / 60
        >= MISSED_CONNECTION_BUFFER_MINUTES
    ]
    missed_connection = not catchable
    leg2_chosen = catchable[0] if catchable else leg2_results[0]

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