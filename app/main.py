"""Headless REST API microservice for SEPTA Regional Rail and Open-Meteo commute telemetry."""
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Query

from app.config import (
    DEFAULT_HOME_STATION, DEFAULT_WORK_STATION, DEFAULT_TRANSFER_STATION,
    MORNING_START, MORNING_END, AFTERNOON_START, AFTERNOON_END,
    ACTIVE_POLL_INTERVAL_MS, IDLE_POLL_INTERVAL_MS,
    AFTERNOON_TARGET_DEPARTURE_TIME,
)
from app.commute import get_commute
from app.alerts import get_alerts
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
    if MORNING_START <= hour < MORNING_END:
        return "morning"
    elif AFTERNOON_START <= hour < AFTERNOON_END:
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
    target_time: Optional[str] = Query(None, description="Target departure time (e.g. '4:50PM')"),
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


@app.get("/api/dashboard")
def get_dashboard(
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    target_time: Optional[str] = None,
):
    """Master telemetry aggregator for frontend kiosk and Home Assistant polling."""
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

    alerts = get_alerts()
    weather = get_weather()

    # Determine unified priority status for dashboard headers & chips
    if "error" in commute:
        overall_status = "error" if window else "idle"
    elif alerts.get("has_critical_alerts"):
        overall_status = "alert"
    elif commute.get("is_cancelled") or commute.get("connection_cancelled"):
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
    weather_display = {k: v for k, v in weather.items() if k != "hourly_detail"}

    return {
        "active_window": window,
        "poll_interval_ms": ACTIVE_POLL_INTERVAL_MS if window else IDLE_POLL_INTERVAL_MS,
        "overall_status": overall_status,
        "commute": commute_display,
        "alerts": alerts,
        "weather": weather_display,
    }