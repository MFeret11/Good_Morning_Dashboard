"""Pulls today's events from a shared private Google Calendar ICS feed
(Google Calendar Settings -> "Secret address in iCal format" - read-only,
no OAuth needed). Google refreshes this feed every few hours on their end,
so expect similar lag; fine for a "what's on today" morning glance, not
meant for real-time updates."""
from datetime import date, datetime, timedelta

import icalendar
import recurring_ical_events
import requests

from app.config import CALENDAR_ICS_URL
from app.time_utils import format_time_no_leading_zero


def get_todays_events() -> dict:
    """Returns today's events, all-day events first, then timed events in
    chronological order. Never raises - returns {"error": ...} on failure
    (feed unreachable, malformed ICS, etc.) so a calendar hiccup never
    crashes the dashboard."""
    if not CALENDAR_ICS_URL:
        return {"error": "Calendar not configured (CALENDAR_ICS_URL not set)", "events": []}

    try:
        response = requests.get(CALENDAR_ICS_URL, timeout=10)
        response.raise_for_status()
        cal = icalendar.Calendar.from_ical(response.text)
    except (requests.RequestException, ValueError) as e:
        return {"error": f"Couldn't load calendar: {e}", "events": []}

    today = date.today()
    tomorrow = today + timedelta(days=1)
    occurrences = recurring_ical_events.of(cal).between(today, tomorrow)

    events = []
    for occ in occurrences:
        start = occ.get("DTSTART").dt
        all_day = not isinstance(start, datetime)
        events.append({
            "title": str(occ.get("SUMMARY", "Untitled event")),
            "all_day": all_day,
            "start_time": None if all_day else format_time_no_leading_zero(start),
            "_sort_hour": 0 if all_day else start.hour,
            "_sort_minute": 0 if all_day else start.minute,
        })

    events.sort(key=lambda e: (not e["all_day"], e["_sort_hour"], e["_sort_minute"]))
    for e in events:
        del e["_sort_hour"]
        del e["_sort_minute"]

    return {"events": events}
