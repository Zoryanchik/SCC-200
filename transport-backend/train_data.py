class TrainData:
	def __init__( self, num_routes:int=0, num_journeys:int=0, num_stops:int=0 ):
		self.route_stops = [ [] for _ in range( num_routes ) ]
		self.route_journeys = [ [] for _ in range( num_routes ) ]
		self.journey_times = [ [ () ] for _ in range( num_journeys ) ]
		self.stop_to_routes = [ [] for _ in range( num_stops ) ]
		self.journey_to_route = [ int ]

	def get_routes( self, atco_code_int ) -> list:
		return self.stop_to_routes[ atco_code_int]

	def get_journey( self, route, date, time, atco_code: str ) -> tuple:
		return ( None, "train" )

	def get_journey_detail( self, type, journey, hop_on_Point, hop_off_point ) -> dict:
		return { 'vehicle': "", 'origin': "", 'destination': "", 'hop_on_point': None, 'hop_off_point': None }
