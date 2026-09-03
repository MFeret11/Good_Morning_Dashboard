"""Builds and sends the daily afternoon commute notification with
live platform track assignments, detailed alert text, and stall alerts."""
import re
import html
from app.commute import get_commute
from app.alerts import get_alerts
from app.config import (
    DEFAULT_WORK_STATION, DEFAULT_HOME_STATION, DEFAULT_TRANSFER_STATION,
    AFTERNOON_TARGET_DEPARTURE_TIME,
)
from app.notifications import send_notification


def _clean_alert_text(text: str) -> str:
    if not text:
        return ""
    clean = html.unescape(str(text))
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def build_afternoon_message(commute: dict, alerts: dict) -> tuple[str, str, str, list[str]]:
    if "error" in commute:
        return (
            "Commute check failed",
            f"Could not retrieve train data: {commute.get('error', 'Unknown error')}. Check SEPTA manually.",
            "high",
            ["warning"],
        )

    # CRITICAL SERVICE ALERTS (WITH DETAILED LIVE REASON)
    if alerts.get("has_critical_alerts"):
        alert_lines = []
        for a in alerts["critical_alerts"]:
            line = a.get("line", "Regional Rail")
            clean_text = _clean_alert_text(a.get("alert_text", ""))

            if not clean_text:
                active_flags = [k.replace("_", " ") for k, v in a.get("flags", {}).items() if v]
                flag_summary = ", ".join(active_flags) if active_flags else "Service disruption"
                clean_text = f"Reported {flag_summary}"

            alert_lines.append(f"{line}: {clean_text}")

        message_body = "\n\n".join(alert_lines)
        return (
            "SEPTA service alert",
            message_body,
            "urgent",
            ["rotating_light"],
        )

    if commute.get("is_cancelled") or commute.get("connection_cancelled"):
        return (
            "TRAIN CANCELLED",
            f"Train {commute.get('origin_train')} (or connection) is CANCELLED today. "
            f"Next backup option departs at {commute.get('backup_train_departure', 'check SEPTA')}.",
            "urgent",
            ["x"],
        )

    # STALL DETECTOR ALERT
    if commute.get("is_stalled") or commute.get("connection_is_stalled"):
        stalled_train = commute.get('origin_train') if commute.get("is_stalled") else commute.get('connection_train')
        stalled_stop = commute.get('origin_current_stop') if commute.get("is_stalled") else commute.get('connection_current_stop')
        stalled_min = commute.get('stall_minutes') if commute.get("is_stalled") else commute.get('connection_stall_minutes')
        return (
            "Train Stalled on Track",
            f"Train {stalled_train} has been stopped at {stalled_stop} for ~{stalled_min} min. "
            f"Expect heavier delays than reported by SEPTA.",
            "high",
            ["warning"],
        )

    if commute.get("missed_connection"):
        return (
            "Missed Connection Alert",
            f"Train {commute.get('origin_train')} reaches {commute.get('connection_station')} too late for the regular Media train. "
            f"Re-planned arrival at Media: {commute.get('actual_arrival_time')}.",
            "urgent",
            ["warning"],
        )

    conn_track = commute.get('connection_track')
    track_info = f" (Track {conn_track})" if conn_track and conn_track != "TBD" else ""

    if commute.get("at_risk"):
        return (
            "Tight connection today",
            f"Train {commute.get('origin_train')} departs {commute.get('origin_actual_departure_time')}, "
            f"running {commute.get('origin_delay_minutes', 0)} min late - only ~"
            f"{round(commute.get('transfer_buffer_minutes', 0))} min to make your connection at "
            f"{commute.get('connection_station', 'the transfer point')}{track_info}.",
            "high",
            ["warning"],
        )

    if commute.get("connection_delay_minutes", 0) >= 10:
        return (
            "Connecting Media Train Delayed",
            f"Train {commute.get('connection_train')}{track_info} at Jefferson is running "
            f"{commute.get('connection_delay_minutes')} min late. Expected arrival: {commute.get('actual_arrival_time')}. Wait in office if preferred.",
            "default",
            ["hourglass"],
        )

    if commute.get("delayed"):
        return (
            "Running behind today",
            f"Train {commute.get('origin_train')} departed {commute.get('origin_actual_departure_time')}, "
            f"running {commute.get('total_delay_minutes', 0)} min behind overall. "
            f"Expected arrival: {commute.get('actual_arrival_time')} (scheduled {commute.get('arrival_time')}). "
            f"Might be worth waiting it out if you're comfortable where you are.",
            "default",
            ["yellow_circle"],
        )

    orig_track = commute.get('origin_track')
    track_str = f" · Track {orig_track}" if orig_track and orig_track != "TBD" else ""
    return (
        "Commute looks good",
        f"Train {commute.get('origin_train')}{track_str} departs {commute.get('origin_actual_departure_time')}, "
        f"on time, arriving {commute.get('actual_arrival_time')}.",
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