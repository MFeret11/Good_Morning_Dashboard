import re
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI

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


@app.get("/api/commute")
def get_commute():
    leg1_params = {"req1": "Media", "req2": "30th Street Station", "top": 5}
    leg2_params = {"req1": "30th Street Station", "req2": "East Falls", "top": 5}

    leg1 = requests.get(f"{SEPTA_BASE}/NextToArrive/index.php", params=leg1_params).json()
    
    leg2 = requests.get(f"{SEPTA_BASE}/NextToArrive/index.php", params=leg2_params).json()

    MIN_TRANSFER_MINUTES = 8
    trip = None

    for l1 in leg1:
        scheduled_arrival = parse_time(l1["orig_arrival_time"])
        delay_minutes = parse_delay_minutes(l1["orig_delay"])
        actual_arrival = scheduled_arrival + timedelta(minutes=delay_minutes)

        for l2 in leg2:
            departure = parse_time(l2["orig_departure_time"])
            gap = (departure - actual_arrival).total_seconds() / 60
            if gap >= MIN_TRANSFER_MINUTES:
                trip = {
                    "leg1": l1,
                    "leg2": l2,
                    "leg1_delay_minutes": delay_minutes,
                    "transfer_wait_minutes": gap,
                    "at_risk": gap < 15  # flag tight connections even if technically feasible
                }
                break
        if trip:
            break

    return trip