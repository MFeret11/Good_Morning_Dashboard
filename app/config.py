"""Central place for all constants and configuration values."""
import os
from dotenv import load_dotenv

load_dotenv()

SEPTA_BASE = "https://www3.septa.org/api"

# --- Station Defaults ---
DEFAULT_HOME_STATION = "Media"
DEFAULT_WORK_STATION = "East Falls"
DEFAULT_TRANSFER_STATION = "Jefferson Station"

# --- Walk times (minutes), keyed by origin station ---
WALK_TIMES = {
    "Media": 5,
    "East Falls": 15,
    "Jefferson Station": 3,
    "Suburban Station": 4,
    "30th Street Station": 5,
}
DEFAULT_WALK_TIME_MINUTES = 5

# --- Timing & Thresholds ---
RISK_BUFFER_MINUTES = 3           # Transfers with < 3 min buffer are "at_risk"
SIGNIFICANT_DELAY_MINUTES = 10    # Trips with >= 10 min total delay are "delayed"
LEAVE_NOW_THRESHOLD_MINUTES = 3   # <= 3 min to leave = urgent "LEAVE NOW"
AFTERNOON_TARGET_DEPARTURE_TIME = "4:50PM"
MISSED_CONNECTION_BUFFER_MINUTES = 2 # Minimum 2 min needed to physically switch platforms

# --- Active Commute Windows (24hr clock) ---
MORNING_START, MORNING_END = 5, 9
AFTERNOON_START, AFTERNOON_END = 14, 19

# --- Polling Cadence ---
ACTIVE_POLL_INTERVAL_MS = 60_000      # 1 min during commute
IDLE_POLL_INTERVAL_MS = 1_800_000     # 30 min otherwise

# --- Alerts & Lines ---
RELEVANT_LINES = {"Media/Wawa", "Manayunk/Norristown", "Media"}
ADVISORY_RECENCY_DAYS = 3

# --- Notifications ---
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
NTFY_BASE_URL = "https://ntfy.sh"
NOTIFY_HOUR = 16
NOTIFY_MINUTE = 30

# --- Weather ---
WEATHER_LAT = 39.9168
WEATHER_LON = -75.3880  # Media, PA
WEATHER_WINDOW_START, WEATHER_WINDOW_END = 5, 20

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
    55: "Dense drizzle", 61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 80: "Slight rain showers",
    81: "Moderate rain showers", 82: "Violent rain showers", 95: "Thunderstorm",
    96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}

SEVERE_CODES = {
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
}