from datetime import date, timedelta
import gzip
import io
import json

import train_loader as train_loader_module
from train_data import TrainData
from train_loader import TrainLoader


def test_load_traindata_for_non_today_cache_miss_returns_empty_without_today_fallback(monkeypatch):
    loader = TrainLoader("postgresql://unused")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    calls = {
        "download": 0,
        "parse": 0,
        "save": 0,
    }

    monkeypatch.setattr(loader, "create_schema", lambda: None)
    monkeypatch.setattr(loader, "_load_cached_traindata", lambda _d: None)

    # These should never be called for non-today cache misses.
    monkeypatch.setattr(loader, "download_schedule_raw", lambda: (calls.__setitem__("download", calls["download"] + 1) or b"fake"))

    def _fake_parse(data, target_date=None):
        calls["parse"] += 1
        return TrainData(num_routes=1, num_journeys=1, num_stops=2)

    monkeypatch.setattr(loader, "load_schedule_file", _fake_parse)

    def _save(_service_date, _train_data):
        calls["save"] += 1

    monkeypatch.setattr(loader, "_save_cached_traindata", _save)

    result = loader.load_traindata_for_date(tomorrow)

    assert isinstance(result, TrainData)
    assert len(result.route_stops) == 0
    assert len(result.journey_times) == 0
    assert calls["download"] == 0
    assert calls["parse"] == 0
    assert calls["save"] == 0


def test_load_traindata_for_today_empty_download_does_not_save(monkeypatch):
    loader = TrainLoader("postgresql://unused")
    today = date.today().isoformat()

    calls = {
        "download": 0,
        "parse": 0,
        "save": 0,
    }

    monkeypatch.setattr(loader, "create_schema", lambda: None)
    monkeypatch.setattr(loader, "_load_cached_traindata", lambda _d: None)
    monkeypatch.setattr(loader, "download_schedule_raw", lambda: (calls.__setitem__("download", calls["download"] + 1) or b"fake"))

    def _fake_parse(_data, target_date=None):
        calls["parse"] += 1
        return TrainData(num_routes=0, num_journeys=0, num_stops=0)

    monkeypatch.setattr(loader, "load_schedule_file", _fake_parse)

    def _save(_service_date, _train_data):
        calls["save"] += 1

    monkeypatch.setattr(loader, "_save_cached_traindata", _save)

    result = loader.load_traindata_for_date(today)

    assert isinstance(result, TrainData)
    assert len(result.route_stops) == 0
    assert len(result.journey_times) == 0
    assert calls["download"] == 1
    assert calls["parse"] == 1
    assert calls["save"] == 0


def test_save_cached_traindata_does_not_purge_other_dates(monkeypatch):
    class _FakeCursor:
        def __init__(self):
            self.calls = []

        def execute(self, query, params=None):
            self.calls.append((str(query), params))

        def executemany(self, query, rows):
            self.calls.append((str(query), rows))

    class _FakeConn:
        def __init__(self, cursor):
            self._cursor = cursor
            self.committed = False
            self.closed = False

        def cursor(self):
            return self._cursor

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    fake_cursor = _FakeCursor()
    fake_conn = _FakeConn(fake_cursor)

    monkeypatch.setattr(train_loader_module.psycopg, "connect", lambda *_args, **_kwargs: fake_conn)

    loader = TrainLoader("postgresql://unused")
    empty = TrainData(num_routes=0, num_journeys=0, num_stops=0)

    loader._save_cached_traindata(date.today().isoformat(), empty)

    retention_calls = [
        (query, params)
        for query, params in fake_cursor.calls
        if "WHERE service_date NOT IN" in query
    ]
    assert len(retention_calls) == 0
    assert fake_conn.committed is True
    assert fake_conn.closed is True


def test_load_schedule_file_groups_shared_route_pattern(monkeypatch):
    loader = TrainLoader("postgresql://unused")
    monkeypatch.setattr(loader, "_is_allowed_atco", lambda _code: True)

    monkeypatch.setattr(
        loader,
        "_load_atco_lookup",
        lambda: (
            {
                "2500PRST0": ["2500PRST0"],
                "2600WIGN0": ["2600WIGN0"],
                "2800LVRPLSH0": ["2800LVRPLSH0"],
            },
            {
                "2500PRST0": "Preston Rail Station",
                "2600WIGN0": "Wigan Rail Station",
                "2800LVRPLSH0": "Liverpool Lime Street Rail Station",
            },
            {
                "2500PRST0": {"name": "Preston Rail Station", "stop_type": "train"},
                "2600WIGN0": {"name": "Wigan Rail Station", "stop_type": "train"},
                "2800LVRPLSH0": {"name": "Liverpool Lime Street Rail Station", "stop_type": "train"},
            },
        ),
    )

    records = [
    {"TiplocV1": {"tiploc_code": "PRESTON", "atco_code": "2500PRST0", "tps_description": "Preston"}},
    {"TiplocV1": {"tiploc_code": "WIGAN", "atco_code": "2600WIGN0", "tps_description": "Wigan"}},
    {"TiplocV1": {"tiploc_code": "LIVLIME", "atco_code": "2800LVRPLSH0", "tps_description": "Liverpool Lime Street"}},
        {
            "JsonScheduleV1": {
                "transaction_type": "Create",
                "CIF_train_uid": "U1",
                "schedule_start_date": "2026-04-01",
                "schedule_end_date": "2026-06-01",
                "schedule_segment": {
                    "CIF_headcode": "1A00",
                    "CIF_train_service_code": "21730001",
                    "schedule_location": [
                        {"tiploc_code": "PRESTON", "departure": "0800"},
                        {"tiploc_code": "WIGAN", "pass": "0820"},
                        {"tiploc_code": "LIVLIME", "arrival": "0850"},
                    ],
                },
            }
        },
        {
            "JsonScheduleV1": {
                "transaction_type": "Create",
                "CIF_train_uid": "U2",
                "schedule_start_date": "2026-04-01",
                "schedule_end_date": "2026-06-01",
                "schedule_segment": {
                    "CIF_headcode": "1A01",
                    "CIF_train_service_code": "21730002",
                    "schedule_location": [
                        {"tiploc_code": "PRESTON", "departure": "0900"},
                        {"tiploc_code": "WIGAN", "pass": "0920"},
                        {"tiploc_code": "LIVLIME", "arrival": "0950"},
                    ],
                },
            }
        },
    ]

    raw = "\n".join(json.dumps(r) for r in records).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(raw)

    td = loader.load_schedule_file(buf.getvalue())

    assert len(td.route_stops) == 1
    assert len(td.route_journeys) == 1
    assert len(td.route_journeys[0]) == 2
    assert len(td.journey_times) == 2


def test_load_schedule_file_skips_unmapped_intermediate_tiploc(monkeypatch):
    loader = TrainLoader("postgresql://unused")
    monkeypatch.setattr(loader, "_is_allowed_atco", lambda _code: True)

    monkeypatch.setattr(
        loader,
        "_load_atco_lookup",
        lambda: (
            {
                "2500PRST0": ["2500PRST0"],
                "2600WIGN0": ["2600WIGN0"],
                "2800LVRPLSH0": ["2800LVRPLSH0"],
            },
            {
                "2500PRST0": "Preston Rail Station",
                "2600WIGN0": "Wigan Rail Station",
                "2800LVRPLSH0": "Liverpool Lime Street Rail Station",
            },
            {
                "2500PRST0": {"name": "Preston Rail Station", "stop_type": "train"},
                "2600WIGN0": {"name": "Wigan Rail Station", "stop_type": "train"},
                "2800LVRPLSH0": {"name": "Liverpool Lime Street Rail Station", "stop_type": "train"},
            },
        ),
    )

    records = [
    {"TiplocV1": {"tiploc_code": "PRESTON", "atco_code": "2500PRST0", "tps_description": "Preston"}},
    {"TiplocV1": {"tiploc_code": "WIGAN", "atco_code": "2600WIGN0", "tps_description": "Wigan"}},
    {"TiplocV1": {"tiploc_code": "LIVLIME", "atco_code": "2800LVRPLSH0", "tps_description": "Liverpool Lime Street"}},
        {"TiplocV1": {"tiploc_code": "XJUNC", "tps_description": "Unknown Junction"}},
        {
            "JsonScheduleV1": {
                "transaction_type": "Create",
                "CIF_train_uid": "U3",
                "schedule_start_date": "2026-04-01",
                "schedule_end_date": "2026-06-01",
                "schedule_segment": {
                    "CIF_headcode": "1A02",
                    "CIF_train_service_code": "21730003",
                    "schedule_location": [
                        {"tiploc_code": "PRESTON", "departure": "0800"},
                        {"tiploc_code": "XJUNC", "pass": "0810"},
                        {"tiploc_code": "WIGAN", "arrival": "0820", "departure": "0821"},
                        {"tiploc_code": "LIVLIME", "arrival": "0850"},
                    ],
                },
            }
        },
    ]

    raw = "\n".join(json.dumps(r) for r in records).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(raw)

    td = loader.load_schedule_file(buf.getvalue())

    assert len(td.route_stops) == 1
    assert len(td.journey_times) == 1
    assert len(td.route_stops[0]) == 3


def test_load_schedule_file_filters_by_target_operational_day(monkeypatch):
    loader = TrainLoader("postgresql://unused")
    monkeypatch.setattr(loader, "_is_allowed_atco", lambda _code: True)

    monkeypatch.setattr(
        loader,
        "_load_atco_lookup",
        lambda: (
            {
                "2500PRST0": ["2500PRST0"],
                "2600WIGN0": ["2600WIGN0"],
            },
            {
                "2500PRST0": "Preston Rail Station",
                "2600WIGN0": "Wigan Rail Station",
            },
            {
                "2500PRST0": {"name": "Preston Rail Station", "stop_type": "train"},
                "2600WIGN0": {"name": "Wigan Rail Station", "stop_type": "train"},
            },
        ),
    )

    records = [
    {"TiplocV1": {"tiploc_code": "PRESTON", "atco_code": "2500PRST0", "tps_description": "Preston"}},
    {"TiplocV1": {"tiploc_code": "WIGAN", "atco_code": "2600WIGN0", "tps_description": "Wigan"}},
        {
            "JsonScheduleV1": {
                "transaction_type": "Create",
                "CIF_train_uid": "MON_ONLY",
                "schedule_start_date": "2026-04-01",
                "schedule_end_date": "2026-06-01",
                "schedule_days_runs": "1000000",
                "schedule_segment": {
                    "CIF_headcode": "1A10",
                    "CIF_train_service_code": "21731000",
                    "schedule_location": [
                        {"tiploc_code": "PRESTON", "departure": "0800"},
                        {"tiploc_code": "WIGAN", "arrival": "0820"},
                    ],
                },
            }
        },
        {
            "JsonScheduleV1": {
                "transaction_type": "Create",
                "CIF_train_uid": "TUE_ONLY",
                "schedule_start_date": "2026-04-01",
                "schedule_end_date": "2026-06-01",
                "schedule_days_runs": "0100000",
                "schedule_segment": {
                    "CIF_headcode": "1A11",
                    "CIF_train_service_code": "21731001",
                    "schedule_location": [
                        {"tiploc_code": "PRESTON", "departure": "0900"},
                        {"tiploc_code": "WIGAN", "arrival": "0920"},
                    ],
                },
            }
        },
    ]

    raw = "\n".join(json.dumps(r) for r in records).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(raw)

    # 2026-04-21 is Tuesday, so only TUE_ONLY should pass.
    td = loader.load_schedule_file(buf.getvalue(), target_date="2026-04-21")

    assert len(td.route_stops) == 1
    assert len(td.journey_times) == 1
    assert len(td.journey_metadata) >= 1
    assert td.journey_metadata[0]["train_uid"] == "TUE_ONLY"


def test_route_stays_in_nw_accepts_when_any_stop_allowed(monkeypatch):
    loader = TrainLoader("postgresql://unused")

    # Only one stop prefix is allowed in this synthetic scenario.
    monkeypatch.setattr(loader, "_is_allowed_atco", lambda code: str(code).startswith("250"))

    assert loader._route_stays_in_nw(["2500PRST0", "9990OTHER0"]) is True
    assert loader._route_stays_in_nw(["9990A", "9990B"]) is False
