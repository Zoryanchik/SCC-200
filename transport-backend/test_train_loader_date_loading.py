from datetime import date, timedelta

import train_loader as train_loader_module
from train_data import TrainData
from train_loader import TrainLoader


def test_load_traindata_for_non_today_cache_miss_returns_empty_without_today_fallback(monkeypatch):
    loader = TrainLoader("postgresql://unused")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    calls = {
        "download": 0,
        "save": 0,
    }

    monkeypatch.setattr(loader, "create_schema", lambda: None)
    monkeypatch.setattr(loader, "_load_cached_traindata", lambda _d: None)
    monkeypatch.setattr(
        loader,
        "download_schedule_today",
        lambda: calls.__setitem__("download", calls["download"] + 1),
    )

    def _save(_service_date, _train_data):
        calls["save"] += 1

    monkeypatch.setattr(loader, "_save_cached_traindata", _save)

    result = loader.load_traindata_for_date(tomorrow)

    assert isinstance(result, TrainData)
    assert len(result.route_stops) == 0
    assert len(result.journey_times) == 0
    assert calls["download"] == 0
    assert calls["save"] == 0


def test_save_cached_traindata_retains_yesterday_today_tomorrow(monkeypatch):
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
    assert len(retention_calls) == 1

    _, params = retention_calls[0]
    assert params is not None
    assert len(params) == 3
    assert (date.today() - timedelta(days=1)).isoformat() in params
    assert date.today().isoformat() in params
    assert (date.today() + timedelta(days=1)).isoformat() in params
    assert fake_conn.committed is True
    assert fake_conn.closed is True
