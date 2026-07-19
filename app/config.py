"""Central place for all constants and configuration values."""
import os

from dotenv import load_dotenv

load_dotenv()  # reads .env file into environment variables, if present

SEPTA_BASE = "https://www3.septa.org/api"

# --- Stations ---
HOME_STATION = "Media"
WORK_STATION = "East Falls"

# --- Walk times (minutes), keyed by the station being walked FROM ---
WALK_TIMES = {
    "Media": 5,        # home -> Media station (measured 4:15, rounded up for buffer)
    "East Falls": 15,  # office -> East Falls station
}
DEFAULT_WALK_TIME_MINUTES = 5

# --- Commute risk / timing ---
RISK_BUFFER_MINUTES = 0  # transfer buffer below this is flagged "at_risk"
SIGNIFICANT_DELAY_MINUTES = 10  # total trip delay above this is flagged "delayed"
LEAVE_NOW_THRESHOLD_MINUTES = 3  # minutes_until_leave_by below this (incl. negative) = urgent
PREFERRED_TRANSFER_STATION = "Jefferson Station"
# Manually-stitched transfer buffer below this = the connecting train has
# effectively already left before the rider's train arrives (not just tight,
# genuinely not catchable). Below this floor, get_commute_leg() falls back
# to SEPTA's own single-call transfer pick instead of showing an unreachable
# connection. This is intentionally lower/stricter-in-name than
# RISK_BUFFER_MINUTES, which only controls the "at_risk" UI warning on an
# otherwise-valid connection.
MISSED_CONNECTION_BUFFER_MINUTES = -5

# --- Active commute windows (24hr clock) ---
MORNING_START, MORNING_END = 5, 9      # 5am - 9am
AFTERNOON_START, AFTERNOON_END = 14, 19  # 2pm - 7pm

# --- Frontend polling cadence ---
# The frontend used to keep its own separate copy of these hour ranges to
# decide how often to poll, which drifted out of sync with the ranges above.
# It now just reads poll_interval_ms back from /api/dashboard instead.
ACTIVE_POLL_INTERVAL_MS = 60_000      # 1 min during morning/afternoon windows
IDLE_POLL_INTERVAL_MS = 1_800_000     # 30 min otherwise

# --- Alerts ---
RELEVANT_LINES = {"Media/Wawa", "Manayunk/Norristown"}
ADVISORY_RECENCY_DAYS = 3

# --- Notifications ---
# NTFY_TOPIC must be set via environment variable (.env file) - never hardcode
# it here, since this file gets committed to git and the topic acts like a
# shared secret (anyone who knows it can read/send to it on public ntfy.sh).
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
NTFY_BASE_URL = "https://ntfy.sh"
# When the daily afternoon check fires (24hr clock, weekdays only)
NOTIFY_HOUR = 16
NOTIFY_MINUTE = 30

# --- Weather ---
WEATHER_LAT = 39.9168
WEATHER_LON = -75.3880  # Media, PA
WEATHER_WINDOW_START, WEATHER_WINDOW_END = 5, 20  # 5am - 8pm

WMO_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

# Codes severe enough to flag regardless of how often they occur in the day AKA precipitation
SEVERE_CODES = {
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
}
