"""Builds and sends the daily afternoon commute notification.

Always sends once at the scheduled time, but the message content adapts:
- Clear conditions -> a short, reassuring status update
- Delayed/at-risk conditions -> a more detailed warning, so she can decide
  whether it's worth waiting in a cool office rather than rushing out.
"""
from app.commute import get_commute_leg
from app.alerts import get_alerts
from app.config import WORK_STATION, HOME_STATION
from app.notifications import send_notification


def build_afternoon_message(commute: dict, alerts: dict) -> tuple[str, str, str, list[str]]:
    """Returns (title, message, priority, tags) for the notification."""

    if "error" in commute:
        return (
            "Commute check failed",
            "Couldn't retrieve train data right now. Check the dashboard manually.",
            "high",
            ["warning"],
        )

    if alerts.get("has_critical_alerts"):
        lines = ", ".join(a["line"] for a in alerts["critical_alerts"])
        return (
            "SEPTA service alert",
            f"There's an active alert on {lines}. Check the dashboard before you head out.",
            "urgent",
            ["rotating_light"],
        )

    if commute.get("at_risk"):
        return (
            "Tight connection today",
            f"Train {commute['origin_train']} departs {commute['origin_actual_departure_time']}, "
            f"running {commute['origin_delay_minutes']} min late - only ~"
            f"{round(commute['transfer_buffer_minutes'])} min to make your connection at "
            f"{commute.get('connection_station', 'the transfer point')}.",
            "high",
            ["warning"],
        )

    if commute.get("delayed"):
        return (
            "Running behind today",
            f"Train {commute['origin_train']} departed {commute['origin_actual_departure_time']}, "
            f"running {commute['total_delay_minutes']} min behind overall. "
            f"Expected arrival: {commute['actual_arrival_time']} (scheduled {commute['arrival_time']}). "
            f"Might be worth waiting it out if you're comfortable where you are.",
            "default",
            ["yellow_circle"],
        )

    return (
        "Commute looks good",
        f"Train {commute['origin_train']} departs {commute['origin_actual_departure_time']}, "
        f"on time, arriving {commute['actual_arrival_time']}.",
        "default",
        ["white_check_mark"],
    )


def run_afternoon_check():
    """Entry point called by the scheduler. Checks conditions and sends
    exactly one notification for the afternoon commute."""
    commute = get_commute_leg(WORK_STATION, HOME_STATION)
    alerts = get_alerts()

    title, message, priority, tags = build_afternoon_message(commute, alerts)
    success = send_notification(title, message, priority=priority, tags=tags)

    if success:
        print(f"[afternoon_check] Notification sent: {title}")
    else:
        print(f"[afternoon_check] Notification FAILED to send: {title}")
