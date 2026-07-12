import re
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI
from datetime import datetime as dt, date
from collections import Counter



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
    weather = get_weather()

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
        "weather": weather,
    }

WEATHER_LAT = 39.9168
WEATHER_LON = -75.3880  # Media, PA

# Codes we consider "severe enough to flag regardless of frequency"
SEVERE_CODES = {
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
}

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

@app.get("/api/weather")
def get_weather():
    params = {
        "latitude": WEATHER_LAT,
        "longitude": WEATHER_LON,
        "hourly": "temperature_2m,precipitation,weather_code",
        "temperature_unit": "fahrenheit",
        "forecast_days": 1,
        "timezone": "America/New_York",
    }
    response = requests.get("https://api.open-meteo.com/v1/forecast", params=params).json()
    hourly = response.get("hourly", {})

    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    precip = hourly.get("precipitation", [])
    codes = hourly.get("weather_code", [])

    day_window = []
    for i, t in enumerate(times):
        hour = int(t.split("T")[1].split(":")[0])
        if 5 <= hour <= 20:
            day_window.append({
                "time": t,
                "hour": hour,
                "temp": temps[i],
                "precip": precip[i],
                "code": codes[i],
            })

    if not day_window:
        return {"error": "No forecast data for window"}

    max_temp = max(h["temp"] for h in day_window)
    min_temp = min(h["temp"] for h in day_window)
    total_precip = sum(h["precip"] for h in day_window)

    # Overall vibe: most frequent condition across the day
    code_counts = Counter(h["code"] for h in day_window)
    most_common_code = code_counts.most_common(1)[0][0]
    condition = WMO_CODES.get(most_common_code, "Unknown")

    # Severe weather check, scoped to her actual commute hours
    morning_window = [h for h in day_window if 5 <= h["hour"] <= 9]
    evening_window = [h for h in day_window if 15 <= h["hour"] <= 19]

    def find_severe(hours):
        hits = [h for h in hours if h["code"] in SEVERE_CODES]
        return hits[0] if hits else None

    morning_severe = find_severe(morning_window)
    evening_severe = find_severe(evening_window)

    warnings = []
    if morning_severe:
        warnings.append(f"{SEVERE_CODES[morning_severe['code']]} expected around {morning_severe['time'].split('T')[1]} during your morning commute")
    if evening_severe:
        warnings.append(f"{SEVERE_CODES[evening_severe['code']]} expected around {evening_severe['time'].split('T')[1]} during your evening commute")

    will_rain = total_precip > 0
    needs_umbrella = bool(morning_severe or evening_severe) or will_rain

    # Attire suggestion based on overall day, but escalate if severe weather hits commute windows
    if needs_umbrella:
        attire = "Raincoat + umbrella weather ☔"
    elif max_temp >= 85:
        attire = "Summer dress weather ☀️"
    elif max_temp >= 70:
        attire = "T-shirt weather 👕"
    elif max_temp >= 55:
        attire = "Light jacket weather 🧥"
    elif max_temp >= 40:
        attire = "Sweater weather 🧶"
    else:
        attire = "Bundle up, it's cold 🥶"

    return {
        "high_f": max_temp,
        "low_f": min_temp,
        "condition": condition,
        "needs_umbrella": needs_umbrella,
        "attire_suggestion": attire,
        "commute_warnings": warnings,
        "hourly_detail": day_window,
    }