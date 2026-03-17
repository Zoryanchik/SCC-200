def test_fragment_stitching_chains_along_route_stops(monkeypatch):
    """Unit-test the core fragment chaining semantics.

    The production bug class is: there's no direct fragment key for the leg endpoints,
    but there *is* a chain of consecutive fragments along the route's stop order.
    """

    route_stops = [0, 1, 2]
    link_map = {
        (0, 1): [(10.0, 10.0), (10.1, 10.1)],
        (1, 2): [(10.1, 10.1), (10.2, 10.2)],
    }

    fs = 0
    ts = 2
    assert (fs, ts) not in link_map

    i = route_stops.index(fs)
    j = route_stops.index(ts)
    step = 1 if j > i else -1
    stitched = []
    ok = True
    k = i
    while k != j:
        a = route_stops[k]
        b = route_stops[k + step]
        seg2 = link_map.get((a, b))
        if not seg2:
            rev2 = link_map.get((b, a))
            if rev2:
                seg2 = list(reversed(rev2))
        if not seg2:
            ok = False
            break
        if stitched and stitched[-1] == seg2[0]:
            stitched.extend(seg2[1:])
        else:
            stitched.extend(seg2)
        k += step

    assert ok
    assert len(stitched) == 3
    assert stitched[0] == (10.0, 10.0)
    assert stitched[-1] == (10.2, 10.2)
