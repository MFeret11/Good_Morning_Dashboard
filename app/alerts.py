"""SEPTA service alerts, filtered to the two relevant lines and split into
real-time critical alerts vs. recent (non-stale) standing advisories."""
import requests

from app.config import SEPTA_BASE, RELEVANT_LINES, ADVISORY_RECENCY_DAYS
from app.time_utils import is_recent


def get_alerts() -> dict:
    response = requests.get(f"{SEPTA_BASE}/Alerts/index.php").json()
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

        if any(flags.values()):
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

    return {
        "has_critical_alerts": len(critical_alerts) > 0,
        "critical_alerts": critical_alerts,
        "has_recent_advisories": len(standing_advisories) > 0,
        "recent_advisories": standing_advisories,
    }
