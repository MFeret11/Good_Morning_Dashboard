"""Commute-window-aware weather: high/low, dominant condition, severe
weather flags scoped to actual commute hours, and dual-window attire suggestion."""
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
        "hourly": "temperature_2m,precipitation,precipitation_probability,weather_code",
        "temperature_unit": "fahrenheit",
        "forecast_days": 1,
        "timezone": "America/New_York",
        "current_weather": "true",
    }
    try:
        response = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=10)
        data = response.json()
    except (requests.RequestException, ValueError):
        return {"error": "Weather service unavailable"}

    hourly = data.get("hourly", {})
    current = data.get("current_weather", {})
    current_temp = current.get("temperature")

    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    precip = hourly.get("precipitation", [])
    precip_probs = hourly.get("precipitation_probability", [])
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
                "precip_chance": precip_probs[i] if i < len(precip_probs) else 0,
            })

    if not day_window:
        return {"error": "No forecast data for window"}

    max_temp = max(h["temp"] for h in day_window)
    min_temp = min(h["temp"] for h in day_window)
    max_precip_chance = max((h["precip_chance"] for h in day_window), default=0)
    total_precip = sum(h["precip"] for h in day_window)

    code_counts = Counter(h["code"] for h in day_window)
    most_common_code = code_counts.most_common(1)[0][0]
    condition = WMO_CODES.get(most_common_code, "Unknown")

    # Scoped Commute Windows
    morning_window = [h for h in day_window if MORNING_START <= h["hour"] <= MORNING_END]
    evening_window = [h for h in day_window if AFTERNOON_START <= h["hour"] <= AFTERNOON_END]

    morning_temp = round(sum(h["temp"] for h in morning_window) / len(morning_window)) if morning_window else min_temp
    evening_temp = round(sum(h["temp"] for h in evening_window) / len(evening_window)) if evening_window else max_temp

    morning_severe = _find_severe(morning_window)
    evening_severe = _find_severe(evening_window)

    warnings = []
    if morning_severe:
        warnings.append(
            f"{SEVERE_CODES[morning_severe['code']]} around "
            f"{morning_severe['time'].split('T')[1]} during morning commute"
        )
    if evening_severe:
        warnings.append(
            f"{SEVERE_CODES[evening_severe['code']]} around "
            f"{evening_severe['time'].split('T')[1]} during evening commute"
        )

    commute_rain = any(h["precip"] > 0 or h["precip_chance"] >= 40 for h in morning_window + evening_window)
    needs_umbrella = bool(morning_severe or evening_severe) or commute_rain or (total_precip > 0.05)

    # DUAL-WINDOW COMMUTE WARDROBE LOGIC
    temp_spread = evening_temp - morning_temp

    if needs_umbrella:
        attire = "Raincoat + umbrella ☔"
    elif temp_spread >= 18 and morning_temp < 60:
        attire = f"Chilly AM ({morning_temp}°F) → Warm PM ({evening_temp}°F): Wear layers 🧥"
    elif morning_temp >= 75:
        attire = "Summer / light attire ☀️"
    elif morning_temp >= 65:
        attire = "T-shirt / light clothes 👕"
    elif morning_temp >= 50:
        attire = "Light jacket / sweater 🧥"
    elif morning_temp >= 38:
        attire = "Warm coat / sweater 🧶"
    else:
        attire = "Heavy winter coat 🥶"

    return {
        "high_f": round(max_temp),
        "low_f": round(min_temp),
        "morning_temp_f": morning_temp,
        "evening_temp_f": evening_temp,
        "condition": condition,
        "needs_umbrella": needs_umbrella,
        "attire_suggestion": attire,
        "commute_warnings": warnings,
        "hourly_detail": day_window,
        "current_temp_f": round(current_temp) if current_temp is not None else None,
        "precip_chance": max_precip_chance,
    }