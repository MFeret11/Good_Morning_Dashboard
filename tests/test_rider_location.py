"""GPS fixes from Home Assistant: working out which train the rider is on, and
planning the afternoon trip around that train. Coordinates are made up."""
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import app.commute
import app.main
from app import rider
from app.commute import get_commute, get_trip_for_boarded_train


def leg(train, dep, arr, delay="On time", line="Lansdale/Doylestown"):
    return {"orig_train": train, "orig_line": line, "orig_departure_time": dep,
            "orig_arrival_time": arr, "orig_delay": delay, "isdirect": "false"}


def tv(train, lat, lon, late=0, heading=""):
    return {"trainno": train, "lat": str(lat), "lon": str(lon), "late": late,
            "currentstop": "Somewhere", "TRACK": "1", "heading": str(heading)}


def untracked_tv(train, late=0, stop="First Stop"):
    """How TrainView lists a train it has no GPS position for."""
    return {"trainno": train, "lat": "39.9538889", "lon": "-75.1677778", "late": late,
            "currentstop": stop, "nextstop": stop, "heading": "", "consist": "", "TRACK": "1"}


def septa(responses, trainview=()):
    def side_effect(url, params=None, **kwargs):
        resp = MagicMock()
        if "TrainView" in url:
            resp.json.return_value = list(trainview)
        else:
            resp.json.return_value = responses.get((params["req1"], params["req2"]), [])
        return resp
    return side_effect


def feed(*trains):
    """Puts trains straight into the TrainView cache, as if just fetched."""
    app.commute._TRAINVIEW_CACHE = (list(trains), time.time())


def fix(lat, lon, minutes_ago=0, zone="not_home"):
    rider.update_fix(lat, lon, accuracy=10, seen_at=time.time() - minutes_ago * 60, zone=zone)


# --- which train -------------------------------------------------------------

def test_no_fix_means_nothing_known():
    assert rider.current() is None


def test_one_fix_next_to_a_train_is_not_enough():
    feed(tv("9254", 40.100, -75.200))
    fix(40.101, -75.200)
    assert rider.current()["on_train"] is None


def test_two_moving_fixes_beside_the_same_train_confirm_it():
    feed(tv("9254", 40.100, -75.200))
    fix(40.101, -75.200, minutes_ago=2)
    feed(tv("9254", 40.120, -75.200))
    fix(40.119, -75.200)
    assert rider.current()["on_train"] == "9254"


def test_standing_still_beside_a_train_is_not_riding_it():
    feed(tv("9254", 40.100, -75.200))
    fix(40.1010, -75.200, minutes_ago=2)
    fix(40.1011, -75.200)
    assert rider.current()["on_train"] is None


def test_nearest_train_wins_and_far_trains_are_ignored():
    feed(tv("1111", 40.100, -75.200), tv("2222", 40.300, -75.200))
    fix(40.101, -75.200, minutes_ago=2)
    feed(tv("1111", 40.120, -75.200), tv("2222", 40.300, -75.200))
    fix(40.121, -75.200)
    assert rider.current()["on_train"] == "1111"


def test_one_missed_fix_is_forgiven_two_are_not():
    feed(tv("9254", 40.100, -75.200))
    fix(40.101, -75.200, minutes_ago=4)
    feed(tv("9254", 40.120, -75.200))
    fix(40.119, -75.200, minutes_ago=3)
    feed()                                   # train missing from the feed
    fix(40.140, -75.200, minutes_ago=2)
    assert rider.current()["on_train"] == "9254"
    fix(40.160, -75.200, minutes_ago=1)
    assert rider.current()["on_train"] is None


def test_arriving_home_clears_the_train():
    feed(tv("9254", 40.100, -75.200))
    fix(40.101, -75.200, minutes_ago=2)
    feed(tv("9254", 40.120, -75.200))
    fix(40.119, -75.200, minutes_ago=1)
    fix(40.119, -75.200, zone="home")
    now = rider.current()
    assert now["on_train"] is None and now["home"] is True


def test_an_old_fix_says_nothing_about_now():
    feed(tv("9254", 40.100, -75.200))
    fix(40.101, -75.200, minutes_ago=32)
    feed(tv("9254", 40.120, -75.200))
    fix(40.119, -75.200, minutes_ago=30)
    now = rider.current()
    assert now["fresh"] is False and now["on_train"] is None and now["home"] is False


def test_a_train_passing_the_other_way_is_not_the_riders_train():
    # Rider heads north; the only train nearby is heading south past them.
    feed(tv("9113", 40.105, -75.200, heading=180))
    fix(40.100, -75.200, minutes_ago=2)
    feed(tv("9113", 40.112, -75.200, heading=180))
    fix(40.115, -75.200)
    assert rider.current()["on_train"] is None


def test_a_train_with_no_gps_position_is_never_matched_by_distance():
    # The rider is right where TrainView parks trains it can't see.
    feed(untracked_tv("9260"))
    fix(39.950, -75.1677778, minutes_ago=2)
    fix(39.9538889, -75.1677778)
    assert rider.current()["on_train"] is None


def test_moving_at_train_speed_with_no_train_alongside_is_riding_untracked():
    feed(untracked_tv("9260"))
    fix(40.100, -75.200, minutes_ago=3)
    fix(40.110, -75.200, minutes_ago=2)
    assert rider.current()["riding_untracked"] is False     # one fast fix isn't enough
    fix(40.113, -75.200, minutes_ago=1)                     # slowing into a station
    assert rider.current()["riding_untracked"] is True
    fix(40.113, -75.200, minutes_ago=0.5)                   # stopped at it
    assert rider.current()["riding_untracked"] is True


def test_walking_is_not_riding_untracked():
    feed()
    fix(40.1000, -75.200, minutes_ago=3)
    fix(40.1006, -75.200, minutes_ago=2)
    fix(40.1012, -75.200, minutes_ago=1)
    assert rider.current()["riding_untracked"] is False


# --- the trip for the train the rider is on -----------------------------------

PLAN = {
    ("Ambler", "Glenside"): [],
    ("Ambler", "Jefferson Station"): [
        leg("9250", "4:45PM", "5:05PM", line="Paoli/Thorndale"),
        leg("9254", "5:15PM", "5:35PM", line="Paoli/Thorndale"),
    ],
    ("Jefferson Station", "Glenside"): [
        leg("3556", "5:15PM", "5:50PM"), leg("3560", "5:45PM", "6:20PM"), leg("3564", "6:15PM", "6:50PM"),
    ],
}
# After the earlier train has left Ambler it is no longer listed there.
LATER = {**PLAN, ("Ambler", "Jefferson Station"): PLAN[("Ambler", "Jefferson Station")][1:]}


def plan_the_afternoon():
    with patch("app.commute.requests.get", side_effect=septa(PLAN)):
        trip = get_commute("Ambler", "Glenside", target_time="5:15PM", preferred_transfer="Jefferson Station")
    assert trip["origin_train"] == "9254"       # the target-time train, not the one the rider took
    app.commute._TRAINVIEW_CACHE = ([], 0.0)


def test_trip_follows_an_earlier_first_train_the_rider_took():
    plan_the_afternoon()
    with patch("app.commute.requests.get", side_effect=septa(LATER, [tv("9250", 40.1, -75.2, late=4)])):
        trip = get_trip_for_boarded_train("9250", "Ambler", "Glenside", "Jefferson Station")
    assert trip["origin_train"] == "9250"
    assert trip["origin_delay_minutes"] == 4
    assert trip["connection_train"] == "3556"   # 5:09 arrival still makes the 5:15
    assert trip["actual_arrival_time"] == "5:50PM"


def test_trip_for_the_connecting_train_is_just_the_ride_home():
    plan_the_afternoon()
    with patch("app.commute.requests.get", side_effect=septa(LATER, [tv("3560", 40.1, -75.2, late=3)])):
        trip = get_trip_for_boarded_train("3560", "Ambler", "Glenside", "Jefferson Station")
    assert trip["trip_type"] == "direct"
    assert trip["origin_train"] == "3560"
    assert trip["actual_arrival_time"] == "6:23PM"


def test_no_trip_for_a_train_never_listed_or_without_a_live_delay():
    plan_the_afternoon()
    with patch("app.commute.requests.get", side_effect=septa(LATER, [tv("7777", 40.1, -75.2)])):
        assert get_trip_for_boarded_train("7777", "Ambler", "Glenside", "Jefferson Station") is None
    app.commute._TRAINVIEW_CACHE = ([], 0.0)
    with patch("app.commute.requests.get", side_effect=septa(LATER, [tv("9250", 40.1, -75.2, late=None)])):
        assert get_trip_for_boarded_train("9250", "Ambler", "Glenside", "Jefferson Station") is None


# --- the API --------------------------------------------------------------------

def _dashboard(monkeypatch, responses, trainview):
    monkeypatch.setattr(app.main, "get_active_window", lambda *_: "afternoon")
    monkeypatch.setattr(app.main, "AFTERNOON_TARGET_DEPARTURE_TIME", "5:15PM")
    monkeypatch.setattr(app.main, "get_alerts", lambda: {"has_critical_alerts": False, "critical_alerts": []})
    monkeypatch.setattr(app.main, "get_weather", lambda: {})
    with patch("app.commute.requests.get", side_effect=septa(responses, trainview)):
        return app.main.get_dashboard()


def test_post_location_stores_the_fix_without_echoing_it():
    # Home Assistant sends last_seen with a space, not a "T", before the time.
    body = app.main.RiderFix.model_validate({
        "latitude": 40.1, "longitude": -75.2, "accuracy": 8,
        "last_seen": datetime.now().astimezone().isoformat(sep=" "), "zone": "not_home"})
    assert app.main.post_location(body) == {"status": "ok"}
    assert rider.current()["fresh"] is True


def test_dashboard_without_a_fix_is_unchanged(monkeypatch):
    commute = _dashboard(monkeypatch, PLAN, [])["commute"]
    assert commute["origin_train"] == "9254"
    assert commute["rider_on_train"] is None
    assert commute["rider_home"] is False
    assert commute["rider_location_age_minutes"] is None


def test_heading_home_holds_until_cleared_home_or_old():
    rider.update_fix(40.1, -75.2, zone="not_home", heading_home=True)
    assert rider.current()["heading_home"] is True
    rider.update_fix(40.11, -75.2, zone="not_home")             # flag not sent: unchanged
    assert rider.current()["heading_home"] is True
    rider.update_fix(40.12, -75.2, zone="At work", heading_home=False)
    assert rider.current()["heading_home"] is False
    rider.update_fix(40.13, -75.2, zone="not_home", heading_home=True)
    rider._state["heading_home"] -= rider.HEADING_HOME_MAX_AGE_SECONDS + 1
    assert rider.current()["heading_home"] is False             # stale: Home Assistant went quiet
    rider.update_fix(40.14, -75.2, zone="home", heading_home=True)
    assert rider.current()["heading_home"] is False             # home is home


def test_on_the_way_home_the_card_shows_the_next_train_they_can_make(monkeypatch):
    # The target time (5:15PM) would pick 9254; having left work, the first
    # train still reachable on foot is the one to show.
    assert _dashboard(monkeypatch, PLAN, [])["commute"]["origin_train"] == "9254"
    rider.update_fix(40.1, -75.2, accuracy=10, zone="not_home", heading_home=True)
    commute = _dashboard(monkeypatch, PLAN, [])["commute"]
    assert commute["origin_train"] == "9250"
    assert commute["rider_heading_home"] is True


def test_dashboard_follows_the_train_the_rider_is_on(monkeypatch):
    _dashboard(monkeypatch, PLAN, [])            # the afternoon's trains get listed
    feed(tv("9250", 40.100, -75.200, late=4))
    fix(40.101, -75.200, minutes_ago=2)
    feed(tv("9250", 40.120, -75.200, late=4))
    fix(40.119, -75.200)
    app.commute._TRAINVIEW_CACHE = ([], 0.0)
    commute = _dashboard(monkeypatch, LATER, [tv("9250", 40.120, -75.200, late=4)])["commute"]
    assert commute["rider_on_train"] == "9250"
    assert commute["origin_train"] == "9250"
    assert commute["connection_train"] == "3556"
    assert commute["rider_location_age_minutes"] == 0


MORNING = {("Ambler", "Glenside"): [
    {**leg("9260", "7:15AM", "7:49AM", delay="20 mins"), "isdirect": "true"},
    {**leg("9264", "7:45AM", "8:19AM"), "isdirect": "true"},
]}


def _morning(monkeypatch, trainview):
    monkeypatch.setattr(app.main, "get_active_window", lambda *_: "morning")
    monkeypatch.setattr(app.main, "DEFAULT_HOME_STATION", "Ambler")
    monkeypatch.setattr(app.main, "DEFAULT_WORK_STATION", "Glenside")
    monkeypatch.setattr(app.main, "get_alerts", lambda: {"has_critical_alerts": False, "critical_alerts": []})
    monkeypatch.setattr(app.main, "get_weather", lambda: {})
    with patch("app.commute.requests.get", side_effect=septa(MORNING, trainview)):
        return app.main.get_dashboard()


def test_a_train_with_no_gps_position_is_never_called_stalled(monkeypatch):
    monkeypatch.setattr(app.commute, "_now", lambda: datetime(2026, 1, 5, 7, 0))
    _morning(monkeypatch, [untracked_tv("9260", late=20)])
    app.commute._TRAINVIEW_CACHE = ([], 0.0)
    with patch("app.commute.time.time", return_value=time.time() + 1200):
        commute = _morning(monkeypatch, [untracked_tv("9260", late=40)])["commute"]
    assert commute["origin_train"] == "9260"
    assert commute["is_stalled"] is False
    assert commute["origin_position_unknown"] is True


def test_rider_moving_on_the_untracked_train_overrides_septas_parked_guess(monkeypatch):
    monkeypatch.setattr(app.commute, "_now", lambda: datetime(2026, 1, 5, 7, 20))
    trainview = [untracked_tv("9260", late=20), tv("9113", 40.3, -75.2)]
    _morning(monkeypatch, trainview)
    fix(40.100, -75.200, minutes_ago=2)
    fix(40.110, -75.200, minutes_ago=1)
    fix(40.120, -75.200)
    result = _morning(monkeypatch, trainview)
    commute = result["commute"]
    assert commute["rider_on_train"] == "9260"
    assert commute["origin_train"] == "9260"
    assert commute["is_stalled"] is False
    assert commute["origin_delay_unknown"] is True
    assert result["overall_status"] != "stalled"


def test_untracked_riding_names_no_train_when_two_listed_trains_have_no_position(monkeypatch):
    monkeypatch.setattr(app.commute, "_now", lambda: datetime(2026, 1, 5, 7, 20))
    trainview = [untracked_tv("9260", late=20), untracked_tv("9264")]
    _morning(monkeypatch, trainview)
    fix(40.100, -75.200, minutes_ago=2)
    fix(40.110, -75.200, minutes_ago=1)
    fix(40.120, -75.200)
    assert _morning(monkeypatch, trainview)["commute"]["rider_on_train"] is None


def test_dashboard_reports_the_rider_home(monkeypatch):
    fix(40.0, -75.0, zone="home")
    commute = _dashboard(monkeypatch, PLAN, [])["commute"]
    assert commute["rider_home"] is True
    assert commute["rider_on_train"] is None


# --- the last direct train of the morning has left ---------------------------------

# SEPTA keeps listing a train until it reaches the destination, so at 7:50 the
# 7:15 direct train is still on the list, ahead of a later trip with a change.
LAST_DIRECT = {
    ("Ambler", "Glenside"): [
        {**leg("9260", "7:15AM", "8:10AM"), "isdirect": "true"},
        {**leg("9270", "8:30AM", "9:05AM"), "term_train": "3300", "term_depart_time": "9:15AM",
         "term_arrival_time": "9:40AM", "term_delay": "On time", "Connection": "Jefferson Station"},
    ],
    ("Ambler", "Jefferson Station"): [leg("9270", "8:30AM", "9:05AM")],
    ("Jefferson Station", "Glenside"): [leg("3300", "9:15AM", "9:40AM")],
}


def _late_morning(monkeypatch):
    monkeypatch.setattr(app.commute, "_now", lambda: datetime(2026, 1, 5, 7, 50))
    monkeypatch.setattr(app.main, "get_active_window", lambda *_: "morning")
    monkeypatch.setattr(app.main, "DEFAULT_HOME_STATION", "Ambler")
    monkeypatch.setattr(app.main, "DEFAULT_WORK_STATION", "Glenside")
    monkeypatch.setattr(app.main, "get_alerts", lambda: {"has_critical_alerts": False, "critical_alerts": []})
    monkeypatch.setattr(app.main, "get_weather", lambda: {})
    with patch("app.commute.requests.get", side_effect=septa(LAST_DIRECT)):
        return app.main.get_dashboard()["commute"]


def test_a_departed_last_direct_train_gives_way_to_the_next_trip(monkeypatch):
    commute = _late_morning(monkeypatch)
    assert commute["origin_train"] == "9270"
    assert commute["already_departed"] is False


def test_rider_at_home_is_shown_the_next_trip_too(monkeypatch):
    fix(40.0, -75.0, zone="home")
    assert _late_morning(monkeypatch)["origin_train"] == "9270"


def test_rider_already_out_stays_with_the_train_that_left(monkeypatch):
    fix(40.1, -75.2)
    commute = _late_morning(monkeypatch)
    assert commute["origin_train"] == "9260"
    assert commute["already_departed"] is True
    assert commute["actual_arrival_time"] == "8:10AM"


def test_a_departed_direct_train_stays_when_nothing_later_is_listed(monkeypatch):
    monkeypatch.setitem(LAST_DIRECT, ("Ambler", "Glenside"), LAST_DIRECT[("Ambler", "Glenside")][:1])
    assert _late_morning(monkeypatch)["origin_train"] == "9260"


def test_rider_away_with_an_old_position_still_stays_with_that_train(monkeypatch):
    fix(40.1, -75.2, minutes_ago=30)     # in a tunnel: no new position for a while
    assert _late_morning(monkeypatch)["origin_train"] == "9260"


def test_the_first_post_after_leaving_work_sends_the_departure_push(monkeypatch):
    started = []
    monkeypatch.setattr(app.main, "run_soon", started.append)
    post = lambda **kw: app.main.post_location(app.main.RiderFix.model_validate(
        {"latitude": 40.1, "longitude": -75.2, "zone": "not_home", **kw}))
    post(zone="At work", heading_home=False)
    post(heading_home=True)
    post(heading_home=True)         # still on the way: no second push
    post()                          # flag not sent: unchanged
    assert started == [app.main.run_departure_check]
