"""The afternoon push and the follow-up checks that run until the train
leaves: a follow-up is sent only when things got worse than the last push."""
from datetime import datetime
from unittest.mock import patch

import pytest

import app.afternoon_check as ac

NO_ALERTS = {"has_critical_alerts": False, "critical_alerts": []}
SUSPENSION = {"has_critical_alerts": True, "critical_alerts": [
    {"line": "Line A", "flags": {"suspended": True}, "alert_text": "<p>Suspended&nbsp;near Center</p>"}]}


def commute(**overrides):
    base = {
        "trip_type": "transfer", "origin_train": "1001",
        "origin_actual_departure_time": "5:15PM", "origin_delay_minutes": 0,
        "actual_arrival_time": "6:20PM", "arrival_time": "6:20PM",
        "planned_arrival_time": "6:20PM", "total_delay_minutes": 0,
        "origin_departure_time": "5:15PM", "origin_arrival_time": "5:35PM",
        "connection_station": "Center Station", "connection_train": "2002",
        "connection_actual_departure_time": "5:45PM", "transfer_buffer_minutes": 10.0,
    }
    base.update(overrides)
    return base


class Day:
    """Drives the afternoon push and follow-ups with scripted SEPTA results."""

    def __init__(self, monkeypatch):
        self.sent = []
        self.now = datetime(2026, 1, 5, 16, 30)
        self.result = (commute(), NO_ALERTS)
        self.weather = {}
        self.checks = 0
        self.ride = commute()       # what get_connection_update returns once on the first train
        self.rides = []             # (trip, fallback_late) for each connection check
        monkeypatch.setattr(ac, "_now", lambda: self.now)
        monkeypatch.setattr(ac, "_check_return_commute", self._check)
        monkeypatch.setattr(ac, "get_weather", lambda: self.weather)
        monkeypatch.setattr(ac, "get_alerts", lambda: NO_ALERTS)
        monkeypatch.setattr(ac, "get_connection_update", self._ride)
        monkeypatch.setattr(ac, "send_notification",
                            lambda title, message, **kw: self.sent.append((title, message)) or True)

    def _check(self):
        self.checks += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def _ride(self, trip, destination, fallback_late=None):
        self.rides.append((trip, fallback_late))
        if isinstance(self.ride, Exception):
            raise self.ride
        return self.ride

    def at(self, hhmm, result=None):
        hour, minute = map(int, hhmm.split(":"))
        self.now = self.now.replace(hour=hour, minute=minute)
        if result is not None:
            self.result = result

    def titles(self):
        return [t for t, _ in self.sent]


@pytest.fixture
def day(monkeypatch):
    return Day(monkeypatch)


def test_no_follow_up_when_nothing_changes(day):
    ac.run_afternoon_check()
    for t in ("16:35", "16:40", "16:45"):
        day.at(t)
        ac.run_follow_up_check()
    assert day.titles() == ["Commute looks good"]


def test_cancellation_after_the_push_is_sent(day):
    ac.run_afternoon_check()
    day.at("16:50", (commute(is_cancelled=True, backup_train_departure="5:45PM"), NO_ALERTS))
    ac.run_follow_up_check()
    day.at("16:55")
    ac.run_follow_up_check()  # same news, not repeated
    assert day.titles() == ["Commute looks good", "Update: TRAIN CANCELLED"]
    assert "5:45PM" in day.sent[1][1]


def test_growing_delay_is_sent_in_ten_minute_steps(day):
    day.result = (commute(total_delay_minutes=12, delayed=True), NO_ALERTS)
    ac.run_afternoon_check()
    for t, delay in (("16:35", 15), ("16:40", 21), ("16:45", 25)):
        day.at(t, (commute(total_delay_minutes=delay, delayed=True), NO_ALERTS))
        ac.run_follow_up_check()
    assert day.titles() == ["Running behind today", "Update: Running behind today"]
    assert "running 25 min behind" in day.sent[1][1]


def test_improvement_is_not_pushed(day):
    day.result = (commute(total_delay_minutes=20, delayed=True), NO_ALERTS)
    ac.run_afternoon_check()
    day.at("16:35", (commute(), NO_ALERTS))
    ac.run_follow_up_check()
    assert day.titles() == ["Running behind today"]


def test_direct_trip_follow_ups_stop_once_the_train_leaves(day):
    day.result = (commute(trip_type="direct", connection_station=None), NO_ALERTS)
    ac.run_afternoon_check()
    day.checks = 0
    day.at("17:15", (commute(is_cancelled=True), NO_ALERTS))
    ac.run_follow_up_check()
    assert day.checks == 0
    assert day.titles() == ["Commute looks good"]


def test_direct_trip_follow_ups_stop_when_septa_lists_the_next_train(day):
    day.result = (commute(trip_type="direct", connection_station=None), NO_ALERTS)
    ac.run_afternoon_check()
    day.at("17:00", (commute(trip_type="direct", connection_station=None,
                             origin_train="1003", is_cancelled=True), NO_ALERTS))
    ac.run_follow_up_check()
    day.at("17:05")
    ac.run_follow_up_check()
    assert day.titles() == ["Commute looks good"]
    assert day.checks == 2  # the push and one follow-up; then it stopped


def test_follow_ups_only_run_on_the_day_of_the_push(day):
    ac.run_afternoon_check()
    day.now = datetime(2026, 1, 6, 16, 35)
    day.checks = 0
    ac.run_follow_up_check()
    assert day.checks == 0


def test_repeated_failures_send_one_paused_notice(day):
    ac.run_afternoon_check()
    for t in ("16:35", "16:40", "16:45"):
        day.at(t, ConnectionError("SEPTA down"))
        ac.run_follow_up_check()
    assert day.titles() == ["Commute looks good", "Commute updates paused"]


def test_septa_error_counts_as_a_failed_follow_up(day):
    ac.run_afternoon_check()
    for t in ("16:35", "16:40"):
        day.at(t, ({"error": "SEPTA trip data unavailable"}, NO_ALERTS))
        ac.run_follow_up_check()
    assert day.titles()[-1] == "Commute updates paused"


def test_failed_push_is_followed_by_the_real_status(day):
    day.result = RuntimeError("boom")
    ac.run_afternoon_check()
    day.at("16:35", (commute(), NO_ALERTS))
    ac.run_follow_up_check()
    assert day.titles() == ["Commute check failed", "Update: Commute looks good"]


def test_cancellation_after_a_service_alert_push_is_still_sent(day):
    day.result = (commute(), SUSPENSION)
    ac.run_afternoon_check()
    day.at("16:40", (commute(is_cancelled=True), NO_ALERTS))
    ac.run_follow_up_check()
    assert day.titles() == ["SEPTA service alert", "Update: TRAIN CANCELLED"]


def test_alert_text_is_plain_in_the_push(day):
    day.result = (commute(), SUSPENSION)
    ac.run_afternoon_check()
    assert "Line A: Suspended near Center" in day.sent[0][1]


def test_evening_weather_is_added_to_the_push(day):
    day.weather = {"evening_warning": "Heavy rain around 5PM"}
    ac.run_afternoon_check()
    assert "Weather: Heavy rain around 5PM on the way home." in day.sent[0][1]


def test_dry_evening_adds_no_weather_line(day):
    ac.run_afternoon_check()
    assert "Weather" not in day.sent[0][1]


def test_weather_failure_does_not_block_the_push(day, monkeypatch):
    def broken():
        raise ValueError("bad forecast")
    monkeypatch.setattr(ac, "get_weather", broken)
    ac.run_afternoon_check()
    assert day.titles() == ["Commute looks good"]


@pytest.mark.parametrize("trip, alerts", [
    (commute(), SUSPENSION),
    (commute(is_cancelled=True), NO_ALERTS),
    (commute(planned_connection_cancelled=True, planned_connection_train="2000"), NO_ALERTS),
    (commute(is_stalled=True), NO_ALERTS),
    (commute(origin_delay_unknown=True, origin_delay_text="Delayed"), NO_ALERTS),
    (commute(missed_connection=True), NO_ALERTS),
    (commute(at_risk=True, transfer_buffer_minutes=-2.0), NO_ALERTS),
    (commute(connection_delay_minutes=12), NO_ALERTS),
    (commute(delayed=True, total_delay_minutes=12), NO_ALERTS),
    (commute(total_delay_minutes=3), NO_ALERTS),
    (commute(), NO_ALERTS),
    ({"error": "down"}, NO_ALERTS),
])
def test_every_push_title_has_a_severity(trip, alerts):
    title = ac.build_afternoon_message(trip, alerts)[0]
    assert title in ac.SEVERITY


@patch("app.main.get_weather", return_value={})
@patch("app.main.get_alerts", return_value=SUSPENSION)
@patch("app.main.get_commute", return_value=commute())
def test_dashboard_carries_plain_alert_text_for_home_assistant(*_):
    from app.main import get_dashboard
    d = get_dashboard(origin="A", destination="B")
    assert d["commute"]["service_alerts"] == ["Line A: Suspended near Center"]
    assert d["commute"]["alerts_unavailable"] is False
    assert d["overall_status"] == "alert"


# ---- On the first train: following the connection ----

def test_cancelled_connection_is_pushed_while_on_the_first_train(day):
    ac.run_afternoon_check()
    day.at("17:20")  # the 5:15 has left
    day.ride = commute(connection_cancelled=True, backup_train_departure="5:30PM")
    ac.run_follow_up_check()
    assert day.titles() == ["Commute looks good", "Update: TRAIN CANCELLED"]
    assert day.rides[0][0]["origin_train"] == "1001"


def test_missed_connection_is_pushed_while_on_the_first_train(day):
    ac.run_afternoon_check()
    day.at("17:25")
    day.ride = commute(origin_delay_minutes=16, missed_connection=True, connection_train="2004",
                       connection_actual_departure_time="6:15PM", actual_arrival_time="6:50PM",
                       total_delay_minutes=30, delayed=True)
    ac.run_follow_up_check()
    assert day.titles() == ["Commute looks good", "Update: Missed Connection Alert"]
    assert "2004" in day.sent[1][1]


def test_switches_to_the_connection_when_septa_lists_the_next_train(day):
    ac.run_afternoon_check()
    day.ride = commute(connection_cancelled=True)
    day.at("17:10", (commute(origin_train="1003"), NO_ALERTS))  # the 5:15 left early
    ac.run_follow_up_check()
    assert len(day.rides) == 1  # checked the connection right away
    assert day.titles()[-1] == "Update: TRAIN CANCELLED"


def test_connection_follow_ups_stop_once_the_connection_leaves(day):
    ac.run_afternoon_check()
    day.at("17:20")
    ac.run_follow_up_check()
    day.at("17:50")  # the 5:45 connection has left
    ac.run_follow_up_check()
    assert len(day.rides) == 1


def test_later_connection_extends_the_follow_ups(day):
    ac.run_afternoon_check()
    day.at("17:25")
    day.ride = commute(missed_connection=True, connection_train="2004",
                       connection_actual_departure_time="6:15PM", total_delay_minutes=30)
    ac.run_follow_up_check()
    day.at("17:55")
    ac.run_follow_up_check()
    assert len(day.rides) == 2


def test_lost_train_before_the_transfer_is_a_failure(day):
    ac.run_afternoon_check()
    for t in ("17:20", "17:25"):
        day.at(t)
        day.ride = {"error": "Train 1001 has no live position"}
        ac.run_follow_up_check()
    assert [fb for _, fb in day.rides] == [None, None]
    assert day.titles() == ["Commute looks good", "Commute updates paused"]


def test_last_known_delay_is_used_once_the_train_should_have_arrived(day):
    ac.run_afternoon_check()
    day.at("17:36")  # scheduled at the transfer station at 5:35
    ac.run_follow_up_check()
    assert day.rides[0][1] == 0


def test_late_first_train_that_still_connects_does_not_buzz(day):
    ac.run_afternoon_check()
    day.at("16:40", (commute(origin_delay_minutes=4, origin_actual_departure_time="5:19PM"), NO_ALERTS))
    ac.run_follow_up_check()
    assert day.titles() == ["Commute looks good"]


# --- the "Left work" push (Home Assistant says the rider has left work) -------

def test_leaving_work_pushes_the_next_train_and_skips_the_scheduled_push(day):
    day.at("15:40")
    ac.run_departure_check()
    day.at("16:00")
    ac.run_afternoon_check()        # the target-time train would be the wrong one now
    assert day.titles() == ["Left work: Commute looks good"]


def test_leaving_work_after_the_scheduled_push_still_pushes(day):
    ac.run_afternoon_check()
    day.at("16:50")
    ac.run_departure_check()
    assert day.titles() == ["Commute looks good", "Left work: Commute looks good"]


def test_no_departure_push_before_the_afternoon_or_on_weekends(day):
    day.at("12:30")                 # out for lunch
    ac.run_departure_check()
    day.now = day.now.replace(day=10, hour=16)  # a Saturday
    ac.run_departure_check()
    assert day.sent == []


def test_a_repeat_departure_soon_after_is_the_same_one(day):
    day.at("16:40")
    ac.run_departure_check()
    day.at("16:50")
    ac.run_departure_check()
    day.at("17:05")
    ac.run_departure_check()        # back at work and out again: a new departure
    assert day.titles() == ["Left work: Commute looks good"] * 2


def test_follow_ups_run_into_the_evening_after_a_late_departure(day):
    late = dict(origin_actual_departure_time="7:50PM", origin_departure_time="7:50PM",
                origin_arrival_time="8:10PM", connection_actual_departure_time="8:20PM")
    day.at("19:30", (commute(**late), NO_ALERTS))
    ac.run_departure_check()
    day.at("19:35", (commute(**late, is_cancelled=True, backup_train_departure="8:20PM"), NO_ALERTS))
    ac.run_follow_up_check()
    assert day.titles() == ["Left work: Commute looks good", "Update: TRAIN CANCELLED"]


def test_after_leaving_work_the_trip_is_the_next_reachable_train(monkeypatch):
    asked = []
    monkeypatch.setattr(ac, "_now", lambda: datetime(2026, 1, 5, 16, 0))
    monkeypatch.setattr(ac, "get_alerts", lambda: NO_ALERTS)
    monkeypatch.setattr(ac, "get_commute", lambda *a, **kw: asked.append(kw["target_time"]) or commute())
    ac._check_return_commute()
    ac._follow_up["heading_home"] = datetime(2026, 1, 5).date()
    ac._check_return_commute()
    assert asked == [ac.AFTERNOON_TARGET_DEPARTURE_TIME, None]


def test_no_pushes_once_the_rider_is_home(day, monkeypatch):
    # 2026-11-12: a service-alert push went out at the scheduled time though
    # the rider had been home for half an hour.
    home = {"home": False}
    monkeypatch.setattr(ac.rider, "current", lambda: {"home": home["home"]})
    ac.run_afternoon_check()
    home["home"] = True
    day.at("16:50", (commute(is_cancelled=True), NO_ALERTS))
    ac.run_follow_up_check()
    day.now = day.now.replace(day=6, hour=16, minute=30)  # the next day, already home
    ac.run_afternoon_check()
    assert day.titles() == ["Commute looks good"]


def test_without_a_fresh_fix_the_push_still_goes_out(day, monkeypatch):
    monkeypatch.setattr(ac.rider, "current", lambda: None)  # no GPS posted
    ac.run_afternoon_check()
    assert day.titles() == ["Commute looks good"]
