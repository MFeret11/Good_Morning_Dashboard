"""Commute trip-finding logic: talks to SEPTA's NextToArrive endpoint and
builds a clean, delay-aware trip summary (direct or transfer)."""
from datetime import datetime, timedelta

import requests

from app.config import (
    SEPTA_BASE, WALK_TIMES, DEFAULT_WALK_TIME_MINUTES, RISK_BUFFER_MINUTES,
    SIGNIFICANT_DELAY_MINUTES, LEAVE_NOW_THRESHOLD_MINUTES,
)
from app.time_utils import parse_time, parse_delay_minutes, format_time_no_leading_zero, anchor_to_today


def get_commute_leg(origin: str, destination: str) -> dict:
    walk_time = WALK_TIMES.get(origin, DEFAULT_WALK_TIME_MINUTES)

    params = {"req1": origin, "req2": destination, "top": 5}
    results = requests.get(f"{SEPTA_BASE}/NextToArrive/index.php", params=params).json()

    if not results:
        return {"error": "No trips found"}

    direct_trip = next((r for r in results if r.get("isdirect") == "true"), None)
    chosen = direct_trip if direct_trip else results[0]

    is_direct = chosen["isdirect"] == "true"
    origin_delay = parse_delay_minutes(chosen["orig_delay"])
    origin_actual_departure = parse_time(chosen["orig_departure_time"]) + timedelta(minutes=origin_delay)
    leave_by = origin_actual_departure - timedelta(minutes=walk_time)

    now = datetime.now()
    leave_by_today = anchor_to_today(now, leave_by)
    minutes_until_leave_by = round((leave_by_today - now).total_seconds() / 60)

    trip = {
        "trip_type": "direct" if is_direct else "transfer",
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
        "at_risk": False,
        "delayed": False,
        "total_delay_minutes": origin_delay,
    }

    if not is_direct:
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

        trip.update({
            "connection_station": chosen.get("Connection"),
            "connection_train": chosen.get("term_train"),
            "connection_line": chosen.get("term_line"),
            "connection_departure_time": chosen.get("term_depart_time"),
            "connection_delay_minutes": connection_delay,
            "transfer_buffer_minutes": transfer_buffer,
            "arrival_time": chosen.get("term_arrival_time"),
            "actual_arrival_time": format_time_no_leading_zero(actual_final_arrival),
            "total_delay_minutes": total_delay_minutes,
        })
        trip["at_risk"] = transfer_buffer < RISK_BUFFER_MINUTES
        trip["delayed"] = total_delay_minutes >= SIGNIFICANT_DELAY_MINUTES
    else:
        scheduled_final_arrival = parse_time(chosen["orig_arrival_time"])
        actual_final_arrival = scheduled_final_arrival + timedelta(minutes=origin_delay)
        trip["arrival_time"] = chosen.get("orig_arrival_time")
        trip["actual_arrival_time"] = format_time_no_leading_zero(actual_final_arrival)
        trip["total_delay_minutes"] = origin_delay
        trip["delayed"] = origin_delay >= SIGNIFICANT_DELAY_MINUTES

    trip["alternatives"] = results
    return trip
