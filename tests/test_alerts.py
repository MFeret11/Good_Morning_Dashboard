from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from app.alerts import get_alerts


def mock_response(json_data):
    mock = MagicMock()
    mock.json.return_value = json_data
    return mock


def make_alert(route_name, **overrides):
    base = {
        "route": "MED",
        "route_id": "rr_route_med",
        "route_name": route_name,
        "isadvisory": "No",
        "isdetour": "N",
        "isalert": "N",
        "issuppend": "N",
        "iselevator": "N",
        "issuspended": "N",
        "isstrike": "N",
        "ismodifiedservice": "N",
        "isdelays": "N",
        "isdiversion": "N",
        "last_updated": "Jan 1 2020  9:00AM",
        "alert": "",
        "advisory": "",
    }
    base.update(overrides)
    return base


class TestAlertFiltering:
    @patch("app.alerts.requests.get")
    def test_ignores_irrelevant_lines(self, mock_get):
        mock_get.return_value = mock_response([
            make_alert("Some Other Line", isdelays="Y"),
        ])
        result = get_alerts()
        assert result["has_critical_alerts"] is False

    @patch("app.alerts.requests.get")
    def test_clean_lines_produce_no_alerts(self, mock_get):
        mock_get.return_value = mock_response([
            make_alert("Media/Wawa"),
            make_alert("Manayunk/Norristown"),
        ])
        result = get_alerts()
        assert result["has_critical_alerts"] is False
        assert result["has_recent_advisories"] is False


class TestCriticalAlerts:
    @patch("app.alerts.requests.get")
    def test_delay_flag_produces_critical_alert(self, mock_get):
        recent_date = datetime.now().strftime("%b %d %Y %I:%M%p")
        mock_get.return_value = mock_response([
            make_alert("Media/Wawa", isdelays="Y", last_updated=recent_date),
        ])
        result = get_alerts()
        assert result["has_critical_alerts"] is True
        assert result["critical_alerts"][0]["flags"]["delays"] is True

    @patch("app.alerts.requests.get")
    def test_suspended_flag_produces_critical_alert(self, mock_get):
        recent_date = datetime.now().strftime("%b %d %Y %I:%M%p")
        mock_get.return_value = mock_response([
            make_alert("Manayunk/Norristown", issuspended="Y", last_updated=recent_date),
        ])
        result = get_alerts()
        assert result["has_critical_alerts"] is True
        assert result["critical_alerts"][0]["flags"]["suspended"] is True

    @patch("app.alerts.requests.get")
    def test_stale_critical_flag_is_excluded(self, mock_get):
        # Regression guard: critical_alerts used to have no recency check at
        # all, so a stuck/stale flag would show forever. This confirms it's
        # correctly filtered now, same as advisories already were.
        mock_get.return_value = mock_response([
            make_alert("Media/Wawa", isalert="Y", last_updated="Jan 1 2020  9:00AM"),
        ])
        result = get_alerts()
        assert result["has_critical_alerts"] is False


class TestAdvisories:
    @patch("app.alerts.requests.get")
    def test_stale_advisory_is_excluded(self, mock_get):
        # Old advisory - should NOT show up even though isadvisory is Yes
        mock_get.return_value = mock_response([
            make_alert("Manayunk/Norristown", isadvisory="Yes",
                       advisory="Old standing notice", last_updated="Jan 1 2020  9:00AM"),
        ])
        result = get_alerts()
        assert result["has_recent_advisories"] is False

    @patch("app.alerts.requests.get")
    def test_recent_advisory_is_included(self, mock_get):
        recent_date = datetime.now().strftime("%b %d %Y %I:%M%p")
        mock_get.return_value = mock_response([
            make_alert("Manayunk/Norristown", isadvisory="Yes",
                       advisory="Fresh notice", last_updated=recent_date),
        ])
        result = get_alerts()
        assert result["has_recent_advisories"] is True
        assert result["recent_advisories"][0]["advisory_text"] == "Fresh notice"

    @patch("app.alerts.requests.get")
    def test_critical_alert_takes_precedence_over_advisory(self, mock_get):
        # An entry with both a critical flag AND an advisory flag should
        # land in critical_alerts, not standing_advisories.
        recent_date = datetime.now().strftime("%b %d %Y %I:%M%p")
        mock_get.return_value = mock_response([
            make_alert("Media/Wawa", isdelays="Y", isadvisory="Yes",
                       last_updated=recent_date),
        ])
        result = get_alerts()
        assert result["has_critical_alerts"] is True
        assert result["has_recent_advisories"] is False
