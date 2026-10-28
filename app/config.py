"""Central place for all constants and configuration values.

Anything specific to one rider (stations, walk times, target train, home
coordinates, push time) comes from environment variables, usually set in
.env. The defaults are placeholders; see .env.example."""
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_float(name: str, default: str) -> float:
    raw = _env(name, default)
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None


def _env_clock_time(name: str, default: str) -> str:
    """A SEPTA-style time like '5:15PM'. Checked at startup so a typo fails
    loudly instead of breaking the afternoon check at 4PM."""
    raw = _env(name, default).replace(" ", "").upper()
    try:
        datetime.strptime(raw, "%I:%M%p")
    except ValueError:
        raise ValueError(f"{name} must look like '5:15PM', got {raw!r}") from None
    return raw


def _env_walk_times(name: str, default: str) -> dict[str, int]:
    """'Station A:5,Station B:12' -> {'Station A': 5, 'Station B': 12}."""
    raw = _env(name, default)
    walk_times = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        station, _, minutes = item.rpartition(":")
        if not station.strip() or not minutes.strip().isdigit():
            raise ValueError(f"{name} entries must look like 'Station:5', got {item.strip()!r}")
        walk_times[station.strip()] = int(minutes)
    return walk_times


def _env_set(name: str, default: str) -> set[str]:
    return {item.strip() for item in _env(name, default).split(",") if item.strip()}


SEPTA_BASE = "https://www3.septa.org/api"

# --- Stations (SEPTA station names, exactly as NextToArrive spells them) ---
DEFAULT_HOME_STATION = _env("HOME_STATION", "Glenside")
DEFAULT_WORK_STATION = _env("WORK_STATION", "Suburban Station")
DEFAULT_TRANSFER_STATION = _env("TRANSFER_STATION", "Jefferson Station")
# Tried in order when no connection works at TRANSFER_STATION. Every
# Regional Rail line runs through these Center City stations.
TRANSFER_FALLBACK_STATIONS = [
    station.strip()
    for station in _env("TRANSFER_FALLBACK_STATIONS", "Suburban Station,30th Street Station").split(",")
    if station.strip()
]

# --- Walk times (minutes) to each station, keyed by origin station ---
WALK_TIMES = _env_walk_times("WALK_TIMES", "Glenside:5,Suburban Station:5,Jefferson Station:3")
DEFAULT_WALK_TIME_MINUTES = 5

# --- Timing & Thresholds ---
RISK_BUFFER_MINUTES = 0               # Transfer buffer below 0 is "at_risk" (cross-platform transfer)
SIGNIFICANT_DELAY_MINUTES = 10        # Trips with >= 10 min total delay are "delayed"
LEAVE_NOW_THRESHOLD_MINUTES = 3       # <= 3 min to leave = urgent "LEAVE NOW"
LEAVE_BY_GRACE_MINUTES = 2            # Still show a train whose leave-by passed up to 2 min ago; skip older ones
AFTERNOON_TARGET_DEPARTURE_TIME = _env_clock_time("AFTERNOON_TARGET_DEPARTURE", "5:00PM")
MISSED_CONNECTION_BUFFER_MINUTES = -5 # Floor below which connection is uncatchable

# --- Active Commute Windows (24hr clock) ---
MORNING_START, MORNING_END = 5, 9
AFTERNOON_START, AFTERNOON_END = 14, 19

# --- Polling Cadence ---
ACTIVE_POLL_INTERVAL_MS = 60_000      # 1 min during commute
IDLE_POLL_INTERVAL_MS = 1_800_000     # 30 min otherwise

# --- Alerts & Lines (route names as SEPTA's Alerts API spells them) ---
RELEVANT_LINES = _env_set("RELEVANT_LINES", "Lansdale/Doylestown")
ADVISORY_RECENCY_DAYS = 3

# --- Notifications ---
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
NTFY_BASE_URL = "https://ntfy.sh"
_notify_at = datetime.strptime(_env_clock_time("NOTIFY_TIME", "4:00PM"), "%I:%M%p")
NOTIFY_HOUR, NOTIFY_MINUTE = _notify_at.hour, _notify_at.minute

# --- Weather (defaults to Philadelphia City Hall) ---
WEATHER_LAT = _env_float("WEATHER_LAT", "39.9526")
WEATHER_LON = _env_float("WEATHER_LON", "-75.1652")
WEATHER_WINDOW_START, WEATHER_WINDOW_END = 5, 20

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
    55: "Dense drizzle", 56: "Light freezing drizzle", 57: "Dense freezing drizzle", 61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains", 80: "Slight rain showers",
    81: "Moderate rain showers", 82: "Violent rain showers", 85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}

SEVERE_CODES = {
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    85: "Slight snow showers", 86: "Heavy snow showers",
}

FREEZING_CODES = {56, 57, 66, 67}
SNOW_CODES = {71, 73, 75, 77, 85, 86}