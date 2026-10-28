"""Builds and sends the afternoon commute notification, then keeps checking
until the train leaves and pushes again only if things get worse."""
import logging
from datetime import datetime, timedelta

from app.commute import get_commute, get_connection_update
from app.alerts import get_alerts, alert_summary
from app.weather import get_weather
from app.config import (
    DEFAULT_WORK_STATION, DEFAULT_HOME_STATION, DEFAULT_TRANSFER_STATION,
    AFTERNOON_TARGET_DEPARTURE_TIME, SIGNIFICANT_DELAY_MINUTES, AFTERNOON_END,
)
from app.notifications import send_notification
from app.time_utils import parse_time, anchor_to_today

log = logging.getLogger(__name__)

CHECK_FAILED_MESSAGE = (
    "Commute check failed",
    "The commute check hit an error and couldn't read train data. Check SEPTA manually.",
    "high",
    ["warning"],
)

FOLLOW_UP_PAUSED_MESSAGE = (
    "Commute updates paused",
    "Couldn't reach SEPTA for the last two checks, so you won't hear about new "
    "delays or cancellations. Check SEPTA before you leave.",
    "default",
    ["warning"],
)

# How bad each push is, keyed by title. A follow-up is sent only when the
# situation ranks higher than the last push, or the delay has grown by
# FOLLOW_UP_DELAY_STEP_MINUTES at the same rank.
SEVERITY = {
    "Commute check failed": -1,
    "Commute looks good": 0,
    "Late but still connecting": 0,  # first train late, arrival unchanged
    "Minor delay today": 1,
    "Running behind today": 2,
    "Connecting Train Delayed": 2,
    "Tight connection today": 3,
    "Train status unclear": 3,
    "Missed Connection Alert": 4,
    "Train Stalled on Track": 4,
    "Connection cancelled": 5,
    "TRAIN CANCELLED": 5,
    "SEPTA service alert": 6,
}
SERIOUS_SEVERITY = 4  # stalled, missed connection and worse
FOLLOW_UP_DELAY_STEP_MINUTES = 10
FOLLOW_UP_FAILURES_BEFORE_NOTICE = 2

# What the last push said, so follow-ups know what the rider already knows.
_follow_up = {}


def _now() -> datetime:
    """Wall-clock time; a separate function so tests can freeze it."""
    return datetime.now()


def _check_return_commute() -> tuple[dict, dict]:
    commute = get_commute(
        DEFAULT_WORK_STATION,
        DEFAULT_HOME_STATION,
        target_time=AFTERNOON_TARGET_DEPARTURE_TIME,
        preferred_transfer=DEFAULT_TRANSFER_STATION,
    )
    return commute, get_alerts()


def _weather_note() -> str | None:
    """One line about rain or snow on the way home. Weather is a nice-to-have,
    so any failure here just leaves the line out."""
    try:
        warning = get_weather().get("evening_warning")
    except Exception:
        log.exception("Weather lookup for the afternoon push failed")
        return None
    return f"Weather: {warning} on the way home." if warning else None


def _late_suffix(minutes) -> str:
    return f" ({minutes} min late)" if minutes else ""


def _trip_summary(commute: dict) -> str:
    return (
        f"Train {commute.get('origin_train')} departs {commute.get('origin_actual_departure_time')}"
        f"{_late_suffix(commute.get('origin_delay_minutes', 0))}, arriving "
        f"{commute.get('actual_arrival_time')}{_late_suffix(commute.get('total_delay_minutes', 0))}."
    )


def _transfer_notes(commute: dict) -> list[str]:
    """Says so when the plan changes trains somewhere other than the usual
    station, because no connection works there right now."""
    if not commute.get("transfer_fallback"):
        return []
    usual, station = commute.get("preferred_transfer_station"), commute.get("connection_station")
    return [f"No connection works at {usual} right now, so change trains at {station} instead."]


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
        alert_lines = [alert_summary(a) for a in alerts["critical_alerts"]]
        alert_lines.append(f"Your train: {_trip_summary(commute)}")
        return (
            "SEPTA service alert",
            "\n\n".join(alert_lines),
            "urgent",
            ["rotating_light"],
        )

    if commute.get("is_cancelled") or commute.get("connection_cancelled"):
        if commute.get("is_cancelled"):
            what = f"Train {commute.get('origin_train')} is CANCELLED today."
        else:
            what = (f"Connecting train {commute.get('connection_train')} at "
                    f"{commute.get('connection_station')} is CANCELLED today.")
        backup = commute.get("backup_train_departure")
        next_step = (f"The next train from your station leaves at {backup}." if backup
                     else "Check SEPTA for the next train.")
        return ("TRAIN CANCELLED", f"{what} {next_step}", "urgent", ["x"])

    if commute.get("planned_connection_cancelled"):
        return (
            "Connection cancelled",
            f"Your usual connection, train {commute.get('planned_connection_train')} at "
            f"{commute.get('connection_station')}, is CANCELLED. Take train {commute.get('origin_train')} "
            f"at {commute.get('origin_actual_departure_time')}, then train {commute.get('connection_train')} "
            f"at {commute.get('connection_actual_departure_time')}, arriving {commute.get('actual_arrival_time')} "
            f"(planned {commute.get('planned_arrival_time')}).",
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
        buffer = round(commute.get("transfer_buffer_minutes", 0))
        station = commute.get("connection_station", "the transfer point")
        if buffer < 0:
            squeeze = (f"It reaches {station} about {-buffer} min after connecting train "
                       f"{commute.get('connection_train')}{track_info} is due to leave "
                       f"({commute.get('connection_actual_departure_time')}). You may still make it, "
                       f"but it's tight.")
        else:
            squeeze = f"Only ~{buffer} min to make your connection at {station}{track_info}."
        return (
            "Tight connection today",
            f"Train {commute.get('origin_train')} departs {commute.get('origin_actual_departure_time')}"
            f"{_late_suffix(commute.get('origin_delay_minutes', 0))}. {squeeze}",
            "high",
            ["warning"],
        )

    if commute.get("connection_delay_minutes", 0) >= SIGNIFICANT_DELAY_MINUTES:
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

    if not total_delay and origin_delay:
        # The first train is late but still makes the planned connection, so
        # the rider gets home as planned. Its own title ranks with "looks
        # good", so a follow-up doesn't buzz for it, and the message still
        # says the train is late (never "on time").
        return (
            "Late but still connecting",
            f"Train {commute.get('origin_train')}{track_str} departs {commute.get('origin_actual_departure_time')}"
            f"{_late_suffix(origin_delay)} but still makes the connection, arriving "
            f"{commute.get('actual_arrival_time')} as planned.",
            "default",
            ["white_check_mark"],
        )

    if not total_delay:
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
    notes = [*_transfer_notes(commute), *_data_quality_notes(commute, alerts)]
    if notes:
        message = "\n\n".join([message, *notes])
    return title, message, priority, tags


def _send(title: str, message: str, priority: str, tags: list[str]) -> None:
    if send_notification(title, message, priority=priority, tags=tags):
        print(f"[afternoon_check] Notification sent: {title}")
    else:
        print(f"[afternoon_check] Notification FAILED to send: {title}")


def _window_end(now: datetime) -> datetime:
    return now.replace(hour=AFTERNOON_END, minute=0, second=0, microsecond=0)


def _at_today(now: datetime, commute: dict, key: str) -> datetime | None:
    try:
        return anchor_to_today(now, parse_time(commute[key]))
    except (KeyError, TypeError, ValueError):
        return None


def _follow_up_cutoff(commute: dict, now: datetime) -> datetime:
    """When the first train leaves the rider's station. After that SEPTA's
    NextToArrive lists the next train instead, so a transfer trip switches
    to following the connection (_ride_cutoff) and a direct trip stops."""
    departure = _at_today(now, commute, "origin_actual_departure_time")
    return min(departure, _window_end(now)) if departure else _window_end(now)


def _ride_cutoff(commute: dict, now: datetime) -> datetime:
    """While the rider is on the first train, follow-ups run until the
    connecting train leaves the transfer station."""
    departure = _at_today(now, commute, "connection_actual_departure_time")
    return min(departure, _window_end(now)) if departure else _window_end(now)


def _is_transfer(commute: dict | None) -> bool:
    return bool(commute) and commute.get("trip_type") == "transfer" and bool(commute.get("connection_station"))


def _remember(title: str, commute: dict | None, now: datetime) -> None:
    ok = commute is not None and "error" not in commute
    _follow_up.update({
        "date": now.date(),
        "severity": SEVERITY.get(title, 2),
        "delay": (commute or {}).get("total_delay_minutes", 0) if ok else 0,
        "train": commute.get("origin_train") if ok else None,
        "cutoff": _follow_up_cutoff(commute, now) if ok else _follow_up_cutoff({}, now),
        # The latest plan for a transfer trip, used to follow the connection
        # once the first train has left.
        "trip": commute if ok and _is_transfer(commute) else None,
        "station": commute.get("connection_station") if ok else None,
        "ride_cutoff": _ride_cutoff(commute, now) if ok and _is_transfer(commute) else None,
        "failures": 0,
        "paused_notice_sent": False,
        "sent_titles": {title},
    })


def run_afternoon_check():
    """The scheduled afternoon push. Never let a crash end in silence: no push
    reads as 'all good'."""
    now = _now()
    commute = None
    try:
        commute, alerts = _check_return_commute()
        title, message, priority, tags = build_afternoon_message(commute, alerts)
        if "error" not in commute:
            note = _weather_note()
            if note:
                message = f"{message}\n\n{note}"
    except Exception:
        log.exception("Afternoon commute check crashed")
        title, message, priority, tags = CHECK_FAILED_MESSAGE

    _send(title, message, priority, tags)
    _remember(title, commute, now)


def _check_connection(trip: dict, now: datetime) -> tuple[dict, dict]:
    """The rider is on the first train: re-plan the connection from its live
    delay. If TrainView has dropped the train after it should have reached the
    transfer station (some trains end there), the last known delay is used,
    because the connection is what matters by then."""
    fallback = None
    arrival = _at_today(now, trip, "origin_arrival_time")
    last_late = trip.get("origin_delay_minutes") or 0
    if arrival is not None and now >= arrival + timedelta(minutes=last_late):
        fallback = last_late
    return get_connection_update(trip, DEFAULT_HOME_STATION, fallback_late=fallback), get_alerts()


def run_follow_up_check():
    """Runs every few minutes after the afternoon push. Before the first
    train leaves it re-checks the whole trip; on a transfer trip it then keeps
    following the connection until that train leaves too. Pushes again only
    if things got worse than the last push said."""
    now = _now()
    state = _follow_up
    if state.get("date") != now.date():
        return
    riding = now >= state["cutoff"]
    if riding and (state.get("trip") is None or now >= state["ride_cutoff"]):
        return

    try:
        if riding:
            commute, alerts = _check_connection(state["trip"], now)
        else:
            commute, alerts = _check_return_commute()
        if "error" in commute:
            raise RuntimeError(commute["error"])
    except Exception as e:
        log.warning("Follow-up commute check failed: %s", e)
        state["failures"] += 1
        if (state["failures"] >= FOLLOW_UP_FAILURES_BEFORE_NOTICE
                and not state["paused_notice_sent"] and state["severity"] >= 0):
            state["paused_notice_sent"] = True
            _send(*FOLLOW_UP_PAUSED_MESSAGE)
        return
    state["failures"] = 0

    if not riding and state["train"] is not None and commute.get("origin_train") != state["train"]:
        # The rider's train has left (SEPTA now lists the next one). A
        # transfer trip goes on to follow the connection right away.
        state["cutoff"] = now
        if state.get("trip") is not None:
            run_follow_up_check()
        return

    if _is_transfer(commute):
        state["trip"] = commute
        state["ride_cutoff"] = _ride_cutoff(commute, now)

    title, message, priority, tags = build_afternoon_message(commute, alerts)
    severity = SEVERITY.get(title, 2)
    delay = commute.get("total_delay_minutes", 0)
    station = commute.get("connection_station")
    moved = (not riding and state.get("station") is not None and station is not None
             and station != state["station"])
    worse = (
        moved  # the rider needs to change trains somewhere else now
        or severity > state["severity"]
        # A serious problem the rider hasn't heard about yet, e.g. a
        # cancellation after a service-alert push. Each is sent once.
        or (severity >= SERIOUS_SEVERITY and title not in state["sent_titles"])
        or (severity == state["severity"] and severity > 0
            and delay >= state["delay"] + FOLLOW_UP_DELAY_STEP_MINUTES)
    )
    if not worse:
        return

    if moved and station not in message:
        message = f"{message}\n\nChange trains at {station}."
    _send(f"Update: {title}", message, priority, tags)
    state["sent_titles"].add(title)
    state.update({
        "severity": max(severity, state["severity"]),
        "delay": delay,
        "paused_notice_sent": False,
        "station": station or state.get("station"),
    })
    if not riding:
        state.update({"train": commute.get("origin_train"), "cutoff": _follow_up_cutoff(commute, now)})
