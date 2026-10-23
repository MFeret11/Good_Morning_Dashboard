"""Regression tests for cases where the service gave confidently wrong
answers: a late first leg reported as on time, a false 'missed connection',
and upstream failures that looked like clean data."""
from unittest.mock import MagicMock, patch

import requests

from app.afternoon_check import build_afternoon_message, run_afternoon_check
from app.commute import get_commute
from app.time_utils import is_delay_unknown

NO_ALERTS = {"has_critical_alerts": False, "critical_alerts": []}


def leg(train, dep, arr, delay="On time", line="Lansdale/Doylestown"):
    return {"orig_train": train, "orig_line": line, "orig_departure_time": dep,
            "orig_arrival_time": arr, "orig_delay": delay, "isdirect": "false"}


# SEPTA's own single-call transfer suggestion for Ambler -> Glenside.
SEPTA_PICK = [{
    "orig_train": "9254", "orig_line": "Paoli/Thorndale",
    "orig_departure_time": "5:15PM", "orig_arrival_time": "5:35PM", "orig_delay": "On time",
    "term_train": "3560", "term_line": "Lansdale/Doylestown", "term_depart_time": "5:45PM",
    "term_arrival_time": "6:20PM", "term_delay": "On time",
    "Connection": "Jefferson Station", "isdirect": "false",
}]


def septa(responses, fail=False):
    """requests.get side effect keyed by (req1, req2). TrainView returns []."""
    def side_effect(url, params=None, **kwargs):
        if fail:
            raise requests.ConnectionError("SEPTA down")
        resp = MagicMock()
        if "TrainView" in url:
            resp.json.return_value = []
        else:
            resp.json.return_value = responses.get((params["req1"], params["req2"]), [])
        return resp
    return side_effect


def afternoon(_unused=None):
    return get_commute("Ambler", "Glenside", target_time="5:15PM",
                       preferred_transfer="Jefferson Station")


@patch("app.commute.requests.get")
def test_requests_enough_trains_from_septa(mock_get):
    # SEPTA ignores "top" and returns only 4 trains unless req3 is set.
    mock_get.side_effect = septa({})
    get_commute("Glenside", "Ambler")
    params = mock_get.call_args_list[0].kwargs["params"]
    assert params["req3"] >= 10


@patch("app.commute.requests.get")
def test_late_first_leg_that_still_connects_is_not_reported_on_time(mock_get):
    mock_get.side_effect = septa({
        ("Ambler", "Glenside"): SEPTA_PICK,
        ("Ambler", "Jefferson Station"): [leg("9254", "5:15PM", "5:35PM", "8 mins")],
        ("Jefferson Station", "Glenside"): [leg("3560", "5:45PM", "6:20PM")],
    })
    commute = afternoon(None)
    assert commute["missed_connection"] is False
    title, message, _, _ = build_afternoon_message(commute, NO_ALERTS)
    assert "on time" not in message.lower()
    assert "8 min late" in message


@patch("app.commute.requests.get")
def test_delay_onto_a_later_connection_counts_as_missed_and_delayed(mock_get):
    # Planned: arrive 5:35, catch the 5:45. A 16 min delay lands at 5:51,
    # past the -5 min catchable floor, so the rider takes the 6:15 instead and gets home 30 min late, even
    # though that later train is itself on time.
    mock_get.side_effect = septa({
        ("Ambler", "Glenside"): SEPTA_PICK,
        ("Ambler", "Jefferson Station"): [leg("9254", "5:15PM", "5:35PM", "16 mins")],
        ("Jefferson Station", "Glenside"): [leg("3560", "5:45PM", "6:20PM"),
                                         leg("3564", "6:15PM", "6:50PM")],
    })
    commute = afternoon(None)
    assert commute["connection_train"] == "3564"
    assert commute["missed_connection"] is True
    assert commute["total_delay_minutes"] == 30
    assert commute["delayed"] is True
    assert commute["planned_arrival_time"] == "6:20PM"


@patch("app.commute.requests.get")
def test_connections_not_yet_listed_are_not_a_missed_connection(mock_get):
    # Polled at 2PM: SEPTA's connecting-train list ends before the 5:15
    # train reaches Jefferson. That's missing data, not a missed connection.
    mock_get.side_effect = septa({
        ("Ambler", "Glenside"): SEPTA_PICK,
        ("Ambler", "Jefferson Station"): [leg("9250", "2:45PM", "3:05PM"),
                                              leg("9254", "5:15PM", "5:35PM")],
        ("Jefferson Station", "Glenside"): [leg("3500", "2:35PM", "3:10PM"),
                                         leg("3504", "3:35PM", "4:10PM")],
    })
    commute = afternoon(None)
    assert commute["missed_connection"] is False
    assert commute["connection_train"] == "3560"  # SEPTA's own pick
    assert commute["actual_arrival_time"] == "6:20PM"


@patch("app.commute.requests.get")
def test_septa_outage_without_cache_is_an_error_not_empty(mock_get):
    mock_get.side_effect = septa({}, fail=True)
    assert get_commute("Glenside", "Ambler") == {"error": "SEPTA trip data unavailable"}


@patch("app.commute.requests.get")
def test_septa_outage_serves_cache_marked_stale(mock_get):
    data = {
        ("Ambler", "Glenside"): SEPTA_PICK,
        ("Ambler", "Jefferson Station"): [leg("9254", "5:15PM", "5:35PM")],
        ("Jefferson Station", "Glenside"): [leg("3560", "5:45PM", "6:20PM")],
    }
    mock_get.side_effect = septa(data)
    assert afternoon(None)["data_stale"] is False

    mock_get.side_effect = septa(data, fail=True)
    commute = afternoon(None)
    assert commute["data_stale"] is True
    _, message, _, _ = build_afternoon_message(commute, NO_ALERTS)
    assert "few minutes ago" in message


@patch("app.commute.requests.get")
def test_septa_error_payload_is_treated_as_failure(mock_get):
    resp = MagicMock()
    resp.json.return_value = {"error": "something broke"}
    mock_get.return_value = resp
    assert "error" in get_commute("Glenside", "Ambler")


def test_unreadable_delay_text_is_flagged():
    assert is_delay_unknown("Delayed") is True
    assert is_delay_unknown("On time") is False
    assert is_delay_unknown("5 mins") is False
    assert is_delay_unknown("Cancelled") is False
    assert is_delay_unknown("Suspended") is False


@patch("app.commute.requests.get")
def test_unreadable_delay_produces_warning_not_on_time(mock_get):
    mock_get.side_effect = septa({
        ("Ambler", "Glenside"): SEPTA_PICK,
        ("Ambler", "Jefferson Station"): [leg("9254", "5:15PM", "5:35PM", "Delayed")],
        ("Jefferson Station", "Glenside"): [leg("3560", "5:45PM", "6:20PM")],
    })
    commute = afternoon(None)
    assert commute["origin_delay_unknown"] is True
    assert commute["delayed"] is True
    title, message, priority, _ = build_afternoon_message(commute, NO_ALERTS)
    assert title == "Train status unclear"
    assert '"Delayed"' in message
    assert priority == "high"


def test_service_alert_push_still_names_the_train():
    commute = {"origin_train": "9254", "origin_actual_departure_time": "5:15PM",
               "actual_arrival_time": "6:20PM", "origin_delay_minutes": 0,
               "total_delay_minutes": 0}
    alerts = {"has_critical_alerts": True,
              "critical_alerts": [{"line": "Lansdale/Doylestown", "alert_text": "Signal problems"}]}
    _, message, _, _ = build_afternoon_message(commute, alerts)
    assert "Signal problems" in message
    assert "Train 9254 departs 5:15PM" in message


def test_alerts_unavailable_is_mentioned_in_push():
    commute = {"origin_train": "9254", "origin_actual_departure_time": "5:15PM",
               "actual_arrival_time": "6:20PM"}
    _, message, _, _ = build_afternoon_message(commute, {"alerts_unavailable": True})
    assert "alerts couldn't be checked" in message


@patch("app.afternoon_check.send_notification", return_value=True)
@patch("app.afternoon_check.get_commute", side_effect=KeyError("orig_arrival_time"))
def test_crash_still_sends_a_failure_push(mock_commute, mock_send):
    run_afternoon_check()
    title = mock_send.call_args.args[0]
    assert title == "Commute check failed"


MORNING_SEPTA_PICK = [{
    "orig_train": "3504", "orig_line": "Lansdale/Doylestown",
    "orig_departure_time": "7:00AM", "orig_arrival_time": "7:35AM", "orig_delay": "On time",
    "term_train": "9210", "term_line": "Paoli/Thorndale", "term_depart_time": "7:45AM",
    "term_arrival_time": "8:05AM", "term_delay": "On time",
    "Connection": "Jefferson Station", "isdirect": "false",
}]


def morning_with_clock(monkeypatch, hour, minute, leg1):
    import app.commute
    from datetime import datetime
    monkeypatch.setattr(app.commute, "_now", lambda: datetime(2026, 1, 5, hour, minute))
    with patch("app.commute.requests.get") as mock_get:
        mock_get.side_effect = septa({
            ("Glenside", "Ambler"): MORNING_SEPTA_PICK,
            ("Glenside", "Jefferson Station"): leg1,
            ("Jefferson Station", "Ambler"): [leg("9210", "7:45AM", "8:05AM"),
                                                  leg("9214", "8:15AM", "8:35AM")],
        })
        return get_commute("Glenside", "Ambler")


MORNING_LEG1 = [leg("3500", "6:30AM", "7:05AM"), leg("3504", "7:00AM", "7:35AM")]


def test_morning_skips_a_train_the_rider_can_no_longer_reach(monkeypatch):
    # 6:30 train, 5 min walk: leave-by 6:25. At 6:28 it's 3 min past the
    # 2 min grace, so show the 7:00 instead of LEAVE NOW for a lost cause.
    commute = morning_with_clock(monkeypatch, 6, 28, MORNING_LEG1)
    assert commute["origin_train"] == "3504"
    assert commute["leave_now"] is False


def test_morning_keeps_a_train_within_the_grace_window(monkeypatch):
    commute = morning_with_clock(monkeypatch, 6, 26, MORNING_LEG1)
    assert commute["origin_train"] == "3500"
    assert commute["leave_now"] is True


def test_morning_counts_delay_when_deciding_reachability(monkeypatch):
    # The 6:30 is 10 min late (leaves 6:40, leave-by 6:35), so at 6:28 it's
    # still the right train.
    leg1 = [leg("3500", "6:30AM", "7:05AM", "10 mins"), leg("3504", "7:00AM", "7:35AM")]
    commute = morning_with_clock(monkeypatch, 6, 28, leg1)
    assert commute["origin_train"] == "3500"


@patch("app.commute.requests.get")
def test_itineraries_start_at_the_target_train_and_match_the_main_pick(mock_get):
    # Polled at 2PM for the 5:15PM: options should be the 5:15 and the two
    # after it, not the next three departures from now.
    mock_get.side_effect = septa({
        ("Ambler", "Glenside"): SEPTA_PICK,
        ("Ambler", "Jefferson Station"): [leg("9246", "2:15PM", "2:35PM"),
                                          leg("9254", "5:15PM", "5:35PM", "16 mins"),
                                          leg("9258", "5:45PM", "6:05PM"),
                                          leg("9262", "6:15PM", "6:35PM")],
        ("Jefferson Station", "Glenside"): [leg("3540", "2:45PM", "3:20PM"),
                                            leg("3560", "5:45PM", "6:20PM"),
                                            leg("3564", "6:15PM", "6:50PM"),
                                            leg("3568", "6:45PM", "7:20PM")],
    })
    commute = afternoon()
    options = commute["itineraries"]
    assert [o["origin_train"] for o in options] == ["9254", "9258", "9262"]

    first = options[0]
    assert first["connection_train"] == commute["connection_train"] == "3564"
    assert first["actual_arrival_time"] == commute["actual_arrival_time"]
    assert first["total_delay_minutes"] == commute["total_delay_minutes"] == 30
    assert first["missed_connection"] is commute["missed_connection"] is True
    assert first["status"] == "delayed"
    assert options[1]["status"] == "ok"


@patch("app.commute.requests.get")
def test_direct_itineraries_start_at_the_chosen_train(mock_get):
    direct = [dict(leg(n, dep, arr), isdirect="true") for n, dep, arr in [
        ("1001", "2:00PM", "2:40PM"), ("1005", "5:15PM", "5:55PM"),
        ("1009", "5:45PM", "6:25PM"), ("1013", "6:15PM", "6:55PM")]]
    mock_get.side_effect = septa({("Ambler", "Glenside"): direct})
    commute = afternoon()
    assert commute["origin_train"] == "1005"
    assert [o["origin_train"] for o in commute["itineraries"]] == ["1005", "1009", "1013"]
