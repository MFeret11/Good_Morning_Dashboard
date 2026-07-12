"""Commute-window-aware weather: high/low, dominant condition, severe
weather flags scoped to actual commute hours, and an attire suggestion."""
from collections import Counter

import requests

from app.config import (
    WEATHER_LAT, WEATHER_LON, WEATHER_WINDOW_START, WEATHER_WINDOW_END,
    WMO_CODES, SEVERE_CODES, MORNING_START, MORNING_END, AFTERNOON_START, AFTERNOON_END,
)


def _find_severe(hours):
    hits = [h for h in hours if h["code"] in SEVERE_CODES]
    return hits[0] if hits else None


def get_weather() -> dict:
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
        if WEATHER_WINDOW_START <= hour <= WEATHER_WINDOW_END:
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

    # Overall vibe: most frequent condition across the day (not numeric max)
    code_counts = Counter(h["code"] for h in day_window)
    most_common_code = code_counts.most_common(1)[0][0]
    condition = WMO_CODES.get(most_common_code, "Unknown")

    # Severe weather check, scoped to her actual commute hours
    morning_window = [h for h in day_window if MORNING_START <= h["hour"] <= MORNING_END]
    evening_window = [h for h in day_window if AFTERNOON_START <= h["hour"] <= AFTERNOON_END]

    morning_severe = _find_severe(morning_window)
    evening_severe = _find_severe(evening_window)

    warnings = []
    if morning_severe:
        warnings.append(
            f"{SEVERE_CODES[morning_severe['code']]} expected around "
            f"{morning_severe['time'].split('T')[1]} during your morning commute"
        )
    if evening_severe:
        warnings.append(
            f"{SEVERE_CODES[evening_severe['code']]} expected around "
            f"{evening_severe['time'].split('T')[1]} during your evening commute"
        )

    will_rain = total_precip > 0
    needs_umbrella = bool(morning_severe or evening_severe) or will_rain

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
