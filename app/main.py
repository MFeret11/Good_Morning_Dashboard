from datetime import datetime

import requests
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import (
    SEPTA_BASE, HOME_STATION, WORK_STATION,
    MORNING_START, MORNING_END, AFTERNOON_START, AFTERNOON_END,
)
from app.commute import get_commute_leg
from app.alerts import get_alerts
from app.weather import get_weather
from app.scheduler import start_scheduler
from app.afternoon_check import run_afternoon_check

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
def on_startup():
    start_scheduler()


@app.get("/dashboard")
def serve_dashboard():
    return FileResponse("static/index.html")


@app.get("/")
def read_root():
    return {"status": "ok", "message": "SEPTA dashboard backend running"}


# --- Debug/raw endpoints, handy for inspecting SEPTA's raw responses ---

@app.get("/api/leg1")
def get_leg1():
    """Media -> 30th Street Station (raw)"""
    params = {"req1": HOME_STATION, "req2": "30th Street Station", "top": 5}
    response = requests.get(f"{SEPTA_BASE}/NextToArrive/index.php", params=params)
    return response.json()


@app.get("/api/leg2")
def get_leg2():
    """30th Street Station -> East Falls (raw)"""
    params = {"req1": "30th Street Station", "req2": WORK_STATION, "top": 5}
    response = requests.get(f"{SEPTA_BASE}/NextToArrive/index.php", params=params)
    return response.json()


# --- Core endpoints ---

@app.get("/api/commute")
def get_commute():
    """Morning commute: home -> work"""
    return get_commute_leg(HOME_STATION, WORK_STATION)


@app.get("/api/commute_return")
def get_commute_return():
    """Afternoon return: work -> home"""
    return get_commute_leg(WORK_STATION, HOME_STATION)


@app.get("/api/alerts")
def api_get_alerts():
    return get_alerts()


@app.get("/api/weather")
def api_get_weather():
    return get_weather()


@app.post("/api/test_notification")
def test_notification():
    """Manually trigger the afternoon check/notification, for testing without
    waiting for the scheduled time."""
    run_afternoon_check()
    return {"status": "triggered"}


def get_active_window() -> str | None:
    hour = datetime.now().hour
    if MORNING_START <= hour < MORNING_END:
        return "morning"
    elif AFTERNOON_START <= hour < AFTERNOON_END:
        return "afternoon"
    return None


@app.get("/api/dashboard")
def get_dashboard():
    window = get_active_window()

    if window == "morning":
        commute = get_commute_leg(HOME_STATION, WORK_STATION)
    elif window == "afternoon":
        commute = get_commute_leg(WORK_STATION, HOME_STATION)
    else:
        commute = {"error": "Outside active commute windows"}

    alerts = get_alerts()
    weather = get_weather()

    if alerts.get("has_critical_alerts"):
        overall_status = "alert"
    elif commute.get("at_risk"):
        overall_status = "at_risk"
    elif commute.get("delayed"):
        overall_status = "delayed"
    else:
        overall_status = "ok"

    commute_display = {k: v for k, v in commute.items() if k != "alternatives"}
    weather_display = {k: v for k, v in weather.items() if k != "hourly_detail"}

    return {
        "active_window": window,
        "overall_status": overall_status,
        "commute": commute_display,
        "alerts": alerts,
        "weather": weather_display,
    }
