"""Following the connection once the rider is on the first train: NextToArrive
no longer lists that train, so its delay comes from TrainView's `late` field."""
from unittest.mock import MagicMock, patch

from app.afternoon_check import build_afternoon_message
from app.commute import get_commute, get_connection_update

NO_ALERTS = {"has_critical_alerts": False, "critical_alerts": []}


def leg(train, dep, arr, delay="On time", line="Lansdale/Doylestown"):
    return {"orig_train": train, "orig_line": line, "orig_departure_time": dep,
            "orig_arrival_time": arr, "orig_delay": delay, "isdirect": "false"}


def septa(responses, trainview=()):
    """requests.get side effect keyed by (req1, req2), plus a TrainView feed."""
    def side_effect(url, params=None, **kwargs):
        resp = MagicMock()
        if "TrainView" in url:
            resp.json.return_value = list(trainview)
        else:
            resp.json.return_value = responses.get((params["req1"], params["req2"]), [])
        return resp
    return side_effect


PLAN = {
    ("Ambler", "Glenside"): [],
    ("Ambler", "Jefferson Station"): [leg("9254", "5:15PM", "5:35PM", line="Paoli/Thorndale")],
    ("Jefferson Station", "Glenside"): [leg("3560", "5:45PM", "6:20PM"), leg("3564", "6:15PM", "6:50PM")],
}


def planned_trip():
    with patch("app.commute.requests.get", side_effect=septa(PLAN)):
        trip = get_commute("Ambler", "Glenside", target_time="5:15PM",
                           preferred_transfer="Jefferson Station")
    assert trip["connection_train"] == "3560"
    assert trip["origin_arrival_time"] == "5:35PM"
    return trip


# Once the first train has left Ambler, only the connecting trains are listed.
ON_BOARD = {("Jefferson Station", "Glenside"): PLAN[("Jefferson Station", "Glenside")]}


def train(late):
    return {"trainno": "9254", "currentstop": "Wayne Junction", "late": late, "TRACK": "2"}


def test_live_delay_that_still_connects():
    trip = planned_trip()
    with patch("app.commute.requests.get", side_effect=septa(ON_BOARD, [train(6)])):
        update = get_connection_update(trip, "Glenside")
    assert update["connection_train"] == "3560"
    assert update["missed_connection"] is False
    assert update["origin_delay_minutes"] == 6
    title, message, _, _ = build_afternoon_message(update, NO_ALERTS)
    assert title == "Late but still connecting"
    assert "6 min late" in message
    assert "on time" not in message.lower()


def test_live_delay_that_misses_the_connection():
    trip = planned_trip()
    with patch("app.commute.requests.get", side_effect=septa(ON_BOARD, [train(16)])):
        update = get_connection_update(trip, "Glenside")
    assert update["missed_connection"] is True
    assert update["connection_train"] == "3564"
    assert update["total_delay_minutes"] == 30
    title, _, _, _ = build_afternoon_message(update, NO_ALERTS)
    assert title == "Missed Connection Alert"


def test_cancelled_connection_while_on_board():
    trip = planned_trip()
    cancelled = {("Jefferson Station", "Glenside"): [leg("3560", "5:45PM", "6:20PM", "Cancelled"),
                                                     leg("3564", "6:15PM", "6:50PM")]}
    with patch("app.commute.requests.get", side_effect=septa(cancelled, [train(0)])):
        update = get_connection_update(trip, "Glenside")
    assert update["planned_connection_cancelled"] is True
    assert update["connection_train"] == "3564"


def test_unknown_live_delay_is_an_error_not_on_time():
    trip = planned_trip()
    with patch("app.commute.requests.get", side_effect=septa(ON_BOARD, [])):
        update = get_connection_update(trip, "Glenside")
    assert "error" in update


def test_unparseable_late_field_is_an_error():
    trip = planned_trip()
    with patch("app.commute.requests.get", side_effect=septa(ON_BOARD, [train("n/a")])):
        update = get_connection_update(trip, "Glenside")
    assert "error" in update


def test_fallback_delay_is_used_when_trainview_drops_the_train():
    trip = planned_trip()
    with patch("app.commute.requests.get", side_effect=septa(ON_BOARD, [])):
        update = get_connection_update(trip, "Glenside", fallback_late=0)
    assert update["connection_train"] == "3560"
    assert update["missed_connection"] is False


def test_no_connecting_trains_listed_is_an_error():
    trip = planned_trip()
    with patch("app.commute.requests.get", side_effect=septa({}, [train(0)])):
        update = get_connection_update(trip, "Glenside")
    assert "error" in update
