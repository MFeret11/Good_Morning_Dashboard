import re
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI
from datetime import datetime as dt


app = FastAPI()

SEPTA_BASE = "https://www3.septa.org/api"


def parse_time(t):
    return datetime.strptime(t, "%I:%M%p")

def parse_delay_minutes(delay_str):
    """Convert SEPTA's delay string into minutes. 'On time' -> 0, '5 mins' -> 5."""
    if not delay_str or "on time" in delay_str.lower():
        return 0
    match = re.search(r"(\d+)", delay_str)
    return int(match.group(1)) if match else 0


@app.get("/")
def read_root():
    return {"status": "ok", "message": "SEPTA dashboard backend running"}


@app.get("/api/leg1")
def get_leg1():
    """Media -> 30th Street Station"""
    params = {"req1": "Media", "req2": "30th Street Station", "top": 5}
    response = requests.get(f"{SEPTA_BASE}/NextToArrive/index.php", params=params)
    return response.json()


@app.get("/api/leg2")
def get_leg2():
    """30th Street Station -> East Falls"""
    params = {"req1": "30th Street Station", "req2": "East Falls", "top": 5}
    response = requests.get(f"{SEPTA_BASE}/NextToArrive/index.php", params=params)
    return response.json()

@app.get("/api/schedule_test")
def schedule_test():
    params = {"req1": "Media", "req2": "East Falls"}
    response = requests.get(f"{SEPTA_BASE}/RRSchedules/index.php", params=params)
    return response.json()


RISK_BUFFER_MINUTES = 5  # how close to the wire before we flag it

@app.get("/api/commute")
def get_commute():
    params = {"req1": "Media", "req2": "East Falls", "top": 5}
    results = requests.get(f"{SEPTA_BASE}/NextToArrive/index.php", params=params).json()

    if not results:
        return {"error": "No trips found"}

    direct_trip = next((r for r in results if r.get("isdirect") == "true"), None)
    chosen = direct_trip if direct_trip else results[0]

    is_direct = chosen["isdirect"] == "true"
    orig_delay = parse_delay_minutes(chosen["orig_delay"])

    trip = {
        "type": "direct" if is_direct else "transfer",
        "orig_train": chosen["orig_train"],
        "orig_line": chosen["orig_line"],
        "departure_time": chosen["orig_departure_time"],
        "orig_delay_minutes": orig_delay,
        "at_risk": False,
    }

    if not is_direct:
        term_delay = parse_delay_minutes(chosen.get("term_delay", "On time"))

        # Calculate actual remaining transfer buffer after accounting for orig delay
        scheduled_arrival = parse_time(chosen["orig_arrival_time"])
        actual_arrival = scheduled_arrival + timedelta(minutes=orig_delay)
        scheduled_departure = parse_time(chosen["term_depart_time"])
        remaining_buffer = (scheduled_departure - actual_arrival).total_seconds() / 60

        trip.update({
            "connection_station": chosen.get("Connection"),
            "term_train": chosen.get("term_train"),
            "term_line": chosen.get("term_line"),
            "term_departure_time": chosen.get("term_depart_time"),
            "arrival_time": chosen.get("term_arrival_time"),
            "term_delay_minutes": term_delay,
            "remaining_transfer_buffer_minutes": remaining_buffer,
        })
        trip["at_risk"] = remaining_buffer < RISK_BUFFER_MINUTES
    else:
        trip["arrival_time"] = chosen.get("orig_arrival_time")

    trip["all_options"] = results
    return trip

RELEVANT_LINES = {"Media/Wawa", "Manayunk/Norristown"}

def is_recent(date_str, days=3):
    try:
        parsed = dt.strptime(date_str.strip(), "%b %d %Y %I:%M%p")
        return (datetime.now() - parsed).days <= days
    except (ValueError, AttributeError):
        return False


@app.get("/api/alerts")
def get_alerts():
    response = requests.get(f"{SEPTA_BASE}/Alerts/index.php").json()
    relevant = [r for r in response if r.get("route_name") in RELEVANT_LINES]

    critical_alerts = []
    standing_advisories = []

    for r in relevant:
        flags = {
            "delays": r.get("isdelays") == "Y",
            "suspended": r.get("issuspended") == "Y",
            "detour": r.get("isdetour") == "Y",
            "diversion": r.get("isdiversion") == "Y",
            "modified_service": r.get("ismodifiedservice") == "Y",
            "alert": r.get("isalert") == "Y",
        }
        is_advisory = r.get("isadvisory") == "Yes"
        recent = is_recent(r.get("last_updated", ""))

        if any(flags.values()):
            critical_alerts.append({
                "line": r.get("route_name"),
                "flags": flags,
                "alert_text": r.get("alert"),
                "last_updated": r.get("last_updated"),
            })
        elif is_advisory and recent:
            standing_advisories.append({
                "line": r.get("route_name"),
                "advisory_text": r.get("advisory"),
                "last_updated": r.get("last_updated"),
            })

    return {
        "has_critical_alerts": len(critical_alerts) > 0,
        "critical_alerts": critical_alerts,
        "has_recent_advisories": len(standing_advisories) > 0,
        "recent_advisories": standing_advisories,
    }

@app.get("/api/dashboard")
def get_dashboard():
    commute = get_commute()
    alerts = get_alerts()

    if alerts.get("has_critical_alerts"):
        overall_status = "alert"
    elif commute.get("at_risk"):
        overall_status = "at_risk"
    else:
        overall_status = "ok"

    return {
        "overall_status": overall_status,
        "commute": commute,
        "alerts": alerts,
    }