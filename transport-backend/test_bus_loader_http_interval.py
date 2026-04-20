import json
import importlib
import sys
from unittest.mock import MagicMock

# Ensure we have the real bus_loader module even if another test injected a mock.
if "bus_loader" in sys.modules and isinstance(sys.modules["bus_loader"], MagicMock):
    del sys.modules["bus_loader"]
import bus_loader as _bl_mod
if isinstance(_bl_mod, MagicMock):
    importlib.invalidate_caches()
    del sys.modules["bus_loader"]
    _bl_mod = importlib.import_module("bus_loader")
BusLoader = _bl_mod.BusLoader


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _payload_for_url(url: str) -> dict:
    # Generic sources pick latest by `created`.
    if url.endswith('/bus/times/SCCU'):
        return {
            "results": [
                {
                    "description": "Stagecoach Cumbria & North Lancashire",
                    "created": "2026-01-01T10:00:00Z",
                    "modified": "2026-01-01T11:00:00Z",
                    "url": "https://transport.scc.lancs.ac.uk/timetable/dataset/100/download/",
                }
            ]
        }
    if url.endswith('/bus/times/SCMY'):
        return {
            "results": [
                {
                    "description": "Stagecoach Merseyside & South Lancashire",
                    "created": "2026-01-01T10:00:00Z",
                    "modified": "2026-01-01T11:00:00Z",
                    "url": "https://transport.scc.lancs.ac.uk/timetable/dataset/101/download/",
                }
            ]
        }

    tag = url.rstrip('/').split('/')[-1]
    return {
        "results": [
            {
                "created": "2026-01-01T10:00:00Z",
                "modified": "2026-01-01T11:00:00Z",
                "url": f"https://transport.scc.lancs.ac.uk/timetable/dataset/{tag}/download/",
            }
        ]
    }


def test_fetch_dataset_info_uses_configured_http_interval(monkeypatch):
    sleeps = []

    def _fake_urlopen(url, context=None, timeout=10):
        return _FakeResp(_payload_for_url(url))

    monkeypatch.setenv('BUS_TIMETABLE_HTTP_INTERVAL_S', '5')
    monkeypatch.setattr('bus_loader.urllib.request.urlopen', _fake_urlopen)
    monkeypatch.setattr('bus_loader.time.sleep', lambda s: sleeps.append(s))

    loader = BusLoader(db_path='postgresql://unused')
    datasets = loader._fetch_dataset_info()

    # 4 generic + 2 description-filtered sources
    assert len(datasets) == 6
    # Sleep between calls only (N-1 sleeps for N requests)
    assert sleeps == [5.0, 5.0, 5.0, 5.0, 5.0]


def test_fetch_dataset_info_defaults_to_10s_http_interval(monkeypatch):
    sleeps = []

    def _fake_urlopen(url, context=None, timeout=10):
        return _FakeResp(_payload_for_url(url))

    monkeypatch.delenv('BUS_TIMETABLE_HTTP_INTERVAL_S', raising=False)
    monkeypatch.setattr('bus_loader.urllib.request.urlopen', _fake_urlopen)
    monkeypatch.setattr('bus_loader.time.sleep', lambda s: sleeps.append(s))

    loader = BusLoader(db_path='postgresql://unused')
    datasets = loader._fetch_dataset_info()

    # 4 generic + 2 description-filtered sources
    assert len(datasets) == 6
    # Sleep between calls only (N-1 sleeps for N requests)
    assert sleeps == [10.0, 10.0, 10.0, 10.0, 10.0]
