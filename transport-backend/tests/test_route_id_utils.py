"""Unit tests for route_id_utils.resolve_prefixed_route_id.

These tests mock a minimal DB connection/cursor interface so they can
run fast without a real Postgres instance.
"""
from route_id_utils import resolve_prefixed_route_id


class FakeCursor:
    def __init__(self, responses):
        # responses is an iterator/list of values to return from fetchone()
        self._responses = list(responses)
        self.last_query = None
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_query = sql
        self.last_params = params

    def fetchone(self):
        if not self._responses:
            return None
        return self._responses.pop(0)


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_returns_input_if_already_prefixed():
    # If input already contains '::' it should be returned unchanged and
    # no DB queries should be executed.
    pref = "FILE::PC000:1:RS1"
    # cursor won't be used but supply one anyway
    c = FakeCursor([])
    conn = FakeConn(c)
    out = resolve_prefixed_route_id(conn, pref)
    assert out == pref


def test_finds_candidate_in_section_tracks():
    # Simulate section_tracks returning a prefixed candidate on first query
    candidate = ("FILE::PC000:1:RS1",)
    cursor = FakeCursor([candidate])
    conn = FakeConn(cursor)
    out = resolve_prefixed_route_id(conn, "PC000:1:RS1", prefer_section=True)
    assert out == candidate[0]
    # ensure the query looked for LIKE '%::PC000:1:RS1'
    assert "%::PC000:1:RS1" in cursor.last_params[0]


def test_falls_back_to_route_tracks_when_no_section_candidate():
    # First fetchone returns None (no section match), second returns candidate
    candidate = ("OTHERFILE::PC000:1:RS1",)
    cursor = FakeCursor([None, candidate])
    conn = FakeConn(cursor)
    out = resolve_prefixed_route_id(conn, "PC000:1:RS1", prefer_section=True)
    assert out == candidate[0]


def test_returns_original_when_no_candidate():
    cursor = FakeCursor([None, None])
    conn = FakeConn(cursor)
    out = resolve_prefixed_route_id(conn, "PC000:1:RS1", prefer_section=True)
    assert out == "PC000:1:RS1"
