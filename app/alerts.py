"""SEPTA service alerts, filtered to the two relevant lines and split into
real-time critical alerts vs. recent (non-stale) standing advisories."""
import logging
import time

import requests

from app.config import SEPTA_BASE, RELEVANT_LINES, ADVISORY_RECENCY_DAYS
from app.time_utils import is_recent

log = logging.getLogger(__name__)

_CACHE_TTL = 300  # seconds
_cache = {"data": None, "fetched_at": 0.0}


def _empty(unavailable: bool = False) -> dict:
    return {
        "has_critical_alerts": False,
        "critical_alerts": [],
        "has_recent_advisories": False,
        "recent_advisories": [],
        "alerts_unavailable": unavailable,
    }


def _fetch_alerts_raw():
    resp = requests.get(f"{SEPTA_BASE}/Alerts/index.php", timeout=10)
    resp.raise_for_status()
    return resp.json()


def _fallback() -> dict:
    """Serve the last good result (marked stale) if we have one, else empty."""
    if _cache["data"] is not None:
        return {**_cache["data"], "alerts_stale": True}
    return _empty(unavailable=True)


def get_alerts() -> dict:
    now = time.time()
    if _cache["data"] is not None and now - _cache["fetched_at"] < _CACHE_TTL:
        return _cache["data"]

    try:
        response = _fetch_alerts_raw()
    except (requests.RequestException, ValueError) as e:
        log.warning("SEPTA alerts unavailable: %s", e)
        return _fallback()

    if not isinstance(response, list):
        log.warning("SEPTA alerts: unexpected payload type %s", type(response).__name__)
        return _fallback()

    relevant = [r for r in response if r.get("route_name") in RELEVANT_LINES]

    critical_alerts = []
    standing_advisories = []

    for r in relevant:
        flags = {
            "delays": r.get("isdelays") == "Y",
            "suspended": r.get("issuspended") == "Y",
            "detour": r.get("isdetour") == "Y",
            "diversion": r.get("isdiversion") == "Y",
            "modified_service": r.get("ismodifiedservice") == "Y",
            "alert": r.get("isalert") == "Y",
        }
        is_advisory = r.get("isadvisory") == "Yes"
        recent = is_recent(r.get("last_updated", ""), days=ADVISORY_RECENCY_DAYS)

        if any(flags.values()) and recent:
            critical_alerts.append({
                "line": r.get("route_name"),
                "flags": flags,
                "alert_text": r.get("alert"),
                "last_updated": r.get("last_updated"),
            })
        elif is_advisory and recent:
            standing_advisories.append({
                "line": r.get("route_name"),
                "advisory_text": r.get("advisory"),
                "last_updated": r.get("last_updated"),
            })

    result = {
        "has_critical_alerts": len(critical_alerts) > 0,
        "critical_alerts": critical_alerts,
        "has_recent_advisories": len(standing_advisories) > 0,
        "recent_advisories": standing_advisories,
        "alerts_unavailable": False,
    }
    _cache["data"] = result
    _cache["fetched_at"] = now
    return result