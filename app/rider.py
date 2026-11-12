"""Where the rider is, and which train they are on.

Home Assistant posts the rider's GPS fix to /api/location. Each new fix is
compared with SEPTA's TrainView positions; when two fixes in a row sit next to
the same train and the rider has moved between them, the rider is taken to be
on that train. A rider moving at train speed with no located train alongside
is flagged as riding something TrainView can't see (a train whose tracker is
off). The fix is held in memory only and is never logged or returned by the
API."""
import math
import time

from app.commute import _get_trainview_feed, has_live_position

# A phone fix and a TrainView position can each be a minute or two old, and a
# train covers over a kilometre a minute, so "next to the train" is generous.
# Two matching fixes in a row keep that from producing false matches.
ON_TRAIN_RADIUS_M = 2500
MIN_MOVE_M = 300              # standing on a platform next to a train isn't riding it
FIX_MAX_AGE_SECONDS = 600     # older than this, the position says nothing about now
OPPOSITE_DEGREES = 90         # a train heading this far off the rider's own course is passing, not carrying them
RIDING_SPEED_MPS = 7          # faster than anyone runs
PAUSED_SPEED_MPS = 2          # slower than this is a station stop, or walking

_state: dict = {"fix": None, "votes": [], "train": None, "untracked": False}


def reset() -> None:
    _state.update({"fix": None, "votes": [], "train": None, "untracked": False})


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    return math.degrees(math.atan2(
        math.sin(dl) * math.cos(p2),
        math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360


def _heads_the_other_way(train: dict, course: float | None) -> bool:
    try:
        heading = float(train.get("heading"))
    except (TypeError, ValueError):
        return False  # no heading reported: can't rule it out
    return course is not None and abs((heading - course + 180) % 360 - 180) > OPPOSITE_DEGREES


def _nearest_train(fix: dict, course: float | None = None) -> str | None:
    """The closest located train, leaving out any heading away from `course`
    (the rider's own direction of travel, when they have moved)."""
    best, best_distance = None, ON_TRAIN_RADIUS_M + (fix.get("accuracy") or 0)
    for train in _get_trainview_feed():
        if not has_live_position(train) or _heads_the_other_way(train, course):
            continue
        try:
            distance = _distance_m(fix["lat"], fix["lon"], float(train["lat"]), float(train["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if distance <= best_distance and train.get("trainno"):
            best, best_distance = str(train["trainno"]), distance
    return best


def update_fix(lat: float, lon: float, accuracy: float | None = None,
               seen_at: float | None = None, zone: str | None = None) -> None:
    """Stores a new fix and re-evaluates which train the rider is on.
    `seen_at` is when the phone reported it (epoch seconds); `zone` is Home
    Assistant's name for where the rider is ('home', 'not_home', a zone)."""
    now = time.time()
    fix = {"lat": lat, "lon": lon, "accuracy": accuracy, "seen_at": seen_at or now, "zone": zone}
    previous = _state["fix"]
    _state["fix"] = fix

    if zone == "home":
        _state.update({"votes": [], "train": None, "untracked": False})
        return
    if previous and (previous["lat"], previous["lon"], previous["seen_at"]) == (lat, lon, fix["seen_at"]):
        return  # same fix sent again

    step_m, speed = 0.0, 0.0
    elapsed = fix["seen_at"] - previous["seen_at"] if previous else None
    if previous and 0 < elapsed <= FIX_MAX_AGE_SECONDS:
        step_m = _distance_m(previous["lat"], previous["lon"], lat, lon)
        speed = step_m / elapsed
    moved = step_m >= MIN_MOVE_M
    # Below the accuracy of the fixes themselves a direction means nothing.
    course = _bearing(previous["lat"], previous["lon"], lat, lon) if step_m >= 150 else None
    votes = (_state["votes"] + [(_nearest_train(fix, course), moved, speed)])[-3:]
    _state["votes"] = votes

    # Two fixes in a row with no train alongside, covering ground faster than
    # anyone on foot: riding something TrainView has no position for. It stays
    # set through station stops and passing trains, and ends after three slow
    # fixes in a row or once a located train is confirmed.
    last_two = votes[-2:]
    if len(last_two) == 2 and all(v[0] is None and v[2] >= PAUSED_SPEED_MPS for v in last_two) \
            and max(v[2] for v in last_two) >= RIDING_SPEED_MPS:
        _state["untracked"] = True
    elif len(votes) == 3 and all(v[2] < PAUSED_SPEED_MPS for v in votes):
        _state["untracked"] = False

    latest = votes[-1][0]
    earlier = votes[-2][0] if len(votes) >= 2 else None
    if latest and latest == earlier and votes[-1][1]:
        _state["train"] = latest                      # two fixes in a row, moving with it
        _state["untracked"] = False
    elif _state["train"] and latest != _state["train"]:
        if not (latest is None and earlier == _state["train"]):
            _state["train"] = None                    # one missed fix is forgiven, two aren't


def current() -> dict | None:
    """What is known about the rider right now, or None if no fix was ever posted."""
    fix = _state["fix"]
    if fix is None:
        return None
    age = max(0.0, time.time() - fix["seen_at"])
    fresh = age <= FIX_MAX_AGE_SECONDS
    return {
        "fresh": fresh,
        "age_minutes": round(age / 60),
        "home": fresh and fix["zone"] == "home",
        "on_train": _state["train"] if fresh else None,
        "riding_untracked": fresh and _state["untracked"] and not _state["train"],
    }
