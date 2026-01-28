from raptorRouter import RaptorRouter
from timetable import Timetable

class DummyTimetable(Timetable):
    def __init__(self):
        super().__init__("bus")
        self.stops = ["A", "B", "C"]
        self.routes = {"A": ["R1"], "B": ["R2"], "C": []}
        self.journeys = {
            "R1": [("J1", "bus")],
            "R2": [("J2", "bus")]
        }
        self.arrival = {
            ("J1", "A"): {"B": 100},
            ("J2", "B"): {"C": 200}
        }

    def get_stops(self):
        return self.stops

    def get_routes(self, point):
        return self.routes.get(point, [])

    def get_journey(self, route, time, point):
        return self.journeys[route][0]

    def arrival_time(self, journey, point):
        return self.arrival.get((journey[0], point), {})

def test_simple_route():
    router = RaptorRouter()
    timetable = DummyTimetable()
    result = router.route(2, timetable, 0, "A", "C")
    print(result)
    assert "C" in result
    assert result["C"]["prev_stop"] == "B"

if __name__ == "__main__":
    test_simple_route()
    print("Test passed!")
