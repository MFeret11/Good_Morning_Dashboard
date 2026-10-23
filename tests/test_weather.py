from unittest.mock import MagicMock, patch

from app.weather import get_weather


def forecast(code_by_hour, temp=30):
    hours = range(0, 24)
    return {
        "current_weather": {"temperature": temp},
        "hourly": {
            "time": [f"2026-01-05T{h:02d}:00" for h in hours],
            "temperature_2m": [temp for _ in hours],
            "precipitation": [0.1 if h in code_by_hour else 0 for h in hours],
            "precipitation_probability": [80 if h in code_by_hour else 0 for h in hours],
            "weather_code": [code_by_hour.get(h, 3) for h in hours],
        },
    }


def weather_for(code_by_hour, temp=30):
    resp = MagicMock()
    resp.json.return_value = forecast(code_by_hour, temp)
    with patch("app.weather.requests.get", return_value=resp):
        return get_weather()


def test_freezing_rain_is_named_and_warned_about():
    w = weather_for({7: 66})
    assert "freezing" in w["attire_suggestion"].lower()
    assert any("freezing rain" in x.lower() for x in w["commute_warnings"])


def test_snow_shower_is_not_reported_as_rain_gear():
    w = weather_for({17: 85})
    assert "snow" in w["attire_suggestion"].lower()
    assert "umbrella" not in w["attire_suggestion"].lower()
    assert any("snow showers" in x.lower() for x in w["commute_warnings"])


def test_rain_still_suggests_umbrella():
    w = weather_for({7: 63}, temp=55)
    assert "umbrella" in w["attire_suggestion"].lower()
    assert w["needs_umbrella"] is True


def test_freezing_drizzle_has_a_condition_name():
    w = weather_for({h: 56 for h in range(5, 21)})
    assert w["condition"] == "Light freezing drizzle"
