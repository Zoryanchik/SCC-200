import pytest
from bus_loader import BusLoader


def mk(seq):
    """Helper to build (code, time) tuples from lists."""
    return [(s, t) for s, t in seq]


def test_merge_heuristic_append_no_shift():
    # Reload module to avoid cross-test monkeypatch interference
    import importlib.util, pathlib
    path = str(pathlib.Path(__file__).resolve().parents[1] / 'bus_loader.py')
    spec = importlib.util.spec_from_file_location('fresh_bus_loader', path)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    bl = fresh.BusLoader(db_path='')
    existing = mk([('A', 100), ('B', 200)])
    new = mk([('B', 300), ('C', 400)])
    merged = bl._try_merge_journeys(existing, new)
    assert merged == [('A', 100), ('B', 200), ('C', 400)]


def test_merge_heuristic_append_with_shift():
    # Reload module to avoid cross-test monkeypatch interference
    import importlib.util, pathlib
    path = str(pathlib.Path(__file__).resolve().parents[1] / 'bus_loader.py')
    spec = importlib.util.spec_from_file_location('fresh_bus_loader', path)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    bl = fresh.BusLoader(db_path='')
    existing = mk([('A', 100), ('B', 500)])
    new = mk([('B', 300), ('C', 400)])
    merged = bl._try_merge_journeys(existing, new)
    # new starts at 300, existing ends at 500 -> shift = 500-300+60 = 260
    assert merged[0] == ('A', 100)
    assert merged[1] == ('B', 500)
    # C time should be 400 + 260 = 660
    assert merged[2] == ('C', 660)


def test_merge_heuristic_prefix_prefer_longer():
    # Reload module to avoid cross-test monkeypatch interference
    import importlib.util, pathlib
    path = str(pathlib.Path(__file__).resolve().parents[1] / 'bus_loader.py')
    spec = importlib.util.spec_from_file_location('fresh_bus_loader', path)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    bl = fresh.BusLoader(db_path='')
    # identical stop codes but different times -> prefer existing (keeps longer/first-seen)
    existing = mk([('A', 100), ('B', 200), ('C', 300)])
    new = mk([('A', 120), ('B', 220), ('C', 320)])
    merged = bl._try_merge_journeys(existing, new)
    assert merged == existing


def test_merge_heuristic_reverse_concat():
    # Reload module to avoid cross-test monkeypatch interference
    import importlib.util, pathlib
    path = str(pathlib.Path(__file__).resolve().parents[1] / 'bus_loader.py')
    spec = importlib.util.spec_from_file_location('fresh_bus_loader', path)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    bl = fresh.BusLoader(db_path='')
    new = mk([('X', 100), ('A', 150)])
    existing = mk([('A', 200), ('B', 300)])
    merged = bl._try_merge_journeys(existing, new)
    # merged = new + existing[1:]
    assert merged == [('X', 100), ('A', 150), ('B', 300)]
