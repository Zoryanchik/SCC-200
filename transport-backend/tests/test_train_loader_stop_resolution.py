import train_loader as train_loader_module
from train_loader import TrainLoader


def test_resolve_tiploc_prefers_train_station_over_taxi_rank():
    loader = TrainLoader("postgresql://unused")

    tiploc_info = {
        "LANCASTR": {
            "tps_description": "Lancaster Railway Station",
            "description": "Lancaster Railway Station",
        }
    }

    label_to_atcos = {
        "lancaster railway": [
            "2500TRAIN",
            "2500TAXI",
            "2500BUS",
        ],
        "lancaster railway station": [
            "2500TRAIN",
            "2500TAXI",
        ],
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
            "tps_description": "Preston Railway Station",
            "description": "Preston Railway Station",
        }
    }

    label_to_atcos = {
        "preston railway": [
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


def test_load_atco_lookup_does_not_index_town_alias(monkeypatch):
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

    # Stop name remains indexed...
    assert "penny street" in label_to_atcos
    # ...but town alias should not be indexed for train stop resolution.
    assert "lancaster" not in label_to_atcos


def test_load_atco_lookup_adds_relaxed_alias_for_rail_station_only(monkeypatch):
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

    assert "lancaster" in label_to_atcos
    assert "2500LANR" in label_to_atcos["lancaster"]
    assert "2500LANB" not in label_to_atcos["lancaster"]

    tiploc_info = {
        "LANCASTR": {
            "tps_description": "Lancaster",
            "description": "Lancaster",
        }
    }
    atco = loader._resolve_tiploc_to_atco(tiploc_info, "LANCASTR", label_to_atcos, atco_meta)
    assert atco == "2500LANR"
