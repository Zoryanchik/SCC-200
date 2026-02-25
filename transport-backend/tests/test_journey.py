"""Tests for the POST /journey/plan endpoint.

Validates payload schema, response shape (legs, meta, routeGeometries),
and integration with the RAPTOR router + MergedData coordinate mapping.

Test strategy:
  - Heavy backend modules are mocked at module level (same pattern as test_api.py).
  - Router results are simulated to cover: multi-leg transit, walking-only, no-route, errors.
"""
import sys
import math
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock heavy backend modules BEFORE importing api
# ---------------------------------------------------------------------------
_HEAVY_MODULES = [
    "bus_live", "bus_loader", "bus_data", "main", "time_utils",
    "timetable", "walking", "merged_data", "raptor_router",
    "dense_mapper", "train_data",
]
for mod in _HEAVY_MODULES:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

# Configure time_utils with useful implementations
sys.modules["time_utils"].seconds_since_midnight = MagicMock(return_value=36000)
sys.modules["time_utils"].seconds_to_time = MagicMock(
    side_effect=lambda s: f"{int(s)//3600:02d}:{(int(s)%3600)//60:02d}:{int(s)%60:02d}"
)

import api as api_module  # noqa: E402
from api import app, build_journey_plan_response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    """TestClient without triggering lifespan events."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


#  Valid payload fixture 

VALID_PAYLOAD = {
    "fromStop": {"lat": 54.05, "lon": -2.80},
    "toStop": {"lat": 54.07, "lon": -2.87},
    "departureTime": "10:00:00",
    "date": "2026-02-16",
    "maxTransfers": 3,
    "mode": "both",
}


#  Payload validation tests 


class TestJourneyPlanValidation:
    """Ensure the endpoint enforces required fields and types."""

    def test_missing_fromStop_returns_422(self, client):
        """POST without fromStop yields HTTP 422."""
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "fromStop"}
        resp = client.post("/journey/plan", json=payload)
        assert resp.status_code == 422

    def test_missing_toStop_returns_422(self, client):
        """POST without toStop yields HTTP 422."""
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "toStop"}
        resp = client.post("/journey/plan", json=payload)
        assert resp.status_code == 422

    def test_missing_departureTime_returns_422(self, client):
        """POST without departureTime yields HTTP 422."""
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "departureTime"}
        resp = client.post("/journey/plan", json=payload)
        assert resp.status_code == 422

    def test_missing_date_returns_422(self, client):
        """POST without date yields HTTP 422."""
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "date"}
        resp = client.post("/journey/plan", json=payload)
        assert resp.status_code == 422

    def test_invalid_fromStop_type_returns_422(self, client):
        """fromStop must be an object with lat/lon, not a string."""
        payload = {**VALID_PAYLOAD, "fromStop": "Lancaster"}
        resp = client.post("/journey/plan", json=payload)
        assert resp.status_code == 422

    def test_defaults_maxTransfers_and_mode(self, client):
        """maxTransfers defaults to 5, mode defaults to 'both'."""
        payload = {
            "fromStop": {"lat": 54.05, "lon": -2.80},
            "toStop": {"lat": 54.07, "lon": -2.87},
            "departureTime": "10:00:00",
            "date": "2026-02-16",
        }
        mock_router = MagicMock()
        mock_router.route.return_value = {}
        mock_timetable = MagicMock()
        mock_walking = MagicMock()
        mock_walking._coords = {}

        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_timetable, mock_router, mock_walking)):
            resp = client.post("/journey/plan", json=payload)
        assert resp.status_code == 200
        call_kw = mock_router.route.call_args[1]
        assert call_kw["n_transfer_limit"] == 5
        assert call_kw["allowed_modes"] == {"bus", "train"}


#  Response shape tests 


class TestJourneyPlanResponseShape:
    """Verify response JSON has the required top-level keys."""

    def _mock_route(self, client, route_return):
        mock_router = MagicMock()
        mock_router.route.return_value = route_return
        mock_timetable = MagicMock()
        mock_timetable.today.stop_metadata = ["Stop A", "Stop B", "Stop C"]
        mock_walking = MagicMock()
        mock_walking._coords = {
            0: (54.05, -2.80),
            1: (54.06, -2.83),
            2: (54.07, -2.87),
        }
        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_timetable, mock_router, mock_walking)):
            return client.post("/journey/plan", json=VALID_PAYLOAD)

    def test_response_has_success_key(self, client):
        """Response must include 'success' boolean."""
        resp = self._mock_route(client, {})
        data = resp.json()
        assert "success" in data
        assert isinstance(data["success"], bool)

    def test_response_has_legs_key(self, client):
        """Response must include 'legs' array."""
        resp = self._mock_route(client, {})
        data = resp.json()
        assert "legs" in data
        assert isinstance(data["legs"], list)

    def test_response_has_meta_key(self, client):
        """Response must include 'meta' dict."""
        resp = self._mock_route(client, {})
        data = resp.json()
        assert "meta" in data
        assert isinstance(data["meta"], dict)

    def test_response_has_routeGeometries_key(self, client):
        """Response must include 'routeGeometries' array."""
        resp = self._mock_route(client, {})
        data = resp.json()
        assert "routeGeometries" in data
        assert isinstance(data["routeGeometries"], list)


#  No route found 


class TestJourneyPlanNoRoute:
    """When the router finds no path, response reflects it gracefully."""

    def test_empty_result_returns_success_true(self, client):
        """Empty router result still returns success=True with empty legs."""
        mock_router = MagicMock()
        mock_router.route.return_value = {}
        mock_timetable = MagicMock()
        mock_walking = MagicMock()
        mock_walking._coords = {}

        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_timetable, mock_router, mock_walking)):
            resp = client.post("/journey/plan", json=VALID_PAYLOAD)
        data = resp.json()
        assert data["success"] is True
        assert data["legs"] == []
        assert data["routeGeometries"] == []


#  Walking-only route 


class TestJourneyPlanWalkingOnly:
    """Router returns only _meta (no transit legs)  direct walk."""

    def test_walking_only_has_one_leg(self, client):
        """Walking-only route produces exactly one walking leg."""
        route_result = {
            "_meta": {
                "start_point": (54.05, -2.80),
                "destination": (54.07, -2.87),
                "start_walk_seconds": 300,
                "end_walk_seconds": 0,
                "total_arrival": 36300,
            }
        }
        mock_router = MagicMock()
        mock_router.route.return_value = route_result
        mock_timetable = MagicMock()
        mock_walking = MagicMock()
        mock_walking._coords = {}

        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_timetable, mock_router, mock_walking)):
            resp = client.post("/journey/plan", json=VALID_PAYLOAD)
        data = resp.json()
        assert data["success"] is True
        assert len(data["legs"]) == 1
        assert data["legs"][0]["type"] == "walking"

    def test_walking_only_geometry_has_coords(self, client):
        """Walking-only geometry has start and end coordinates."""
        route_result = {
            "_meta": {
                "start_point": (54.05, -2.80),
                "destination": (54.07, -2.87),
                "start_walk_seconds": 300,
                "end_walk_seconds": 0,
                "total_arrival": 36300,
            }
        }
        mock_router = MagicMock()
        mock_router.route.return_value = route_result
        mock_timetable = MagicMock()
        mock_walking = MagicMock()
        mock_walking._coords = {}

        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_timetable, mock_router, mock_walking)):
            resp = client.post("/journey/plan", json=VALID_PAYLOAD)
        geos = resp.json()["routeGeometries"]
        assert len(geos) >= 1
        geo = geos[0]
        assert "id" in geo
        assert "name" in geo
        assert "coords" in geo
        assert "color" in geo
        assert len(geo["coords"]) == 2


#  Multi-leg transit route 


class TestJourneyPlanMultiLeg:
    """Router returns a multi-stop transit route."""

    MULTI_LEG_RESULT = {
        0: {
            "arrival_time": 36060,
            "prev_stop": None,
            "type": None,
            "journey": None,
            "day": None,
        },
        1: {
            "arrival_time": 36500,
            "prev_stop": 0,
            "type": "bus",
            "journey": 1,
            "day": None,
            "journey_info": {"line_name": "NW:10"},
            "journey_origin": "Lancaster",
            "journey_destination": "Morecambe",
            "board_departure": 36100,
        },
        2: {
            "arrival_time": 36900,
            "prev_stop": 1,
            "type": "walking",
            "journey": None,
            "day": None,
        },
        "_meta": {
            "start_point": (54.05, -2.80),
            "destination": (54.08, -2.88),
            "start_walk_seconds": 60,
            "end_walk_seconds": 90,
            "total_arrival": 36990,
        },
    }

    def _post(self, client):
        mock_router = MagicMock()
        mock_router.route.return_value = dict(self.MULTI_LEG_RESULT)
        mock_timetable = MagicMock()
        mock_timetable.today.stop_metadata = ["Stop A", "Stop B", "Stop C"]
        mock_walking = MagicMock()
        mock_walking._coords = {
            0: (54.05, -2.79),
            1: (54.06, -2.83),
            2: (54.07, -2.87),
        }
        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_timetable, mock_router, mock_walking)):
            return client.post("/journey/plan", json=VALID_PAYLOAD)

    def test_multi_leg_returns_success(self, client):
        """Multi-leg route returns success=True."""
        data = self._post(client).json()
        assert data["success"] is True

    def test_multi_leg_has_multiple_legs(self, client):
        """Should produce walking-start + bus + walking-transfer + walking-end legs."""
        data = self._post(client).json()
        assert len(data["legs"]) >= 3

    def test_multi_leg_bus_leg_has_line_name(self, client):
        """Bus leg should include parsed line_name."""
        data = self._post(client).json()
        bus_legs = [l for l in data["legs"] if l["type"] == "bus"]
        assert len(bus_legs) >= 1
        assert bus_legs[0]["line_name"] == "10"

    def test_multi_leg_geometries_have_coords(self, client):
        """Each geometry entry must have non-empty coords."""
        data = self._post(client).json()
        for geo in data["routeGeometries"]:
            assert "coords" in geo
            assert len(geo["coords"]) >= 1

    def test_multi_leg_geometry_has_required_keys(self, client):
        """Each routeGeometry must have id, name, coords, color."""
        data = self._post(client).json()
        for geo in data["routeGeometries"]:
            assert "id" in geo
            assert "name" in geo
            assert "coords" in geo
            assert "color" in geo

    def test_multi_leg_meta_has_total_arrival(self, client):
        """meta must include total_arrival formatted as time."""
        data = self._post(client).json()
        assert data["meta"]["total_arrival"] is not None

    def test_bus_geometry_color_is_blue(self, client):
        """Bus geometries should use blue color."""
        data = self._post(client).json()
        bus_geos = [g for g in data["routeGeometries"] if "bus" in g["id"].lower() or "Bus" in g["name"]]
        for g in bus_geos:
            assert g["color"] == "#1a73e8"

    def test_walk_geometry_color_is_gray(self, client):
        """Walking geometries should use gray color."""
        data = self._post(client).json()
        walk_geos = [g for g in data["routeGeometries"] if "walk" in g["id"].lower()]
        for g in walk_geos:
            assert g["color"] == "#888888"


#  Mode filtering 


class TestJourneyPlanModeFiltering:
    """Verify mode parameter is forwarded correctly to the router."""

    def _post_with_mode(self, client, mode):
        mock_router = MagicMock()
        mock_router.route.return_value = {}
        mock_timetable = MagicMock()
        mock_walking = MagicMock()
        mock_walking._coords = {}
        with patch.object(api_module, "get_router_for_date",
                          return_value=(mock_timetable, mock_router, mock_walking)):
            payload = {**VALID_PAYLOAD, "mode": mode}
            client.post("/journey/plan", json=payload)
        return mock_router.route.call_args[1]

    def test_mode_bus_filters_bus_only(self, client):
        kw = self._post_with_mode(client, "bus")
        assert kw["allowed_modes"] == {"bus"}

    def test_mode_train_filters_train_only(self, client):
        kw = self._post_with_mode(client, "train")
        assert kw["allowed_modes"] == {"train"}

    def test_mode_both_includes_all(self, client):
        kw = self._post_with_mode(client, "both")
        assert kw["allowed_modes"] == {"bus", "train"}


#  Error handling 


class TestJourneyPlanErrorHandling:
    """Internal errors return success=false with an error message."""

    def test_router_exception_returns_failure(self, client):
        """Router crash returns success=false."""
        with patch.object(api_module, "get_router_for_date",
                          side_effect=RuntimeError("Network down")):
            resp = client.post("/journey/plan", json=VALID_PAYLOAD)
        data = resp.json()
        assert data["success"] is False
        assert "Network down" in data["error"]

    def test_error_response_has_empty_legs(self, client):
        """Error response should have null/empty legs and geometries."""
        with patch.object(api_module, "get_router_for_date",
                          side_effect=ValueError("bad input")):
            resp = client.post("/journey/plan", json=VALID_PAYLOAD)
        data = resp.json()
        assert data["success"] is False
        assert data.get("legs") is None or data["legs"] == []


#  build_journey_plan_response unit tests 


class TestBuildJourneyPlanResponse:
    """Direct unit tests for the response builder helper."""

    def test_empty_result(self):
        """Empty/falsy route_result returns empty structure."""
        result = build_journey_plan_response({}, MagicMock(), {})
        assert result["success"] is True
        assert result["legs"] == []
        assert result["routeGeometries"] == []

    def test_none_result(self):
        """None route_result returns empty structure."""
        result = build_journey_plan_response(None, MagicMock(), {})
        assert result["success"] is True
        assert result["legs"] == []

    def test_walking_only_result(self):
        """Walking-only result produces one walking leg."""
        route = {
            "_meta": {
                "start_point": (54.0, -2.8),
                "destination": (54.1, -2.9),
                "start_walk_seconds": 200,
                "end_walk_seconds": 0,
                "total_arrival": 36200,
            }
        }
        result = build_journey_plan_response(route, MagicMock(), {})
        assert result["success"] is True
        assert len(result["legs"]) == 1
        assert result["legs"][0]["type"] == "walking"
        assert len(result["routeGeometries"]) >= 1

    def test_walking_only_no_arrival(self):
        """Walking-only with total_arrival=None still works."""
        route = {
            "_meta": {
                "start_point": (54.0, -2.8),
                "destination": (54.1, -2.9),
                "start_walk_seconds": 200,
                "end_walk_seconds": 0,
                "total_arrival": None,
            }
        }
        result = build_journey_plan_response(route, MagicMock(), {})
        assert result["success"] is True
        assert result["legs"][0]["arrival_time"] is None

    def test_walking_only_empty_points(self):
        """Walking-only with empty start/dest tuples still works."""
        route = {
            "_meta": {
                "start_point": (),
                "destination": (),
                "start_walk_seconds": 200,
                "end_walk_seconds": 0,
                "total_arrival": None,
            }
        }
        result = build_journey_plan_response(route, MagicMock(), {})
        assert result["success"] is True

    def test_multi_stop_with_coords(self):
        """Multi-stop route maps stop integers to coordinates."""
        merged = MagicMock()
        merged.stop_metadata = ["Stop A", "Stop B"]
        stop_coords = {0: (54.05, -2.80), 1: (54.06, -2.83)}
        route = {
            0: {
                "arrival_time": 36060,
                "prev_stop": None,
                "type": None,
                "journey": None,
                "day": None,
            },
            1: {
                "arrival_time": 36500,
                "prev_stop": 0,
                "type": "bus",
                "journey": 1,
                "day": None,
                "journey_info": {"line_name": "NW:42"},
                "journey_origin": "Depot",
                "journey_destination": "Centre",
                "board_departure": 36100,
            },
            "_meta": {
                "start_point": (54.04, -2.79),
                "destination": (54.07, -2.84),
                "start_walk_seconds": 60,
                "end_walk_seconds": 30,
                "total_arrival": 36530,
            },
        }
        result = build_journey_plan_response(route, merged, stop_coords)
        assert result["success"] is True
        assert len(result["legs"]) >= 2
        # Bus leg should have line_name parsed (split on ':')
        bus_legs = [l for l in result["legs"] if l["type"] == "bus"]
        assert bus_legs[0]["line_name"] == "42"
        # Geometries should have coordinates
        for geo in result["routeGeometries"]:
            assert len(geo["coords"]) >= 1

    def test_stop_out_of_metadata_range(self):
        """Stop index beyond metadata falls back to stop#N."""
        merged = MagicMock()
        merged.stop_metadata = []
        stop_coords = {99: (54.05, -2.80)}
        route = {
            99: {
                "arrival_time": 36000,
                "prev_stop": None,
                "type": None,
                "journey": None,
                "day": None,
            },
            "_meta": {},
        }
        result = build_journey_plan_response(route, merged, stop_coords)
        assert result["success"] is True
        # Should not crash

    def test_train_geometry_color_is_red(self):
        """Train leg geometry should use red color."""
        merged = MagicMock()
        merged.stop_metadata = ["A", "B"]
        stop_coords = {0: (54.0, -2.8), 1: (54.1, -2.9)}
        route = {
            0: {"arrival_time": 36000, "prev_stop": None, "type": None,
                "journey": None, "day": None},
            1: {"arrival_time": 36600, "prev_stop": 0, "type": "train",
                "journey": 1, "day": None, "journey_info": None,
                "journey_origin": "", "journey_destination": "",
                "board_departure": 36100},
            "_meta": {"start_point": (), "destination": (),
                      "start_walk_seconds": 0, "end_walk_seconds": 0,
                      "total_arrival": None},
        }
        result = build_journey_plan_response(route, merged, stop_coords)
        train_geos = [g for g in result["routeGeometries"]
                      if "train" in g["id"].lower()]
        assert len(train_geos) >= 1
        assert train_geos[0]["color"] == "#e53935"

    def test_inf_arrival_handled(self):
        """Infinite arrival time produces None in the response."""
        merged = MagicMock()
        merged.stop_metadata = ["X"]
        route = {
            0: {"arrival_time": float("inf"), "prev_stop": None, "type": None,
                "journey": None, "day": None},
            "_meta": {},
        }
        result = build_journey_plan_response(route, merged, {})
        assert result["success"] is True


# ═══ Multi-leg intermediate geometry tests ═══════════════════════════


class TestIntermediateGeometry:
    """Verify transit legs include intermediate stop coordinates in
    routeGeometries when journey data is available via the 'day' field."""

    def _make_day(self, journey_times, journey_stop_index):
        """Build a mock MergedData 'day' with journey info."""
        day = MagicMock()
        day.journey_times = journey_times
        day.journey_stop_index = journey_stop_index
        day.stop_metadata = [
            "Stop A", "Stop B", "Stop C", "Stop D", "Stop E"
        ]
        return day

    def test_transit_leg_includes_intermediate_coords(self):
        """Bus leg through A→B→C→D should produce 4-point polyline."""
        # Journey 1 visits stops 0→1→2→3 (A→B→C→D)
        jt = [
            (0, 36000, 36000),
            (1, 36200, 36210),
            (2, 36400, 36410),
            (3, 36600, 36600),
        ]
        jsi = {0: 0, 1: 1, 2: 2, 3: 3}
        day = self._make_day([[], jt], [{}, jsi])

        merged = MagicMock()
        merged.stop_metadata = ["Stop A", "Stop B", "Stop C", "Stop D"]
        stop_coords = {
            0: (54.00, -2.80),
            1: (54.01, -2.81),
            2: (54.02, -2.82),
            3: (54.03, -2.83),
        }
        route = {
            0: {"arrival_time": 36000, "prev_stop": None, "type": None,
                "journey": None, "day": None},
            3: {"arrival_time": 36600, "prev_stop": 0, "type": "bus",
                "journey": 1, "day": day,
                "journey_info": {"line_name": "42"},
                "journey_origin": "Stop A", "journey_destination": "Stop D",
                "board_departure": 36000},
            "_meta": {"start_point": (), "destination": (),
                      "start_walk_seconds": 0, "end_walk_seconds": 0,
                      "total_arrival": None},
        }
        result = build_journey_plan_response(route, merged, stop_coords)
        bus_geos = [g for g in result["routeGeometries"]
                    if "bus" in g["id"]]
        assert len(bus_geos) == 1
        # Should have 4 coords: A, B, C, D (not just A, D)
        assert len(bus_geos[0]["coords"]) == 4
        assert bus_geos[0]["coords"][0] == [54.00, -2.80]
        assert bus_geos[0]["coords"][1] == [54.01, -2.81]
        assert bus_geos[0]["coords"][2] == [54.02, -2.82]
        assert bus_geos[0]["coords"][3] == [54.03, -2.83]

    def test_intermediate_stops_in_leg(self):
        """Transit leg should list intermediate stop names."""
        jt = [
            (0, 36000, 36000),
            (1, 36200, 36210),
            (2, 36400, 36410),
            (3, 36600, 36600),
        ]
        jsi = {0: 0, 1: 1, 2: 2, 3: 3}
        day = self._make_day([[], jt], [{}, jsi])

        merged = MagicMock()
        merged.stop_metadata = ["Stop A", "Stop B", "Stop C", "Stop D"]
        stop_coords = {
            0: (54.00, -2.80), 1: (54.01, -2.81),
            2: (54.02, -2.82), 3: (54.03, -2.83),
        }
        route = {
            0: {"arrival_time": 36000, "prev_stop": None, "type": None,
                "journey": None, "day": None},
            3: {"arrival_time": 36600, "prev_stop": 0, "type": "bus",
                "journey": 1, "day": day,
                "journey_info": {"line_name": "42"},
                "journey_origin": "A", "journey_destination": "D",
                "board_departure": 36000},
            "_meta": {"start_point": (), "destination": (),
                      "start_walk_seconds": 0, "end_walk_seconds": 0,
                      "total_arrival": None},
        }
        result = build_journey_plan_response(route, merged, stop_coords)
        bus_legs = [l for l in result["legs"] if l["type"] == "bus"]
        assert len(bus_legs) == 1
        intermediates = bus_legs[0].get("intermediate_stops", [])
        # Stops B and C are between boarding (A) and alighting (D)
        assert len(intermediates) == 2
        assert intermediates[0]["name"] == "Stop B"
        assert intermediates[1]["name"] == "Stop C"
        assert intermediates[0]["lat"] == 54.01

    def test_no_intermediates_for_adjacent_stops(self):
        """When bus goes directly A→B (no intermediate), list is empty."""
        jt = [(0, 36000, 36000), (1, 36300, 36300)]
        jsi = {0: 0, 1: 1}
        day = self._make_day([[], jt], [{}, jsi])

        merged = MagicMock()
        merged.stop_metadata = ["Stop A", "Stop B"]
        stop_coords = {0: (54.00, -2.80), 1: (54.01, -2.81)}
        route = {
            0: {"arrival_time": 36000, "prev_stop": None, "type": None,
                "journey": None, "day": None},
            1: {"arrival_time": 36300, "prev_stop": 0, "type": "bus",
                "journey": 1, "day": day,
                "journey_info": None, "journey_origin": "",
                "journey_destination": "", "board_departure": 36000},
            "_meta": {"start_point": (), "destination": (),
                      "start_walk_seconds": 0, "end_walk_seconds": 0,
                      "total_arrival": None},
        }
        result = build_journey_plan_response(route, merged, stop_coords)
        bus_legs = [l for l in result["legs"] if l["type"] == "bus"]
        assert bus_legs[0].get("intermediate_stops") == []

    def test_fallback_when_day_is_none(self):
        """When day field is None, geometry has 2-point fallback."""
        merged = MagicMock()
        merged.stop_metadata = ["Stop A", "Stop B"]
        stop_coords = {0: (54.00, -2.80), 1: (54.05, -2.85)}
        route = {
            0: {"arrival_time": 36000, "prev_stop": None, "type": None,
                "journey": None, "day": None},
            1: {"arrival_time": 36600, "prev_stop": 0, "type": "bus",
                "journey": 1, "day": None,
                "journey_info": None, "journey_origin": "",
                "journey_destination": "", "board_departure": 36000},
            "_meta": {"start_point": (), "destination": (),
                      "start_walk_seconds": 0, "end_walk_seconds": 0,
                      "total_arrival": None},
        }
        result = build_journey_plan_response(route, merged, stop_coords)
        bus_geos = [g for g in result["routeGeometries"]
                    if "bus" in g["id"]]
        assert len(bus_geos) == 1
        # Falls back to 2 points
        assert len(bus_geos[0]["coords"]) == 2

    def test_fallback_when_journey_is_none(self):
        """When journey id is None, geometry falls back to 2 points."""
        merged = MagicMock()
        merged.stop_metadata = ["X", "Y"]
        stop_coords = {0: (54.0, -2.8), 1: (54.1, -2.9)}
        route = {
            0: {"arrival_time": 36000, "prev_stop": None, "type": None,
                "journey": None, "day": None},
            1: {"arrival_time": 36600, "prev_stop": 0, "type": "train",
                "journey": None, "day": MagicMock(),
                "journey_info": None, "journey_origin": "",
                "journey_destination": "", "board_departure": 36000},
            "_meta": {"start_point": (), "destination": (),
                      "start_walk_seconds": 0, "end_walk_seconds": 0,
                      "total_arrival": None},
        }
        result = build_journey_plan_response(route, merged, stop_coords)
        train_geos = [g for g in result["routeGeometries"]
                      if "train" in g["id"]]
        assert len(train_geos[0]["coords"]) == 2

    def test_fallback_when_stops_not_in_journey(self):
        """When boarding stop not found in journey_stop_index, fallback."""
        day = MagicMock()
        day.journey_times = [[(10, 100, 100), (11, 200, 200)]]
        day.journey_stop_index = [{10: 0, 11: 1}]  # stops 0,1 not in index

        merged = MagicMock()
        merged.stop_metadata = ["X", "Y"]
        stop_coords = {0: (54.0, -2.8), 1: (54.1, -2.9)}
        route = {
            0: {"arrival_time": 36000, "prev_stop": None, "type": None,
                "journey": None, "day": None},
            1: {"arrival_time": 36600, "prev_stop": 0, "type": "bus",
                "journey": 0, "day": day,
                "journey_info": None, "journey_origin": "",
                "journey_destination": "", "board_departure": 36000},
            "_meta": {"start_point": (), "destination": (),
                      "start_walk_seconds": 0, "end_walk_seconds": 0,
                      "total_arrival": None},
        }
        result = build_journey_plan_response(route, merged, stop_coords)
        bus_geos = [g for g in result["routeGeometries"]
                    if "bus" in g["id"]]
        assert len(bus_geos[0]["coords"]) == 2  # fallback

    def test_skips_intermediate_stops_without_coords(self):
        """Intermediate stops with no coord entry are skipped in geometry."""
        jt = [
            (0, 36000, 36000),
            (1, 36200, 36210),  # stop 1 has NO coords
            (2, 36400, 36410),
            (3, 36600, 36600),
        ]
        jsi = {0: 0, 1: 1, 2: 2, 3: 3}
        day = self._make_day([[], jt], [{}, jsi])

        merged = MagicMock()
        merged.stop_metadata = ["A", "B", "C", "D"]
        # Stop 1 deliberately missing from coords
        stop_coords = {0: (54.00, -2.80), 2: (54.02, -2.82), 3: (54.03, -2.83)}
        route = {
            0: {"arrival_time": 36000, "prev_stop": None, "type": None,
                "journey": None, "day": None},
            3: {"arrival_time": 36600, "prev_stop": 0, "type": "bus",
                "journey": 1, "day": day,
                "journey_info": None, "journey_origin": "",
                "journey_destination": "", "board_departure": 36000},
            "_meta": {"start_point": (), "destination": (),
                      "start_walk_seconds": 0, "end_walk_seconds": 0,
                      "total_arrival": None},
        }
        result = build_journey_plan_response(route, merged, stop_coords)
        bus_geos = [g for g in result["routeGeometries"]
                    if "bus" in g["id"]]
        # 3 coords: stop 0, stop 2, stop 3 (stop 1 skipped)
        assert len(bus_geos[0]["coords"]) == 3

    def test_train_leg_with_intermediates(self):
        """Train legs also get intermediate coords."""
        jt = [
            (0, 36000, 36000),
            (1, 36300, 36310),
            (2, 36600, 36600),
        ]
        jsi = {0: 0, 1: 1, 2: 2}
        day = self._make_day([[], [], jt], [{}, {}, jsi])

        merged = MagicMock()
        merged.stop_metadata = ["Station A", "Station B", "Station C"]
        stop_coords = {
            0: (51.50, -0.10),
            1: (51.55, -0.15),
            2: (51.60, -0.20),
        }
        route = {
            0: {"arrival_time": 36000, "prev_stop": None, "type": None,
                "journey": None, "day": None},
            2: {"arrival_time": 36600, "prev_stop": 0, "type": "train",
                "journey": 2, "day": day,
                "journey_info": {"line_name": "Avanti"},
                "journey_origin": "Station A",
                "journey_destination": "Station C",
                "board_departure": 36000},
            "_meta": {"start_point": (), "destination": (),
                      "start_walk_seconds": 0, "end_walk_seconds": 0,
                      "total_arrival": None},
        }
        result = build_journey_plan_response(route, merged, stop_coords)
        train_geos = [g for g in result["routeGeometries"]
                      if "train" in g["id"]]
        assert len(train_geos) == 1
        assert len(train_geos[0]["coords"]) == 3  # A, B, C
        assert train_geos[0]["color"] == "#e53935"

    def test_walking_legs_unchanged(self):
        """Walking legs still use 2-point geometry (no journey data)."""
        merged = MagicMock()
        merged.stop_metadata = ["X", "Y"]
        stop_coords = {0: (54.0, -2.8), 1: (54.1, -2.9)}
        route = {
            0: {"arrival_time": 36000, "prev_stop": None, "type": None,
                "journey": None, "day": None},
            1: {"arrival_time": 36600, "prev_stop": 0, "type": "walking",
                "journey": None, "day": None},
            "_meta": {"start_point": (), "destination": (),
                      "start_walk_seconds": 0, "end_walk_seconds": 0,
                      "total_arrival": None},
        }
        result = build_journey_plan_response(route, merged, stop_coords)
        walk_geos = [g for g in result["routeGeometries"]
                     if "walk" in g["id"]]
        assert len(walk_geos) == 1
        assert len(walk_geos[0]["coords"]) == 2

    def test_multi_transfer_route_all_intermediates(self):
        """Multi-transfer route: each transit leg gets its own intermediates."""
        # Journey 1: stops 0→1→2 (bus)
        jt1 = [(0, 36000, 36000), (1, 36200, 36210), (2, 36400, 36400)]
        jsi1 = {0: 0, 1: 1, 2: 2}
        # Journey 2: stops 2→3→4 (train)
        jt2 = [(2, 36500, 36500), (3, 36700, 36710), (4, 36900, 36900)]
        jsi2 = {2: 0, 3: 1, 4: 2}

        day = MagicMock()
        day.journey_times = [[], jt1, jt2]
        day.journey_stop_index = [{}, jsi1, jsi2]
        day.stop_metadata = ["A", "B", "C", "D", "E"]

        merged = MagicMock()
        merged.stop_metadata = ["A", "B", "C", "D", "E"]
        stop_coords = {
            0: (54.00, -2.80), 1: (54.01, -2.81), 2: (54.02, -2.82),
            3: (54.03, -2.83), 4: (54.04, -2.84),
        }
        route = {
            0: {"arrival_time": 36000, "prev_stop": None, "type": None,
                "journey": None, "day": None},
            2: {"arrival_time": 36400, "prev_stop": 0, "type": "bus",
                "journey": 1, "day": day,
                "journey_info": {"line_name": "X1"},
                "journey_origin": "A", "journey_destination": "C",
                "board_departure": 36000},
            4: {"arrival_time": 36900, "prev_stop": 2, "type": "train",
                "journey": 2, "day": day,
                "journey_info": {"line_name": "TRN"},
                "journey_origin": "C", "journey_destination": "E",
                "board_departure": 36500},
            "_meta": {"start_point": (), "destination": (),
                      "start_walk_seconds": 0, "end_walk_seconds": 0,
                      "total_arrival": None},
        }
        result = build_journey_plan_response(route, merged, stop_coords)

        bus_geos = [g for g in result["routeGeometries"] if "bus" in g["id"]]
        train_geos = [g for g in result["routeGeometries"] if "train" in g["id"]]

        # Bus A→B→C = 3 coords
        assert len(bus_geos) == 1
        assert len(bus_geos[0]["coords"]) == 3

        # Train C→D→E = 3 coords
        assert len(train_geos) == 1
        assert len(train_geos[0]["coords"]) == 3

        # Check intermediate_stops on each leg
        bus_legs = [l for l in result["legs"] if l["type"] == "bus"]
        train_legs = [l for l in result["legs"] if l["type"] == "train"]
        assert len(bus_legs[0]["intermediate_stops"]) == 1  # B
        assert bus_legs[0]["intermediate_stops"][0]["name"] == "B"
        assert len(train_legs[0]["intermediate_stops"]) == 1  # D
        assert train_legs[0]["intermediate_stops"][0]["name"] == "D"

    def test_journey_index_error_falls_back(self):
        """IndexError when accessing journey_times falls back gracefully."""
        day = MagicMock()
        day.journey_times = []  # empty — will trigger IndexError
        day.journey_stop_index = []

        merged = MagicMock()
        merged.stop_metadata = ["X", "Y"]
        stop_coords = {0: (54.0, -2.8), 1: (54.1, -2.9)}
        route = {
            0: {"arrival_time": 36000, "prev_stop": None, "type": None,
                "journey": None, "day": None},
            1: {"arrival_time": 36600, "prev_stop": 0, "type": "bus",
                "journey": 99, "day": day,
                "journey_info": None, "journey_origin": "",
                "journey_destination": "", "board_departure": 36000},
            "_meta": {"start_point": (), "destination": (),
                      "start_walk_seconds": 0, "end_walk_seconds": 0,
                      "total_arrival": None},
        }
        result = build_journey_plan_response(route, merged, stop_coords)
        bus_geos = [g for g in result["routeGeometries"] if "bus" in g["id"]]
        assert len(bus_geos[0]["coords"]) == 2  # fallback
