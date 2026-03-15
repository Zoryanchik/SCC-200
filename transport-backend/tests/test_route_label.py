"""Tests for the /routes/label/{line} endpoint.

This endpoint is intended to be a cheap way for frontends to fetch canonical
route_ids for a line without downloading full variant geometry.

Contract (current):
  - 200 with {line, variant_count, route_ids}
  - route_ids are canonical strings and should be deduplicated while
    *preserving first-seen order*.
"""

import sys
from unittest.mock import MagicMock

import pytest


# Mock heavy modules before importing api, mirroring tests/test_api.py.
_HEAVY_MODULES = [
    "bus_live",
    "bus_loader",
    "bus_data",
    "main",
    "time_utils",
    "timetable",
    "walking",
    "merged_data",
    "raptor_router",
    "dense_mapper",
    "train_data",
]

for mod in _HEAVY_MODULES:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()


import api as api_module  # noqa: E402
from api import app  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


class TestRouteLabelEndpoint:
    def test_routes_label_dedupes_route_ids_preserving_order(self, client):
        # Build a synthetic route line response that contains duplicates and
        # varying variants. This avoids depending on real timetables.
        #
        # The api implementation looks at routeData['variants'][*]['route_id'].
        line_data = {
            "line": "7",
            "variants": [
                {"route_id": "A"},
                {"route_id": "B"},
                {"route_id": "A"},
                {"route_id": "C"},
                {"route_id": "B"},
            ],
        }

        # Prime the in-module cache used by api.py so the handler doesn't try
        # to build real timetable data.
        api_module._route_label_cache.clear()
        # The handler uses cached /routes/line data if present.
        api_module._route_line_cache["7"] = line_data

        resp = client.get("/routes/label/7")
        assert resp.status_code == 200
        data = resp.json()
        assert data["line"] == "7"
        assert data["variant_count"] == 5
        assert data["route_ids"] == ["A", "B", "C"]

    def test_routes_label_404_when_no_variants(self, client):
        api_module._route_label_cache.clear()
        api_module._route_line_cache["42"] = {"line": "42", "variants": []}

        resp = client.get("/routes/label/42")
        assert resp.status_code == 404
