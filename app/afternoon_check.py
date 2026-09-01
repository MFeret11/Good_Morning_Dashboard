"""Builds and sends the daily afternoon commute notification with
live platform track assignments, delay warnings, and cancellation alerts."""
from app.commute import get_commute
from app.alerts import get_alerts
from app.config import (
    DEFAULT_WORK_STATION, DEFAULT_HOME_STATION, DEFAULT_TRANSFER_STATION,
    AFTERNOON_TARGET_DEPARTURE_TIME,
)
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

    if commute.get("is_cancelled") or commute.get("connection_cancelled"):
        return (
            "❌ TRAIN CANCELLED",
            f"Train {commute['origin_train']} (or connection) is CANCELLED today. "
            f"Next backup option departs at {commute.get('backup_train_departure', 'check SEPTA')}.",
            "urgent",
            ["x"],
        )

    if commute.get("missed_connection"):
        return (
            "🚨 Missed Connection Alert",
            f"Train {commute['origin_train']} reaches {commute.get('connection_station')} too late for the regular Media train. "
            f"Re-planned arrival at Media: {commute['actual_arrival_time']}.",
            "urgent",
            ["warning"],
        )

    if commute.get("at_risk"):
        track_info = f" (Track {commute['connection_track']})" if commute.get("connection_track") != "TBD" else ""
        return (
            "⚠️ Tight Connection",
            f"Train {commute['origin_train']} departs East Falls at {commute['origin_actual_departure_time']} "
            f"(+{commute['origin_delay_minutes']}m). Only ~{commute['transfer_buffer_minutes']} min to make connection at {commute.get('connection_station')}{track_info}!",
            "high",
            ["warning"],
        )

    if commute.get("connection_delay_minutes", 0) >= 10:
        track_info = f" on Track {commute['connection_track']}" if commute.get("connection_track") != "TBD" else ""
        return (
            "🟡 Connecting Media Train Delayed",
            f"Train {commute.get('connection_train')}{track_info} at Jefferson is running "
            f"{commute['connection_delay_minutes']} min late. Expected arrival: {commute['actual_arrival_time']}. Wait in office if preferred.",
            "default",
            ["hourglass"],
        )

    if commute.get("delayed"):
        return (
            "🟡 Commute Delayed",
            f"Departing East Falls at {commute['origin_actual_departure_time']}. "
            f"Expected home at {commute['actual_arrival_time']} (+{commute['total_delay_minutes']} min).",
            "default",
            ["yellow_circle"],
        )

    track_str = f" · Track {commute['origin_track']}" if commute.get("origin_track") != "TBD" else ""
    return (
        "🟢 Commute Looks Great",
        f"Train {commute['origin_train']}{track_str} departs East Falls at {commute['origin_actual_departure_time']}. "
        f"Clean ~{commute['transfer_buffer_minutes']} min transfer at Jefferson. Home by {commute['actual_arrival_time']}.",
        "default",
        ["white_check_mark"],
    )


def run_afternoon_check():
    commute = get_commute(
        DEFAULT_WORK_STATION,
        DEFAULT_HOME_STATION,
        target_time=AFTERNOON_TARGET_DEPARTURE_TIME,
        preferred_transfer=DEFAULT_TRANSFER_STATION,
    )
    alerts = get_alerts()

    title, message, priority, tags = build_afternoon_message(commute, alerts)
    success = send_notification(title, message, priority=priority, tags=tags)

    if success:
        print(f"[afternoon_check] Notification sent: {title}")
    else:
        print(f"[afternoon_check] Notification FAILED to send: {title}")