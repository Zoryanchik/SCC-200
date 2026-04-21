import train_loader as train_loader_module
from train_loader import TrainLoader
import urllib.error


def test_resolve_tiploc_prefers_train_station_over_taxi_rank():
    loader = TrainLoader("postgresql://unused")

    tiploc_info = {
        "LANCASTR": {
            "atco_code": "2500LAN",
        }
    }

    label_to_atcos = {
        "2500LAN": [
            "2500TRAIN",
            "2500TAXI",
            "2500BUS",
        ]
    }

    atco_meta = {
        "2500TRAIN": {"name": "Lancaster Railway Station", "town": "Lancaster", "stop_type": "train"},
        "2500TAXI": {"name": "Lancaster Railway Station Taxi Rank", "town": "Lancaster", "stop_type": "bus"},
        "2500BUS": {"name": "Lancaster Bus Station Stand 3", "town": "Lancaster", "stop_type": "bus"},
    }

    atco = loader._resolve_tiploc_to_atco(tiploc_info, "LANCASTR", label_to_atcos, atco_meta)
    assert atco == "2500TRAIN"


def test_resolve_tiploc_penalizes_taxi_rank_when_no_train_type():
    loader = TrainLoader("postgresql://unused")

    tiploc_info = {
        "PRESTON": {
            "atco_code": "2500PRE",
        }
    }

    label_to_atcos = {
        "2500PRE": [
            "2500TAXI",
            "2500OTHER",
        ]
    }

    atco_meta = {
        "2500TAXI": {"name": "Preston Railway Station Taxi Rank", "town": "Preston", "stop_type": "other"},
        "2500OTHER": {"name": "Preston Railway Station", "town": "Preston", "stop_type": "other"},
    }

    atco = loader._resolve_tiploc_to_atco(tiploc_info, "PRESTON", label_to_atcos, atco_meta)
    assert atco == "2500OTHER"


def test_load_atco_lookup_indexes_atco_only(monkeypatch):
    class _FakeCursor:
        def execute(self, *_args, **_kwargs):
            return None

        def fetchall(self):
            return [
                ("2500PENNY", "Penny Street", "Lancaster", "train"),
            ]

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def close(self):
            return None

    monkeypatch.setattr(train_loader_module.psycopg, "connect", lambda *_args, **_kwargs: _FakeConn())

    loader = TrainLoader("postgresql://unused", atco_db_path="postgresql://unused")
    label_to_atcos, _atco_to_label, _atco_meta = loader._load_atco_lookup()

    assert "2500PENNY" in label_to_atcos
    assert "2500penny" in label_to_atcos
    assert "penny street" not in label_to_atcos
    assert "lancaster" not in label_to_atcos


def test_resolve_tiploc_does_not_fallback_to_name_matching(monkeypatch):
    class _FakeCursor:
        def execute(self, *_args, **_kwargs):
            return None

        def fetchall(self):
            return [
                ("2500LANR", "Lancaster Railway Station", "Lancaster", "other"),
                ("2500LANB", "Lancaster Bus Station", "Lancaster", "bus"),
            ]

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def close(self):
            return None

    monkeypatch.setattr(train_loader_module.psycopg, "connect", lambda *_args, **_kwargs: _FakeConn())

    loader = TrainLoader("postgresql://unused", atco_db_path="postgresql://unused")
    label_to_atcos, _atco_to_label, atco_meta = loader._load_atco_lookup()

    tiploc_info = {
        "LANCASTR": {
            "tps_description": "Lancaster",
            "description": "Lancaster",
        }
    }
    atco = loader._resolve_tiploc_to_atco(tiploc_info, "LANCASTR", label_to_atcos, atco_meta)
    assert atco is None


def test_download_schedule_today_skips_404(monkeypatch):
    loader = TrainLoader("postgresql://unused")

    def _raise_404(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            loader.schedule_url,
            404,
            "Not Found",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(train_loader_module.urllib.request, "urlopen", _raise_404)

    td = loader.download_schedule_today()
    assert len(td.route_stops) == 0
    assert len(td.journey_times) == 0


def test_download_schedule_today_skips_403(monkeypatch):
    loader = TrainLoader("postgresql://unused")

    def _raise_403(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            loader.schedule_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(train_loader_module.urllib.request, "urlopen", _raise_403)

    td = loader.download_schedule_today()
    assert len(td.route_stops) == 0
    assert len(td.journey_times) == 0
