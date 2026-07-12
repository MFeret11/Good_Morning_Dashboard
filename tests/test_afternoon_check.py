from app.afternoon_check import build_afternoon_message


def make_commute(**overrides):
    base = {
        "trip_type": "transfer",
        "origin_train": "2453",
        "origin_delay_minutes": 0,
        "leave_by_time": "5:48PM",
        "arrival_time": "7:19PM",
        "actual_arrival_time": "7:19PM",
        "total_delay_minutes": 0,
        "delayed": False,
        "at_risk": False,
        "transfer_buffer_minutes": 20.0,
        "connection_station": "30th Street Station",
    }
    base.update(overrides)
    return base


def make_alerts(**overrides):
    base = {
        "has_critical_alerts": False,
        "critical_alerts": [],
        "has_recent_advisories": False,
        "recent_advisories": [],
    }
    base.update(overrides)
    return base


class TestBuildAfternoonMessage:
    def test_commute_error_produces_failure_message(self):
        title, message, priority, tags = build_afternoon_message(
            {"error": "No trips found"}, make_alerts()
        )
        assert "failed" in title.lower()
        assert priority == "high"

    def test_critical_alert_takes_top_priority(self):
        commute = make_commute(at_risk=True)  # even if also at_risk
        alerts = make_alerts(
            has_critical_alerts=True,
            critical_alerts=[{"line": "Manayunk/Norristown", "flags": {"suspended": True}}],
        )
        title, message, priority, tags = build_afternoon_message(commute, alerts)
        assert "alert" in title.lower()
        assert priority == "urgent"
        assert "Manayunk/Norristown" in message

    def test_at_risk_produces_warning_with_details(self):
        commute = make_commute(at_risk=True, origin_delay_minutes=20, transfer_buffer_minutes=3.0)
        title, message, priority, tags = build_afternoon_message(commute, make_alerts())
        assert priority == "high"
        assert "20" in message  # delay minutes mentioned
        assert commute["leave_by_time"] in message

    def test_delayed_but_not_at_risk_produces_default_priority(self):
        commute = make_commute(delayed=True, total_delay_minutes=17,
                                actual_arrival_time="7:36PM")
        title, message, priority, tags = build_afternoon_message(commute, make_alerts())
        assert priority == "default"
        assert "17" in message

    def test_clean_trip_produces_reassuring_message(self):
        commute = make_commute()  # all defaults: on time, not delayed, not at risk
        title, message, priority, tags = build_afternoon_message(commute, make_alerts())
        assert "good" in title.lower()
        assert priority == "default"
        assert commute["origin_train"] in message

    def test_all_responses_return_four_values(self):
        # Guards against a future refactor accidentally changing the return shape
        result = build_afternoon_message(make_commute(), make_alerts())
        assert len(result) == 4
        title, message, priority, tags = result
        assert isinstance(title, str)
        assert isinstance(message, str)
        assert isinstance(priority, str)
        assert isinstance(tags, list)

    def test_titles_are_ascii_only(self):
        # Regression guard: ntfy titles are sent as raw HTTP headers, which
        # must be Latin-1/ASCII. A stray emoji here caused a real
        # UnicodeEncodeError crash in production - titles must stay plain text.
        # (Emoji belong in `tags`, which ntfy renders as icons separately.)
        scenarios = [
            ({"error": "No trips found"}, make_alerts()),
            (make_commute(), make_alerts(has_critical_alerts=True,
             critical_alerts=[{"line": "Media/Wawa", "flags": {}}])),
            (make_commute(at_risk=True), make_alerts()),
            (make_commute(delayed=True), make_alerts()),
            (make_commute(), make_alerts()),
        ]
        for commute, alerts in scenarios:
            title, _, _, _ = build_afternoon_message(commute, alerts)
            title.encode("ascii")  # raises UnicodeEncodeError if non-ASCII sneaks in
