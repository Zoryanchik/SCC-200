from train_loader import TrainLoader


def test_normalize_journey_times_rolls_over_midnight():
    arrivals = [
        ("STOP_A", 23 * 3600 + 55 * 60),
        ("STOP_B", 5 * 60),
        ("STOP_C", 20 * 60),
    ]
    departures = [
        23 * 3600 + 56 * 60,
        6 * 60,
        21 * 60,
    ]

    norm_arrivals, norm_departures = TrainLoader._normalize_journey_times(arrivals, departures)

    assert norm_arrivals[0][1] == 23 * 3600 + 55 * 60
    assert norm_arrivals[1][1] == 86400 + 5 * 60
    assert norm_arrivals[2][1] == 86400 + 20 * 60

    assert norm_departures[0] == 23 * 3600 + 56 * 60
    assert norm_departures[1] == 86400 + 6 * 60
    assert norm_departures[2] == 86400 + 21 * 60


def test_normalize_journey_times_keeps_departure_after_arrival():
    arrivals = [
        ("STOP_A", 86340),
        ("STOP_B", 86370),
    ]
    departures = [
        86350,
        86360,
    ]

    norm_arrivals, norm_departures = TrainLoader._normalize_journey_times(arrivals, departures)

    assert norm_departures[1] >= norm_arrivals[1][1]
    assert norm_departures[1] == 86360 + 86400
