"""Small time-parsing/formatting helpers shared across modules."""
import re
from datetime import datetime


def parse_time(t: str) -> datetime:
    """Parse SEPTA's '2:03PM' style strings. Returned datetime is anchored
    to year 1900 (strptime's default) - only .hour/.minute should be trusted."""
    return datetime.strptime(t, "%I:%M%p")


def parse_delay_minutes(delay_str: str) -> int:
    """Convert SEPTA's delay string into minutes. 'On time' -> 0, '5 mins' -> 5."""
    if not delay_str or "on time" in delay_str.lower():
        return 0
    match = re.search(r"(\d+)", delay_str)
    return int(match.group(1)) if match else 0


def format_time_no_leading_zero(dt: datetime) -> str:
    """Format a datetime as '2:03PM' (no leading zero), cross-platform safe."""
    return dt.strftime("%I:%M%p").lstrip("0")


def anchor_to_today(reference_now: datetime, time_of_day: datetime) -> datetime:
    """Take a datetime that only has a meaningful hour/minute (e.g. from
    parse_time, which defaults to year 1900) and re-anchor it to today's date."""
    return reference_now.replace(
        hour=time_of_day.hour, minute=time_of_day.minute, second=0, microsecond=0
    )


def is_recent(date_str: str, days: int = 3) -> bool:
    """Check whether a SEPTA 'last_updated' timestamp falls within the last N days."""
    try:
        parsed = datetime.strptime(date_str.strip(), "%b %d %Y %I:%M%p")
        return (datetime.now() - parsed).days <= days
    except (ValueError, AttributeError):
        return False
