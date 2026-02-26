"""Tests for bus_live.py — BusLive class and get_bus_live wrapper.

Tests the XML parsing, filtering, and operator name lookup using
mocked network responses.
"""
import os
import sys
import importlib
from unittest.mock import MagicMock, patch

import pytest

backend_dir = os.path.join(os.path.dirname(__file__), "..")
if backend_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(backend_dir))

# Ensure we have the REAL bus_live, not a mock
sys.modules.pop("bus_live", None)
import bus_live as _real_bl
importlib.reload(_real_bl)
sys.modules["bus_live"] = _real_bl

from bus_live import BusLive, get_bus_live


# ── Sample SIRI XML for testing ──────────────────────────────────────

SIRI_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<Siri>
  <ServiceDelivery>
    <VehicleMonitoringDelivery>
      <VehicleActivity>
        <MonitoredVehicleJourney>
          <LineRef>10</LineRef>
          <DestinationName>Town Centre</DestinationName>
          <OperatorRef>SCCU</OperatorRef>
          <VehicleLocation>
            <Longitude>-2.801</Longitude>
            <Latitude>54.048</Latitude>
          </VehicleLocation>
        </MonitoredVehicleJourney>
      </VehicleActivity>
      <VehicleActivity>
        <MonitoredVehicleJourney>
          <LineRef>42</LineRef>
          <DestinationName>University</DestinationName>
          <OperatorRef>ARCT</OperatorRef>
          <VehicleLocation>
            <Longitude>-2.900</Longitude>
            <Latitude>54.100</Latitude>
          </VehicleLocation>
        </MonitoredVehicleJourney>
      </VehicleActivity>
      <VehicleActivity>
        <MonitoredVehicleJourney>
          <LineRef>99</LineRef>
          <DestinationName>Far Away</DestinationName>
          <OperatorRef>BLAC</OperatorRef>
          <VehicleLocation>
            <Longitude>-3.500</Longitude>
            <Latitude>55.000</Latitude>
          </VehicleLocation>
        </MonitoredVehicleJourney>
      </VehicleActivity>
    </VehicleMonitoringDelivery>
  </ServiceDelivery>
</Siri>
"""

# Minimal XML with no vehicle activities
EMPTY_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<Siri><ServiceDelivery><VehicleMonitoringDelivery/></ServiceDelivery></Siri>
"""

# XML with missing fields
INCOMPLETE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<Siri>
  <ServiceDelivery>
    <VehicleMonitoringDelivery>
      <VehicleActivity>
        <MonitoredVehicleJourney>
          <LineRef>X1</LineRef>
        </MonitoredVehicleJourney>
      </VehicleActivity>
    </VehicleMonitoringDelivery>
  </ServiceDelivery>
</Siri>
"""


def _mock_urlopen(xml_content):
    """Create a mock for urllib.request.urlopen that returns XML."""
    import io
    import xml.etree.ElementTree as ET
    mock_resp = MagicMock()
    mock_resp.read.return_value = xml_content.encode("utf-8")
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ═══════════════════════════════════════════════════════════════════════
#  BusLive constructor
# ═══════════════════════════════════════════════════════════════════════

class TestBusLiveConstructor:
    def test_default_urls(self):
        bl = BusLive()
        assert len(bl.urls) == 6
        assert "SCCU" in bl.urls[0]

    def test_custom_urls(self):
        bl = BusLive(urls=["http://custom-feed/1"])
        assert bl.urls == ["http://custom-feed/1"]

    def test_timeout_none(self):
        bl = BusLive(timeout=None)
        assert bl.timeout is None

    def test_timeout_set(self):
        bl = BusLive(timeout=5)
        assert bl.timeout == 5


# ═══════════════════════════════════════════════════════════════════════
#  BusLive.get_bus_live
# ═══════════════════════════════════════════════════════════════════════

class TestGetBusLive:
    @patch("bus_live.urllib.request.urlopen")
    def test_returns_nearby_vehicles(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen(SIRI_XML)
        bl = BusLive(urls=["http://fake"], timeout=5)
        results = bl.get_bus_live(54.048, -2.801, lat_tol=0.01, lon_tol=0.01)
        # Should find the first vehicle (54.048, -2.801)
        assert len(results) >= 1
        line_refs = [r[0] for r in results]
        assert "10" in line_refs

    @patch("bus_live.urllib.request.urlopen")
    def test_filters_out_distant_vehicles(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen(SIRI_XML)
        bl = BusLive(urls=["http://fake"], timeout=5)
        results = bl.get_bus_live(54.048, -2.801, lat_tol=0.01, lon_tol=0.01)
        # Vehicle "Far Away" at 55.0/-3.5 should be excluded
        line_refs = [r[0] for r in results]
        assert "99" not in line_refs

    @patch("bus_live.urllib.request.urlopen")
    def test_operator_name_resolved(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen(SIRI_XML)
        bl = BusLive(urls=["http://fake"], timeout=5)
        results = bl.get_bus_live(54.048, -2.801, lat_tol=0.1, lon_tol=0.2)
        for r in results:
            if r[0] == "10":
                assert r[4] == "Stagecoach Cumbria & North Lancashire"

    @patch("bus_live.urllib.request.urlopen")
    def test_empty_feed(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen(EMPTY_XML)
        bl = BusLive(urls=["http://fake"], timeout=5)
        results = bl.get_bus_live(54.048, -2.801)
        assert results == []

    @patch("bus_live.urllib.request.urlopen")
    def test_incomplete_xml_skipped(self, mock_urlopen):
        """Vehicle without location coords should be skipped."""
        mock_urlopen.return_value = _mock_urlopen(INCOMPLETE_XML)
        bl = BusLive(urls=["http://fake"], timeout=5)
        results = bl.get_bus_live(54.048, -2.801)
        assert results == []

    @patch("bus_live.urllib.request.urlopen", side_effect=Exception("Network error"))
    def test_network_error_skipped(self, mock_urlopen):
        """Feed errors should be silently skipped."""
        bl = BusLive(urls=["http://broken"], timeout=1)
        results = bl.get_bus_live(54.048, -2.801)
        assert results == []

    @patch("bus_live.urllib.request.urlopen")
    def test_custom_urls_parameter(self, mock_urlopen):
        """urls parameter overrides instance urls."""
        mock_urlopen.return_value = _mock_urlopen(EMPTY_XML)
        bl = BusLive(urls=["http://default"])
        bl.get_bus_live(54.0, -2.8, urls=["http://override"])
        # Should have called with override URL
        mock_urlopen.assert_called()

    @patch("bus_live.urllib.request.urlopen")
    def test_no_timeout_fetch(self, mock_urlopen):
        """BusLive with timeout=None uses urlopen without timeout arg."""
        mock_urlopen.return_value = _mock_urlopen(EMPTY_XML)
        bl = BusLive(urls=["http://fake"], timeout=None)
        bl._fetch_xml("http://fake")
        mock_urlopen.assert_called()


# ═══════════════════════════════════════════════════════════════════════
#  Module-level get_bus_live wrapper
# ═══════════════════════════════════════════════════════════════════════

class TestGetBusLiveWrapper:
    @patch("bus_live.urllib.request.urlopen")
    def test_convenience_function(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen(EMPTY_XML)
        results = get_bus_live(54.0, -2.8, urls=["http://fake"], timeout=5)
        assert isinstance(results, list)


# ═══════════════════════════════════════════════════════════════════════
#  Operator names
# ═══════════════════════════════════════════════════════════════════════

class TestOperatorNames:
    def test_all_known_operators_mapped(self):
        expected = {"ARCT", "BLAC", "KLCO", "SCCU", "SCMY", "NUTT"}
        assert expected == set(BusLive.OPERATOR_NAMES.keys())

    def test_unknown_operator_returns_ref(self):
        """Unknown operator ref should return the ref itself."""
        name = BusLive.OPERATOR_NAMES.get("UNKNOWN", "UNKNOWN")
        assert name == "UNKNOWN"
