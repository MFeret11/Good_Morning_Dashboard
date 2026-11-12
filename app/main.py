"""Headless REST API microservice for SEPTA Regional Rail and Open-Meteo commute telemetry."""
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel

from app.config import (
    DEFAULT_HOME_STATION, DEFAULT_WORK_STATION, DEFAULT_TRANSFER_STATION,
    DASHBOARD_MORNING_START, DASHBOARD_MORNING_END, DASHBOARD_AFTERNOON_START, DASHBOARD_AFTERNOON_END,
    ACTIVE_POLL_INTERVAL_MS, IDLE_POLL_INTERVAL_MS,
    AFTERNOON_TARGET_DEPARTURE_TIME,
)
from app import rider
from app.commute import get_commute, get_trip_for_boarded_train, untracked_listed_trains
from app.alerts import get_alerts, alert_summary, no_alerts
from app.weather import get_weather
from app.scheduler import start_scheduler
from app.afternoon_check import run_afternoon_check


# --- LIFESPAN CONTEXT MANAGER (FastAPI Startup / Shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes background schedules on startup."""
    start_scheduler()
    yield
    # Shutdown logic (if needed in the future) goes here


app = FastAPI(
    title="Commute Telemetry Microservice",
    version="2.0.0",
    lifespan=lifespan,
)


def get_active_window() -> str | None:
    """Determines active commute window based on day and 24h clock."""
    now = datetime.now()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return None
    hour = now.hour
    if DASHBOARD_MORNING_START <= hour < DASHBOARD_MORNING_END:
        return "morning"
    elif DASHBOARD_AFTERNOON_START <= hour < DASHBOARD_AFTERNOON_END:
        return "afternoon"
    return None


# ------------------------------------------------------------------------------
# ENDPOINTS
# ------------------------------------------------------------------------------

@app.get("/")
def read_root():
    return {
        "status": "ok",
        "service": "Commute Telemetry Microservice",
        "active_window": get_active_window(),
    }


@app.get("/api/commute")
def api_get_commute(
    origin: str = Query(DEFAULT_HOME_STATION, description="Origin station name"),
    destination: str = Query(DEFAULT_WORK_STATION, description="Destination station name"),
    target_time: Optional[str] = Query(None, description="Target departure time (e.g. '5:15PM')"),
    transfer: Optional[str] = Query(DEFAULT_TRANSFER_STATION, description="Preferred transfer station"),
):
    """Dynamic universal commute endpoint for any origin/destination pair."""
    return get_commute(origin, destination, target_time=target_time, preferred_transfer=transfer)


@app.get("/api/commute_morning")
def get_commute_morning():
    """Preset morning commute (Home -> Work)."""
    return get_commute(DEFAULT_HOME_STATION, DEFAULT_WORK_STATION)


@app.get("/api/commute_return")
def get_commute_return():
    """Preset afternoon return commute (Work -> Home with target departure)."""
    return get_commute(
        DEFAULT_WORK_STATION,
        DEFAULT_HOME_STATION,
        target_time=AFTERNOON_TARGET_DEPARTURE_TIME,
        preferred_transfer=DEFAULT_TRANSFER_STATION,
    )


@app.get("/api/alerts")
def api_get_alerts():
    """Returns active SEPTA advisories and system alerts."""
    return get_alerts()


@app.get("/api/weather")
def api_get_weather():
    """Returns Open-Meteo forecast and severe weather flags."""
    return get_weather()


@app.post("/api/test_notification")
def test_notification():
    """Manually triggers the afternoon commute push notification dispatch."""
    run_afternoon_check()
    return {"status": "triggered"}


class RiderFix(BaseModel):
    latitude: float
    longitude: float
    accuracy: Optional[float] = None
    last_seen: Optional[datetime] = None   # when the phone reported this position
    zone: Optional[str] = None             # Home Assistant state: 'home', 'not_home' or a zone name


@app.post("/api/location")
def post_location(fix: RiderFix):
    """Home Assistant posts the rider's GPS fix here. It is kept in memory
    only, to work out which train the rider is on; nothing echoes it back."""
    rider.update_fix(
        fix.latitude, fix.longitude, fix.accuracy,
        seen_at=fix.last_seen.timestamp() if fix.last_seen else None,
        zone=fix.zone,
    )
    return {"status": "ok"}


@app.get("/api/dashboard")
def get_dashboard(
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    target_time: Optional[str] = None,
):
    """Master telemetry aggregator for Home Assistant polling."""
    window = get_active_window()

    if origin and destination:
        commute = get_commute(origin, destination, target_time=target_time)
    elif window == "morning":
        commute = get_commute(DEFAULT_HOME_STATION, DEFAULT_WORK_STATION)
    elif window == "afternoon":
        commute = get_commute(
            DEFAULT_WORK_STATION,
            DEFAULT_HOME_STATION,
            target_time=AFTERNOON_TARGET_DEPARTURE_TIME,
            preferred_transfer=DEFAULT_TRANSFER_STATION,
        )
    else:
        commute = {"error": "Outside active commute windows"}

    # With a GPS fix, the afternoon card follows the train the rider is really
    # on, which is often not the one nearest the target time.
    who = rider.current() if not (origin and destination) else None
    on_train = who["on_train"] if who else None
    # Moving at train speed with no located train alongside: if exactly one of
    # today's listed trains has no GPS position in TrainView, that is the one.
    untracked = False
    if who and not on_train and who["riding_untracked"] and window:
        candidates = untracked_listed_trains()
        if len(candidates) == 1:
            on_train, untracked = candidates[0], True
    if on_train and "error" not in commute and on_train != commute.get("origin_train") \
            and (window == "afternoon" or (window == "morning" and untracked)):
        ends = (DEFAULT_WORK_STATION, DEFAULT_HOME_STATION) if window == "afternoon" \
            else (DEFAULT_HOME_STATION, DEFAULT_WORK_STATION)
        boarded = get_trip_for_boarded_train(on_train, *ends, DEFAULT_TRANSFER_STATION)
        if boarded:
            commute = boarded
    if untracked and commute.get("origin_train") == on_train:
        # TrainView shows a train it can't see as parked at its first stop with
        # a delay that grows by the minute. The rider's own movement says it is
        # running, so neither the stall nor that delay is to be believed.
        commute = {**commute, "is_stalled": False, "stall_minutes": 0,
                   "origin_delay_unknown": True, "origin_delay_text": "no live position"}
        if commute.get("trip_type") == "direct":
            commute["at_risk"] = False

    # Outside commute hours nothing shows the alerts, so don't ask SEPTA for
    # them every time Home Assistant polls.
    alerts = get_alerts() if "error" not in commute or window else no_alerts()
    weather = get_weather()

    # Determine unified priority status for dashboard headers & chips
    if "error" in commute:
        overall_status = "error" if window else "idle"
    elif alerts.get("has_critical_alerts"):
        overall_status = "alert"
    elif (commute.get("is_cancelled") or commute.get("connection_cancelled")
          or commute.get("planned_connection_cancelled")):
        overall_status = "cancelled"
    elif commute.get("is_stalled") or commute.get("connection_is_stalled"):
        overall_status = "stalled"
    elif commute.get("missed_connection"):
        overall_status = "missed_connection"
    elif commute.get("at_risk"):
        overall_status = "at_risk"
    elif commute.get("delayed"):
        overall_status = "delayed"
    else:
        overall_status = "ok"

    # Strip heavy nested arrays from top-level summary payload
    commute_display = {k: v for k, v in commute.items() if k != "alternatives"}
    # Home Assistant imports only the commute section, so the line alerts the
    # cards must show ride along here, already reduced to plain text.
    commute_display["service_alerts"] = [alert_summary(a) for a in alerts.get("critical_alerts", [])]
    commute_display["alerts_unavailable"] = bool(alerts.get("alerts_unavailable"))
    commute_display["rider_on_train"] = on_train
    commute_display["rider_home"] = bool(who and who["home"])
    commute_display["rider_location_age_minutes"] = who["age_minutes"] if who and who["fresh"] else None
    weather_display = {k: v for k, v in weather.items() if k != "hourly_detail"}

    return {
        "active_window": window,
        "poll_interval_ms": ACTIVE_POLL_INTERVAL_MS if window else IDLE_POLL_INTERVAL_MS,
        "overall_status": overall_status,
        "commute": commute_display,
        "alerts": alerts,
        "weather": weather_display,
    }