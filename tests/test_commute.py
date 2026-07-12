from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from app.commute import get_commute_leg


def mock_response(json_data):
    mock = MagicMock()
    mock.json.return_value = json_data
    return mock


# A realistic "transfer required" response, matching SEPTA's real shape
TRANSFER_RESPONSE = [
    {
        "orig_train": "3538",
        "orig_line": "Media/Wawa",
        "orig_departure_time": "2:03PM",
        "orig_arrival_time": "2:37PM",
        "orig_delay": "On time",
        "term_train": "4240",
        "term_line": "Manayunk/Norristown",
        "term_depart_time": "3:00PM",
        "term_arrival_time": "3:22PM",
        "Connection": "30th Street Station",
        "term_delay": "On time",
        "isdirect": "false",
    },
    {
        "orig_train": "3542",
        "orig_line": "Media/Wawa",
        "orig_departure_time": "3:03PM",
        "orig_arrival_time": "3:37PM",
        "orig_delay": "On time",
        "term_train": "4244",
        "term_line": "Manayunk/Norristown",
        "term_depart_time": "4:00PM",
        "term_arrival_time": "4:22PM",
        "Connection": "30th Street Station",
        "term_delay": "On time",
        "isdirect": "false",
    },
]

# A response containing one genuine direct/through trip
DIRECT_RESPONSE = [
    {
        "orig_train": "1001",
        "orig_line": "Media/Wawa",
        "orig_departure_time": "6:06AM",
        "orig_arrival_time": "6:50AM",
        "orig_delay": "On time",
        "isdirect": "true",
    },
    {
        "orig_train": "3538",
        "orig_line": "Media/Wawa",
        "orig_departure_time": "7:03AM",
        "orig_arrival_time": "7:37AM",
        "orig_delay": "On time",
        "term_train": "4240",
        "term_line": "Manayunk/Norristown",
        "term_depart_time": "8:00AM",
        "term_arrival_time": "8:22AM",
        "Connection": "30th Street Station",
        "term_delay": "On time",
        "isdirect": "false",
    },
]


class TestDirectVsTransferSelection:
    @patch("app.commute.requests.get")
    def test_prefers_direct_trip_when_available(self, mock_get):
        mock_get.return_value = mock_response(DIRECT_RESPONSE)
        result = get_commute_leg("Media", "East Falls")
        assert result["trip_type"] == "direct"
        assert result["origin_train"] == "1001"

    @patch("app.commute.requests.get")
    def test_falls_back_to_transfer_when_no_direct_exists(self, mock_get):
        mock_get.return_value = mock_response(TRANSFER_RESPONSE)
        result = get_commute_leg("Media", "East Falls")
        assert result["trip_type"] == "transfer"
        assert result["origin_train"] == "3538"
        assert result["connection_train"] == "4240"

    @patch("app.commute.requests.get")
    def test_empty_results_returns_error(self, mock_get):
        mock_get.return_value = mock_response([])
        result = get_commute_leg("Media", "East Falls")
        assert "error" in result


class TestDelayAwareTransferBuffer:
    @patch("app.commute.requests.get")
    def test_on_time_trip_has_full_buffer(self, mock_get):
        mock_get.return_value = mock_response(TRANSFER_RESPONSE)
        result = get_commute_leg("Media", "East Falls")
        # 2:37PM arrival -> 3:00PM departure = 23 min buffer
        assert result["transfer_buffer_minutes"] == 23.0
        assert result["at_risk"] is False

    @patch("app.commute.requests.get")
    def test_origin_delay_shrinks_transfer_buffer(self, mock_get):
        delayed_response = [dict(TRANSFER_RESPONSE[0])]
        delayed_response[0]["orig_delay"] = "20 mins"
        mock_get.return_value = mock_response(delayed_response)

        result = get_commute_leg("Media", "East Falls")
        # 23 min buffer - 20 min delay = 3 min remaining
        assert result["transfer_buffer_minutes"] == 3.0
        assert result["at_risk"] is True  # below RISK_BUFFER_MINUTES (5)

    @patch("app.commute.requests.get")
    def test_severe_origin_delay_flags_at_risk(self, mock_get):
        delayed_response = [dict(TRANSFER_RESPONSE[0])]
        delayed_response[0]["orig_delay"] = "30 mins"
        mock_get.return_value = mock_response(delayed_response)

        result = get_commute_leg("Media", "East Falls")
        assert result["at_risk"] is True


class TestTotalDelayCalculation:
    @patch("app.commute.requests.get")
    def test_no_delay_means_zero_total_delay(self, mock_get):
        mock_get.return_value = mock_response(TRANSFER_RESPONSE)
        result = get_commute_leg("Media", "East Falls")
        assert result["total_delay_minutes"] == 0
        assert result["delayed"] is False

    @patch("app.commute.requests.get")
    def test_connection_delay_reflected_in_total_delay(self, mock_get):
        # Origin on time, but the CONNECTING train is running 17 min late.
        # This is the exact real-world scenario that originally exposed the bug:
        # total_delay_minutes must reflect connection delay, not just origin delay.
        delayed_response = [dict(TRANSFER_RESPONSE[0])]
        delayed_response[0]["term_delay"] = "17 mins"
        mock_get.return_value = mock_response(delayed_response)

        result = get_commute_leg("Media", "East Falls")
        assert result["total_delay_minutes"] == 17
        assert result["delayed"] is True  # >= SIGNIFICANT_DELAY_MINUTES (10)

    @patch("app.commute.requests.get")
    def test_minor_delay_not_flagged_as_delayed(self, mock_get):
        delayed_response = [dict(TRANSFER_RESPONSE[0])]
        delayed_response[0]["term_delay"] = "3 mins"
        mock_get.return_value = mock_response(delayed_response)

        result = get_commute_leg("Media", "East Falls")
        assert result["total_delay_minutes"] == 3
        assert result["delayed"] is False  # below SIGNIFICANT_DELAY_MINUTES

    @patch("app.commute.requests.get")
    def test_direct_trip_total_delay_matches_origin_delay(self, mock_get):
        delayed_direct = [dict(DIRECT_RESPONSE[0])]
        delayed_direct[0]["orig_delay"] = "12 mins"
        mock_get.return_value = mock_response(delayed_direct)

        result = get_commute_leg("Media", "East Falls")
        assert result["trip_type"] == "direct"
        assert result["total_delay_minutes"] == 12
        assert result["delayed"] is True


class TestWalkTimeByOrigin:
    @patch("app.commute.requests.get")
    def test_media_uses_configured_walk_time(self, mock_get):
        mock_get.return_value = mock_response(TRANSFER_RESPONSE)
        result = get_commute_leg("Media", "East Falls")
        assert result["walk_time_minutes"] == 5

    @patch("app.commute.requests.get")
    def test_east_falls_uses_configured_walk_time(self, mock_get):
        mock_get.return_value = mock_response(TRANSFER_RESPONSE)
        result = get_commute_leg("East Falls", "Media")
        assert result["walk_time_minutes"] == 15

    @patch("app.commute.requests.get")
    def test_unknown_origin_uses_default_walk_time(self, mock_get):
        mock_get.return_value = mock_response(TRANSFER_RESPONSE)
        result = get_commute_leg("Some Unknown Station", "East Falls")
        assert result["walk_time_minutes"] == 5  # DEFAULT_WALK_TIME_MINUTES


class TestAlternativesIncluded:
    @patch("app.commute.requests.get")
    def test_alternatives_contains_all_raw_results(self, mock_get):
        mock_get.return_value = mock_response(TRANSFER_RESPONSE)
        result = get_commute_leg("Media", "East Falls")
        assert len(result["alternatives"]) == len(TRANSFER_RESPONSE)
