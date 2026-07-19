from unittest.mock import patch, MagicMock

import pytest

from app.commute import get_commute_leg


def mock_response(json_data):
    mock = MagicMock()
    mock.json.return_value = json_data
    return mock


def mock_get_by_station_pair(response_map, default=None):
    """Builds a requests.get side_effect that returns different mocked JSON
    depending on which (req1, req2) station pair was queried - needed now
    that get_commute_leg can make up to 3 real calls (origin->destination,
    origin->transfer, transfer->destination)."""
    def side_effect(url, params=None, **kwargs):
        key = (params["req1"], params["req2"])
        return mock_response(response_map.get(key, default if default is not None else []))
    return side_effect


# A realistic "transfer required" response for the origin->destination call,
# matching SEPTA's real shape. Used only to confirm no direct trip exists and
# as the "alternatives" payload - the actual chosen route now comes from the
# stitched leg1/leg2 calls below.
NO_DIRECT_RESPONSE = [
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

# origin -> Jefferson Station (leg 1 of the stitched connection)
LEG1_RESPONSE = [
    {
        "orig_train": "3538",
        "orig_line": "Media/Wawa",
        "orig_departure_time": "2:03PM",
        "orig_arrival_time": "2:37PM",
        "orig_delay": "On time",
        "isdirect": "true",
    },
]

# Jefferson Station -> destination (leg 2 of the stitched connection)
LEG2_RESPONSE = [
    {
        "orig_train": "4240",
        "orig_line": "Manayunk/Norristown",
        "orig_departure_time": "3:00PM",
        "orig_arrival_time": "3:22PM",
        "orig_delay": "On time",
        "isdirect": "true",
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
        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): LEG1_RESPONSE,
            ("Jefferson Station", "East Falls"): LEG2_RESPONSE,
        })
        result = get_commute_leg("Media", "East Falls")
        assert result["trip_type"] == "transfer"
        assert result["origin_train"] == "3538"
        assert result["connection_train"] == "4240"
        assert result["connection_station"] == "Jefferson Station"

    @patch("app.commute.requests.get")
    def test_empty_results_returns_error(self, mock_get):
        mock_get.return_value = mock_response([])
        result = get_commute_leg("Media", "East Falls")
        assert "error" in result

    @patch("app.commute.requests.get")
    def test_falls_back_to_septa_pick_when_stitched_connection_not_catchable(self, mock_get):
        # Leg 1 doesn't arrive at Jefferson until well after every leg-2
        # train has already left - the stitched connection is impossible,
        # so we should fall back to SEPTA's own single-call pick.
        unreachable_leg1 = [dict(LEG1_RESPONSE[0])]
        unreachable_leg1[0]["orig_arrival_time"] = "2:37PM"
        unreachable_leg1[0]["orig_delay"] = "45 mins"  # actual arrival 3:22PM

        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): unreachable_leg1,
            ("Jefferson Station", "East Falls"): LEG2_RESPONSE,  # departs 3:00PM, already gone
        })
        result = get_commute_leg("Media", "East Falls")
        # Falls back to NO_DIRECT_RESPONSE's own chosen transfer (30th St, train 4240)
        assert result["trip_type"] == "transfer"
        assert result["connection_station"] == "30th Street Station"
        assert result["origin_train"] == "3538"


class TestDelayAwareTransferBuffer:
    @patch("app.commute.requests.get")
    def test_on_time_trip_has_full_buffer(self, mock_get):
        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): LEG1_RESPONSE,
            ("Jefferson Station", "East Falls"): LEG2_RESPONSE,
        })
        result = get_commute_leg("Media", "East Falls")
        # 2:37PM arrival -> 3:00PM departure = 23 min buffer
        assert result["transfer_buffer_minutes"] == 23.0
        assert result["at_risk"] is False

    @patch("app.commute.requests.get")
    def test_origin_delay_shrinks_transfer_buffer(self, mock_get):
        delayed_leg1 = [dict(LEG1_RESPONSE[0])]
        delayed_leg1[0]["orig_delay"] = "20 mins"

        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): delayed_leg1,
            ("Jefferson Station", "East Falls"): LEG2_RESPONSE,
        })
        result = get_commute_leg("Media", "East Falls")
        # 23 min buffer - 20 min delay = 3 min remaining
        assert result["transfer_buffer_minutes"] == 3.0
        # Trains share a platform at Jefferson (RISK_BUFFER_MINUTES = 0), so a
        # positive 3 min buffer is NOT at risk - only a negative buffer is.
        assert result["at_risk"] is False

    @patch("app.commute.requests.get")
    def test_severe_origin_delay_flags_at_risk(self, mock_get):
        delayed_leg1 = [dict(LEG1_RESPONSE[0])]
        delayed_leg1[0]["orig_delay"] = "24 mins"  # shrinks 23 min buffer to -1 (past 0)

        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): delayed_leg1,
            ("Jefferson Station", "East Falls"): LEG2_RESPONSE,
        })
        result = get_commute_leg("Media", "East Falls")
        assert result["transfer_buffer_minutes"] == -1.0
        assert result["at_risk"] is True


class TestTotalDelayCalculation:
    @patch("app.commute.requests.get")
    def test_no_delay_means_zero_total_delay(self, mock_get):
        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): LEG1_RESPONSE,
            ("Jefferson Station", "East Falls"): LEG2_RESPONSE,
        })
        result = get_commute_leg("Media", "East Falls")
        assert result["total_delay_minutes"] == 0
        assert result["delayed"] is False

    @patch("app.commute.requests.get")
    def test_connection_delay_reflected_in_total_delay(self, mock_get):
        # Origin on time, but the CONNECTING train is running 17 min late.
        # This is the exact real-world scenario that originally exposed the
        # bug: total_delay_minutes must reflect connection delay, not just
        # origin delay.
        delayed_leg2 = [dict(LEG2_RESPONSE[0])]
        delayed_leg2[0]["orig_delay"] = "17 mins"

        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): LEG1_RESPONSE,
            ("Jefferson Station", "East Falls"): delayed_leg2,
        })
        result = get_commute_leg("Media", "East Falls")
        assert result["total_delay_minutes"] == 17
        assert result["delayed"] is True  # >= SIGNIFICANT_DELAY_MINUTES (10)

    @patch("app.commute.requests.get")
    def test_minor_delay_not_flagged_as_delayed(self, mock_get):
        delayed_leg2 = [dict(LEG2_RESPONSE[0])]
        delayed_leg2[0]["orig_delay"] = "3 mins"

        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): LEG1_RESPONSE,
            ("Jefferson Station", "East Falls"): delayed_leg2,
        })
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
        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): LEG1_RESPONSE,
            ("Jefferson Station", "East Falls"): LEG2_RESPONSE,
        })
        result = get_commute_leg("Media", "East Falls")
        assert result["walk_time_minutes"] == 5

    @patch("app.commute.requests.get")
    def test_east_falls_uses_configured_walk_time(self, mock_get):
        mock_get.side_effect = mock_get_by_station_pair({
            ("East Falls", "Media"): NO_DIRECT_RESPONSE,
            ("East Falls", "Jefferson Station"): LEG1_RESPONSE,
            ("Jefferson Station", "Media"): LEG2_RESPONSE,
        })
        result = get_commute_leg("East Falls", "Media")
        assert result["walk_time_minutes"] == 15

    @patch("app.commute.requests.get")
    def test_unknown_origin_uses_default_walk_time(self, mock_get):
        mock_get.side_effect = mock_get_by_station_pair({
            ("Some Unknown Station", "East Falls"): NO_DIRECT_RESPONSE,
            ("Some Unknown Station", "Jefferson Station"): LEG1_RESPONSE,
            ("Jefferson Station", "East Falls"): LEG2_RESPONSE,
        })
        result = get_commute_leg("Some Unknown Station", "East Falls")
        assert result["walk_time_minutes"] == 5  # DEFAULT_WALK_TIME_MINUTES


class TestAlternativesIncluded:
    @patch("app.commute.requests.get")
    def test_alternatives_contains_all_raw_results(self, mock_get):
        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): LEG1_RESPONSE,
            ("Jefferson Station", "East Falls"): LEG2_RESPONSE,
        })
        result = get_commute_leg("Media", "East Falls")
        # Alternatives reflect the original origin->destination call (what
        # SEPTA itself considered), not the internal leg1/leg2 stitching calls.
        assert len(result["alternatives"]) == len(NO_DIRECT_RESPONSE)


class TestForcedTransferStitching:
    @patch("app.commute.requests.get")
    def test_stitches_via_preferred_transfer_station_over_septas_own_pick(self, mock_get):
        # SEPTA's own single-call pick would use 30th Street Station, but
        # PREFERRED_TRANSFER_STATION should force Jefferson Station instead.
        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): LEG1_RESPONSE,
            ("Jefferson Station", "East Falls"): LEG2_RESPONSE,
        })
        result = get_commute_leg("Media", "East Falls")
        assert result["connection_station"] == "Jefferson Station"

    @patch("app.commute.requests.get")
    def test_picks_earliest_catchable_leg2_train(self, mock_get):
        # Two leg-2 options: the first departs before leg1 arrives (miss it),
        # the second is comfortably catchable - should pick the second.
        leg2_options = [
            {
                "orig_train": "4230",
                "orig_line": "Manayunk/Norristown",
                "orig_departure_time": "2:30PM",  # leaves before leg1 arrives at 2:37PM
                "orig_arrival_time": "2:52PM",
                "orig_delay": "On time",
                "isdirect": "true",
            },
            {
                "orig_train": "4240",
                "orig_line": "Manayunk/Norristown",
                "orig_departure_time": "3:00PM",
                "orig_arrival_time": "3:22PM",
                "orig_delay": "On time",
                "isdirect": "true",
            },
        ]
        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): LEG1_RESPONSE,
            ("Jefferson Station", "East Falls"): leg2_options,
        })
        result = get_commute_leg("Media", "East Falls")
        assert result["connection_train"] == "4240"

    @patch("app.commute.requests.get")
    def test_falls_back_when_leg1_has_no_results(self, mock_get):
        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): [],
            ("Jefferson Station", "East Falls"): LEG2_RESPONSE,
        })
        result = get_commute_leg("Media", "East Falls")
        assert result["trip_type"] == "transfer"
        assert result["connection_station"] == "30th Street Station"

    @patch("app.commute.requests.get")
    def test_falls_back_when_leg2_has_no_results(self, mock_get):
        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): LEG1_RESPONSE,
            ("Jefferson Station", "East Falls"): [],
        })
        result = get_commute_leg("Media", "East Falls")
        assert result["trip_type"] == "transfer"
        assert result["connection_station"] == "30th Street Station"

    @patch("app.commute.requests.get")
    def test_slightly_negative_buffer_still_catchable_not_missed(self, mock_get):
        # A buffer of -3 min is within MISSED_CONNECTION_BUFFER_MINUTES (-5),
        # so it should still be used (flagged at_risk) rather than falling back.
        delayed_leg1 = [dict(LEG1_RESPONSE[0])]
        delayed_leg1[0]["orig_delay"] = "26 mins"  # 23 min buffer - 26 = -3

        mock_get.side_effect = mock_get_by_station_pair({
            ("Media", "East Falls"): NO_DIRECT_RESPONSE,
            ("Media", "Jefferson Station"): delayed_leg1,
            ("Jefferson Station", "East Falls"): LEG2_RESPONSE,
        })
        result = get_commute_leg("Media", "East Falls")
        assert result["connection_station"] == "Jefferson Station"
        assert result["transfer_buffer_minutes"] == -3.0
        assert result["at_risk"] is True
