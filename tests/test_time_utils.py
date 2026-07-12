from datetime import datetime

from app.time_utils import (
    parse_time,
    parse_delay_minutes,
    format_time_no_leading_zero,
    anchor_to_today,
    is_recent,
)


class TestParseTime:
    def test_parses_pm_time(self):
        result = parse_time("2:03PM")
        assert result.hour == 14
        assert result.minute == 3

    def test_parses_am_time(self):
        result = parse_time("6:45AM")
        assert result.hour == 6
        assert result.minute == 45

    def test_parses_noon(self):
        result = parse_time("12:00PM")
        assert result.hour == 12

    def test_parses_midnight(self):
        result = parse_time("12:00AM")
        assert result.hour == 0


class TestParseDelayMinutes:
    def test_on_time_returns_zero(self):
        assert parse_delay_minutes("On time") == 0

    def test_on_time_case_insensitive(self):
        assert parse_delay_minutes("on time") == 0
        assert parse_delay_minutes("ON TIME") == 0

    def test_extracts_delay_number(self):
        assert parse_delay_minutes("5 mins") == 5
        assert parse_delay_minutes("20 mins") == 20

    def test_handles_none(self):
        assert parse_delay_minutes(None) == 0

    def test_handles_empty_string(self):
        assert parse_delay_minutes("") == 0

    def test_handles_unparseable_string(self):
        # No digits present - should default to 0 rather than crash
        assert parse_delay_minutes("delayed") == 0


class TestFormatTimeNoLeadingZero:
    def test_strips_leading_zero(self):
        dt = datetime(2026, 1, 1, 9, 5)  # 9:05 AM
        assert format_time_no_leading_zero(dt) == "9:05AM"

    def test_keeps_double_digit_hour(self):
        dt = datetime(2026, 1, 1, 14, 30)  # 2:30 PM
        assert format_time_no_leading_zero(dt) == "2:30PM"

    def test_noon(self):
        dt = datetime(2026, 1, 1, 12, 0)
        assert format_time_no_leading_zero(dt) == "12:00PM"


class TestAnchorToToday:
    def test_anchors_1900_dated_time_to_todays_date(self):
        # parse_time-style object: correct hour/minute, wrong (1900) year
        stale_date_time = parse_time("2:03PM")
        now = datetime(2026, 7, 12, 10, 0, 0)

        result = anchor_to_today(now, stale_date_time)

        assert result.year == 2026
        assert result.month == 7
        assert result.day == 12
        assert result.hour == 14
        assert result.minute == 3


class TestIsRecent:
    def test_recent_date_within_window(self):
        # Format matches SEPTA's last_updated: "Jul 10 2026  2:00PM"
        recent = datetime.now()
        date_str = recent.strftime("%b %d %Y %I:%M%p")
        assert is_recent(date_str, days=3) is True

    def test_stale_date_outside_window(self):
        assert is_recent("May 30 2023  9:03PM", days=3) is False

    def test_malformed_date_returns_false(self):
        assert is_recent("not a date", days=3) is False

    def test_empty_string_returns_false(self):
        assert is_recent("", days=3) is False
