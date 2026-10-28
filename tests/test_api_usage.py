"""Keep SEPTA calls down: a recent answer is reused, and nothing is fetched
from SEPTA outside commute hours."""
from datetime import datetime
from unittest.mock import MagicMock, patch

import app.commute
import app.main
from app.commute import get_commute


def leg(train, dep, arr):
    return {"orig_train": train, "orig_line": "Lansdale/Doylestown", "orig_departure_time": dep,
            "orig_arrival_time": arr, "orig_delay": "On time", "isdirect": "false"}


RESPONSES = {
    ("Ambler", "Jefferson Station"): [leg("9254", "5:15PM", "5:35PM")],
    ("Jefferson Station", "Glenside"): [leg("3560", "5:45PM", "6:20PM")],
}


def counting():
    calls = []

    def side_effect(url, params=None, **kwargs):
        calls.append(url)
        resp = MagicMock()
        resp.json.return_value = [] if "TrainView" in url else RESPONSES.get((params["req1"], params["req2"]), [])
        return resp
    return calls, side_effect


def test_recent_answers_are_reused(monkeypatch):
    monkeypatch.setattr(app.commute, "TRIP_FRESH_SECONDS", 60)
    clock = {"t": 1000.0}
    monkeypatch.setattr(app.commute.time, "time", lambda: clock["t"])
    calls, side_effect = counting()
    with patch("app.commute.requests.get", side_effect=side_effect):
        get_commute("Ambler", "Glenside", target_time="5:15PM", preferred_transfer="Jefferson Station")
        first = len(calls)
        clock["t"] += 30  # Home Assistant's next poll
        get_commute("Ambler", "Glenside", target_time="5:15PM", preferred_transfer="Jefferson Station")
        assert len(calls) == first
        clock["t"] += 61
        get_commute("Ambler", "Glenside", target_time="5:15PM", preferred_transfer="Jefferson Station")
        assert len(calls) > first


def test_no_septa_calls_outside_commute_hours(monkeypatch):
    monkeypatch.setattr(app.main, "get_active_window", lambda: None)
    monkeypatch.setattr(app.main, "get_weather", lambda: {})
    with patch("app.commute.requests.get") as trips, patch("app.alerts.requests.get") as alerts:
        result = app.main.get_dashboard()
    assert trips.call_count == 0 and alerts.call_count == 0
    assert result["overall_status"] == "idle"
    assert result["commute"]["service_alerts"] == []


def window_at(monkeypatch, weekday_hour):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 5, *weekday_hour)  # a Monday
    monkeypatch.setattr(app.main, "datetime", Fixed)
    return app.main.get_active_window()


def test_dashboard_looks_up_trains_only_around_the_commute(monkeypatch):
    assert window_at(monkeypatch, (5, 59)) is None
    assert window_at(monkeypatch, (6, 0)) == "morning"
    assert window_at(monkeypatch, (7, 59)) == "morning"
    assert window_at(monkeypatch, (8, 0)) is None
    assert window_at(monkeypatch, (13, 59)) is None
    assert window_at(monkeypatch, (14, 0)) == "afternoon"
    assert window_at(monkeypatch, (17, 59)) == "afternoon"
    assert window_at(monkeypatch, (18, 0)) is None
