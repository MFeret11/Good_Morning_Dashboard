"""Builds and sends the daily afternoon commute notification with
live platform track assignments, detailed alert text, and stall alerts."""
import html
import logging
import re

from app.commute import get_commute
from app.alerts import get_alerts
from app.config import (
    DEFAULT_WORK_STATION, DEFAULT_HOME_STATION, DEFAULT_TRANSFER_STATION,
    AFTERNOON_TARGET_DEPARTURE_TIME,
)
from app.notifications import send_notification

log = logging.getLogger(__name__)

CHECK_FAILED_MESSAGE = (
    "Commute check failed",
    "The commute check hit an error and couldn't read train data. Check SEPTA manually.",
    "high",
    ["warning"],
)


def _clean_alert_text(text: str) -> str:
    if not text:
        return ""
    clean = html.unescape(str(text))
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _late_suffix(minutes) -> str:
    return f" ({minutes} min late)" if minutes else ""


def _trip_summary(commute: dict) -> str:
    return (
        f"Train {commute.get('origin_train')} departs {commute.get('origin_actual_departure_time')}"
        f"{_late_suffix(commute.get('origin_delay_minutes', 0))}, arriving "
        f"{commute.get('actual_arrival_time')}{_late_suffix(commute.get('total_delay_minutes', 0))}."
    )


def _data_quality_notes(commute: dict, alerts: dict) -> list[str]:
    """Caveats appended to every message, so a degraded upstream never reads
    as a clean bill of health."""
    notes = []
    if commute.get("data_stale"):
        notes.append("SEPTA didn't respond just now; train times are from a few minutes ago.")
    if alerts.get("alerts_unavailable"):
        notes.append("SEPTA service alerts couldn't be checked.")
    elif alerts.get("alerts_stale"):
        notes.append("SEPTA service alerts are from an earlier check.")
    return notes


def _build_core_message(commute: dict, alerts: dict) -> tuple[str, str, str, list[str]]:
    # CRITICAL SERVICE ALERTS (WITH DETAILED LIVE REASON), plus the train to take
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

        alert_lines.append(f"Your train: {_trip_summary(commute)}")
        return (
            "SEPTA service alert",
            "\n\n".join(alert_lines),
            "urgent",
            ["rotating_light"],
        )

    if commute.get("is_cancelled") or commute.get("connection_cancelled"):
        return (
            "TRAIN CANCELLED",
            f"Train {commute.get('origin_train')} (or connection) is CANCELLED today. "
            f"Next backup option departs at {commute.get('backup_train_departure') or 'check SEPTA'}.",
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

    # SEPTA sent delay text we can't read as minutes; don't guess "on time".
    if commute.get("origin_delay_unknown") or commute.get("connection_delay_unknown"):
        if commute.get("origin_delay_unknown"):
            train, status = commute.get("origin_train"), commute.get("origin_delay_text")
        else:
            train, status = commute.get("connection_train"), commute.get("connection_delay_text")
        return (
            "Train status unclear",
            f"SEPTA lists train {train} as \"{status}\" instead of a delay estimate. "
            f"Check SEPTA before heading out.",
            "high",
            ["warning"],
        )

    if commute.get("missed_connection"):
        return (
            "Missed Connection Alert",
            f"Train {commute.get('origin_train')} reaches {commute.get('connection_station')} too late "
            f"for the planned connection. Next connection is train {commute.get('connection_train')} at "
            f"{commute.get('connection_actual_departure_time')}, arriving {commute.get('actual_arrival_time')} "
            f"(planned {commute.get('planned_arrival_time')}).",
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
            "Connecting Train Delayed",
            f"Train {commute.get('connection_train')}{track_info} at {commute.get('connection_station')} is running "
            f"{commute.get('connection_delay_minutes')} min late. Expected arrival: {commute.get('actual_arrival_time')}. "
            f"Wait in the office if you prefer.",
            "default",
            ["hourglass"],
        )

    if commute.get("delayed"):
        return (
            "Running behind today",
            f"Train {commute.get('origin_train')} departs {commute.get('origin_actual_departure_time')}, "
            f"running {commute.get('total_delay_minutes', 0)} min behind overall. "
            f"Expected arrival: {commute.get('actual_arrival_time')} (scheduled {commute.get('arrival_time')}). "
            f"Might be worth waiting it out if you're comfortable where you are.",
            "default",
            ["yellow_circle"],
        )

    orig_track = commute.get('origin_track')
    track_str = f" · Track {orig_track}" if orig_track and orig_track != "TBD" else ""
    origin_delay = commute.get("origin_delay_minutes", 0)
    total_delay = commute.get("total_delay_minutes", 0)

    if not origin_delay and not total_delay:
        return (
            "Commute looks good",
            f"Train {commute.get('origin_train')}{track_str} departs {commute.get('origin_actual_departure_time')}, "
            f"on time, arriving {commute.get('actual_arrival_time')}.",
            "default",
            ["white_check_mark"],
        )

    return (
        "Minor delay today",
        f"Train {commute.get('origin_train')}{track_str} departs {commute.get('origin_actual_departure_time')}"
        f"{_late_suffix(origin_delay)}, arriving {commute.get('actual_arrival_time')}"
        f"{_late_suffix(total_delay)}.",
        "default",
        ["yellow_circle"],
    )


def build_afternoon_message(commute: dict, alerts: dict) -> tuple[str, str, str, list[str]]:
    if "error" in commute:
        return (
            "Commute check failed",
            f"Could not retrieve train data: {commute.get('error', 'Unknown error')}. Check SEPTA manually.",
            "high",
            ["warning"],
        )

    title, message, priority, tags = _build_core_message(commute, alerts)
    notes = _data_quality_notes(commute, alerts)
    if notes:
        message = "\n\n".join([message, *notes])
    return title, message, priority, tags


def run_afternoon_check():
    # Never let a crash end in silence: no push reads as "all good".
    try:
        commute = get_commute(
            DEFAULT_WORK_STATION,
            DEFAULT_HOME_STATION,
            target_time=AFTERNOON_TARGET_DEPARTURE_TIME,
            preferred_transfer=DEFAULT_TRANSFER_STATION,
        )
        alerts = get_alerts()
        title, message, priority, tags = build_afternoon_message(commute, alerts)
    except Exception:
        log.exception("Afternoon commute check crashed")
        title, message, priority, tags = CHECK_FAILED_MESSAGE

    success = send_notification(title, message, priority=priority, tags=tags)

    if success:
        print(f"[afternoon_check] Notification sent: {title}")
    else:
        print(f"[afternoon_check] Notification FAILED to send: {title}")
