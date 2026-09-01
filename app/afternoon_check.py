"""Builds and sends the daily afternoon commute notification.
Anchored to the ~4:50 PM East Falls train and checks the Jefferson transfer."""
from app.commute import get_afternoon_commute
from app.alerts import get_alerts
from app.config import WORK_STATION, HOME_STATION, PREFERRED_TRANSFER_STATION, AFTERNOON_TARGET_DEPARTURE_TIME
from app.notifications import send_notification


def build_afternoon_message(commute: dict, alerts: dict) -> tuple[str, str, str, list[str]]:
    if "error" in commute:
        return (
            "Commute Check Failed",
            f"Could not retrieve train data: {commute['error']}. Check SEPTA manually.",
            "high",
            ["warning"],
        )

    if alerts.get("has_critical_alerts"):
        lines = ", ".join(a["line"] for a in alerts["critical_alerts"])
        return (
            "🚨 SEPTA Service Alert",
            f"Active alerts on {lines}. Check dashboard before leaving.",
            "urgent",
            ["rotating_light"],
        )

    if commute.get("missed_connection"):
        return (
            "🚨 Missed Connection Alert",
            f"Train {commute['origin_train']} arrives at {commute.get('connection_station')} too late for the usual Media train. "
            f"Next realistic arrival at Media: {commute['actual_arrival_time']}.",
            "urgent",
            ["warning"],
        )

    if commute.get("at_risk"):
        return (
            "⚠️ Tight Connection at Jefferson",
            f"Train {commute['origin_train']} leaves East Falls at {commute['origin_actual_departure_time']} "
            f"(+{commute['origin_delay_minutes']}m). Transfer window is only ~{commute['transfer_buffer_minutes']} min at Jefferson!",
            "high",
            ["warning"],
        )

    # Connecting train (Media line) is significantly delayed
    if commute.get("connection_delay_minutes", 0) >= 10:
        return (
            "🟡 Connecting Media Train Delayed",
            f"Your East Falls train is on time, but connecting train {commute.get('connection_train')} at Jefferson "
            f"is running {commute['connection_delay_minutes']} min late. Expected home: {commute['actual_arrival_time']}.",
            "default",
            ["hourglass"],
        )

    if commute.get("delayed"):
        return (
            "🟡 Commute Running Behind",
            f"Departing East Falls at {commute['origin_actual_departure_time']}. "
            f"Overall arrival at Media delayed to {commute['actual_arrival_time']} (+{commute['total_delay_minutes']} min).",
            "default",
            ["yellow_circle"],
        )

    return (
        "🟢 Commute Looks Great",
        f"Train {commute['origin_train']} departs East Falls at {commute['origin_actual_departure_time']}. "
        f"Clean ~{commute['transfer_buffer_minutes']} min connection at Jefferson. Home by {commute['actual_arrival_time']}.",
        "default",
        ["white_check_mark"],
    )


def run_afternoon_check():
    commute = get_afternoon_commute(
        WORK_STATION, PREFERRED_TRANSFER_STATION, HOME_STATION, AFTERNOON_TARGET_DEPARTURE_TIME
    )
    alerts = get_alerts()

    title, message, priority, tags = build_afternoon_message(commute, alerts)
    success = send_notification(title, message, priority=priority, tags=tags)

    if success:
        print(f"[afternoon_check] Notification sent: {title}")
    else:
        print(f"[afternoon_check] Notification FAILED to send: {title}")