from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest

from app.calendar_data import get_todays_events

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)


def _ics(events_block: str) -> str:
    return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
{events_block}
END:VCALENDAR
"""


def mock_response(text: str, status_code: int = 200):
    mock = MagicMock()
    mock.text = text
    mock.status_code = status_code
    mock.raise_for_status = MagicMock()
    if status_code != 200:
        mock.raise_for_status.side_effect = Exception("HTTP error")
    return mock


class TestNoCalendarConfigured:
    @patch("app.calendar_data.CALENDAR_ICS_URL", None)
    def test_returns_error_when_url_not_set(self):
        result = get_todays_events()
        assert "error" in result
        assert result["events"] == []


class TestFeedFailures:
    @patch("app.calendar_data.CALENDAR_ICS_URL", "https://example.com/feed.ics")
    @patch("app.calendar_data.requests.get")
    def test_network_failure_returns_error_not_exception(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException("timed out")
        result = get_todays_events()
        assert "error" in result
        assert result["events"] == []

    @patch("app.calendar_data.CALENDAR_ICS_URL", "https://example.com/feed.ics")
    @patch("app.calendar_data.requests.get")
    def test_malformed_ics_returns_error_not_exception(self, mock_get):
        mock_get.return_value = mock_response("this is not valid ICS data at all")
        result = get_todays_events()
        assert "error" in result
        assert result["events"] == []


class TestEventParsing:
    @patch("app.calendar_data.CALENDAR_ICS_URL", "https://example.com/feed.ics")
    @patch("app.calendar_data.requests.get")
    def test_timed_event_today(self, mock_get):
        ics = _ics(f"""BEGIN:VEVENT
UID:1
DTSTART;TZID=America/New_York:{TODAY.strftime('%Y%m%d')}T140000
DTEND;TZID=America/New_York:{TODAY.strftime('%Y%m%d')}T150000
SUMMARY:Dentist appointment
END:VEVENT""")
        mock_get.return_value = mock_response(ics)
        result = get_todays_events()
        assert "error" not in result
        assert len(result["events"]) == 1
        assert result["events"][0]["title"] == "Dentist appointment"
        assert result["events"][0]["all_day"] is False
        assert result["events"][0]["start_time"] == "2:00PM"

    @patch("app.calendar_data.CALENDAR_ICS_URL", "https://example.com/feed.ics")
    @patch("app.calendar_data.requests.get")
    def test_all_day_event_today(self, mock_get):
        ics = _ics(f"""BEGIN:VEVENT
UID:2
DTSTART;VALUE=DATE:{TODAY.strftime('%Y%m%d')}
DTEND;VALUE=DATE:{TOMORROW.strftime('%Y%m%d')}
SUMMARY:Anniversary
END:VEVENT""")
        mock_get.return_value = mock_response(ics)
        result = get_todays_events()
        assert len(result["events"]) == 1
        assert result["events"][0]["all_day"] is True
        assert result["events"][0]["start_time"] is None

    @patch("app.calendar_data.CALENDAR_ICS_URL", "https://example.com/feed.ics")
    @patch("app.calendar_data.requests.get")
    def test_event_tomorrow_is_excluded(self, mock_get):
        ics = _ics(f"""BEGIN:VEVENT
UID:3
DTSTART;TZID=America/New_York:{TOMORROW.strftime('%Y%m%d')}T100000
DTEND;TZID=America/New_York:{TOMORROW.strftime('%Y%m%d')}T110000
SUMMARY:Tomorrow's event
END:VEVENT""")
        mock_get.return_value = mock_response(ics)
        result = get_todays_events()
        assert result["events"] == []

    @patch("app.calendar_data.CALENDAR_ICS_URL", "https://example.com/feed.ics")
    @patch("app.calendar_data.requests.get")
    def test_all_day_events_sort_before_timed_events(self, mock_get):
        ics = _ics(f"""BEGIN:VEVENT
UID:1
DTSTART;TZID=America/New_York:{TODAY.strftime('%Y%m%d')}T080000
DTEND;TZID=America/New_York:{TODAY.strftime('%Y%m%d')}T083000
SUMMARY:Early meeting
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;VALUE=DATE:{TODAY.strftime('%Y%m%d')}
DTEND;VALUE=DATE:{TOMORROW.strftime('%Y%m%d')}
SUMMARY:All day thing
END:VEVENT""")
        mock_get.return_value = mock_response(ics)
        result = get_todays_events()
        assert len(result["events"]) == 2
        assert result["events"][0]["title"] == "All day thing"
        assert result["events"][1]["title"] == "Early meeting"

    @patch("app.calendar_data.CALENDAR_ICS_URL", "https://example.com/feed.ics")
    @patch("app.calendar_data.requests.get")
    def test_timed_events_sort_chronologically(self, mock_get):
        ics = _ics(f"""BEGIN:VEVENT
UID:1
DTSTART;TZID=America/New_York:{TODAY.strftime('%Y%m%d')}T160000
DTEND;TZID=America/New_York:{TODAY.strftime('%Y%m%d')}T163000
SUMMARY:Afternoon thing
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;TZID=America/New_York:{TODAY.strftime('%Y%m%d')}T090000
DTEND;TZID=America/New_York:{TODAY.strftime('%Y%m%d')}T093000
SUMMARY:Morning thing
END:VEVENT""")
        mock_get.return_value = mock_response(ics)
        result = get_todays_events()
        assert [e["title"] for e in result["events"]] == ["Morning thing", "Afternoon thing"]

    @patch("app.calendar_data.CALENDAR_ICS_URL", "https://example.com/feed.ics")
    @patch("app.calendar_data.requests.get")
    def test_recurring_event_expands_for_today(self, mock_get):
        # Recurring daily standup starting weeks before today, should still
        # produce an occurrence for today.
        past_start = TODAY - timedelta(days=30)
        ics = _ics(f"""BEGIN:VEVENT
UID:1
DTSTART;TZID=America/New_York:{past_start.strftime('%Y%m%d')}T090000
DTEND;TZID=America/New_York:{past_start.strftime('%Y%m%d')}T093000
RRULE:FREQ=DAILY
SUMMARY:Daily standup
END:VEVENT""")
        mock_get.return_value = mock_response(ics)
        result = get_todays_events()
        assert len(result["events"]) == 1
        assert result["events"][0]["title"] == "Daily standup"
        assert result["events"][0]["start_time"] == "9:00AM"
