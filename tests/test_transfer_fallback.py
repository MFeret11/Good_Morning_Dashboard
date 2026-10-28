"""When no connection works at the preferred transfer station, the plan tries
the other Center City stations before falling back to SEPTA's own pick, and
says where to change trains."""
from datetime import datetime
from unittest.mock import MagicMock, patch

import app.afternoon_check as ac
from app.afternoon_check import build_afternoon_message
from app.commute import get_commute

NO_ALERTS = {"has_critical_alerts": False, "critical_alerts": []}


def leg(train, dep, arr, delay="On time", line="Lansdale/Doylestown"):
    return {"orig_train": train, "orig_line": line, "orig_departure_time": dep,
            "orig_arrival_time": arr, "orig_delay": delay, "isdirect": "false"}


def septa(responses, calls=None):
    def side_effect(url, params=None, **kwargs):
        resp = MagicMock()
        if "TrainView" in url:
            resp.json.return_value = []
        else:
            if calls is not None:
                calls.append((params["req1"], params["req2"]))
            resp.json.return_value = responses.get((params["req1"], params["req2"]), [])
        return resp
    return side_effect


def plan(overrides=None):
    responses = {
        ("Ambler", "Jefferson Station"): [leg("9254", "5:15PM", "5:35PM", line="Paoli/Thorndale")],
        ("Jefferson Station", "Glenside"): [leg("3560", "5:45PM", "6:20PM")],
        ("Ambler", "Suburban Station"): [leg("9254", "5:15PM", "5:31PM", line="Paoli/Thorndale")],
        ("Suburban Station", "Glenside"): [leg("3560", "5:41PM", "6:20PM")],
        ("Ambler", "30th Street Station"): [leg("9254", "5:15PM", "5:26PM", line="Paoli/Thorndale")],
        ("30th Street Station", "Glenside"): [leg("3560", "5:36PM", "6:20PM")],
    }
    responses.update(overrides or {})
    return responses


def afternoon(responses, calls=None, destination="Glenside"):
    with patch("app.commute.requests.get", side_effect=septa(responses, calls)):
        return get_commute("Ambler", destination, target_time="5:15PM",
                           preferred_transfer="Jefferson Station")


def test_preferred_station_is_used_when_it_works():
    calls = []
    trip = afternoon(plan(), calls)
    assert trip["connection_station"] == "Jefferson Station"
    assert trip["transfer_fallback"] is False
    assert not any("Suburban Station" in pair for pair in calls)  # no extra SEPTA calls
    _, message, _, _ = build_afternoon_message(trip, NO_ALERTS)
    assert "instead" not in message


def test_falls_back_when_no_connecting_trains_at_the_preferred_station():
    trip = afternoon(plan({("Jefferson Station", "Glenside"): []}))
    assert trip["connection_station"] == "Suburban Station"
    assert trip["transfer_fallback"] is True
    _, message, _, _ = build_afternoon_message(trip, NO_ALERTS)
    assert "No connection works at Jefferson Station right now, so change trains at Suburban Station instead." in message


def test_falls_back_when_every_preferred_connection_is_cancelled():
    trip = afternoon(plan({("Jefferson Station", "Glenside"): [leg("3560", "5:45PM", "6:20PM", "Cancelled")]}))
    assert trip["connection_station"] == "Suburban Station"


def test_tries_the_next_station_in_order():
    trip = afternoon(plan({("Jefferson Station", "Glenside"): [],
                             ("Suburban Station", "Glenside"): []}))
    assert trip["connection_station"] == "30th Street Station"


def test_skips_a_fallback_that_is_the_destination():
    calls = []
    responses = {("Ambler", "Jefferson Station"): [leg("9254", "5:15PM", "5:35PM")]}
    trip = afternoon(responses, calls, destination="Suburban Station")
    assert ("Ambler", "Suburban Station") not in calls[1:]  # only the first, direct lookup
    assert "error" in trip


def test_septa_pick_is_the_last_resort():
    septa_pick = [{
        "orig_train": "9254", "orig_line": "Paoli/Thorndale",
        "orig_departure_time": "5:15PM", "orig_arrival_time": "5:35PM", "orig_delay": "On time",
        "term_train": "3560", "term_line": "Lansdale/Doylestown", "term_depart_time": "5:45PM",
        "term_arrival_time": "6:20PM", "term_delay": "On time",
        "Connection": "Temple University", "isdirect": "false",
    }]
    trip = afternoon({("Ambler", "Glenside"): septa_pick})
    assert trip["connection_station"] == "Temple University"
    assert trip["transfer_fallback"] is True


# ---- Follow-ups when the transfer station changes ----

def follow(monkeypatch, results):
    sent = []
    clock = {"now": datetime(2026, 1, 5, 16, 30)}
    queue = list(results)
    monkeypatch.setattr(ac, "_now", lambda: clock["now"])
    monkeypatch.setattr(ac, "_check_return_commute", lambda: (queue.pop(0), NO_ALERTS))
    monkeypatch.setattr(ac, "get_weather", lambda: {})
    monkeypatch.setattr(ac, "send_notification", lambda t, m, **kw: sent.append((t, m)) or True)
    ac.run_afternoon_check()
    for minute in (35, 40):
        clock["now"] = clock["now"].replace(minute=minute)
        if queue:
            ac.run_follow_up_check()
    return sent


def trip_via(station, fallback):
    return {"trip_type": "transfer", "origin_train": "1001", "origin_actual_departure_time": "5:15PM",
            "origin_departure_time": "5:15PM", "origin_arrival_time": "5:35PM",
            "origin_delay_minutes": 0, "total_delay_minutes": 0, "actual_arrival_time": "6:20PM",
            "connection_station": station, "connection_train": "2002",
            "connection_actual_departure_time": "5:45PM",
            "preferred_transfer_station": "Jefferson Station", "transfer_fallback": fallback}


def test_station_change_after_the_push_is_sent(monkeypatch):
    sent = follow(monkeypatch, [trip_via("Jefferson Station", False), trip_via("Suburban Station", True),
                                trip_via("Suburban Station", True)])
    assert [t for t, _ in sent] == ["Commute looks good", "Update: Commute looks good"]
    assert "change trains at Suburban Station instead" in sent[1][1]


def test_moving_back_to_the_usual_station_says_so(monkeypatch):
    sent = follow(monkeypatch, [trip_via("Suburban Station", True), trip_via("Jefferson Station", False)])
    assert len(sent) == 2
    assert "Change trains at Jefferson Station." in sent[1][1]
