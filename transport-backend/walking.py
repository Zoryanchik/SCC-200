class Walking:
    #return dict of walkable stops and walking time in seconds from start_point
    #use precomputed database( which should include walking / transfer in the same station as 3 min )
    def inter_walk( start_point ) -> dict:
        return {  point, walking_time }
    
    #given a location, return reachable stops by walking and correspoding walking time
    #use arbitrary distance or time limit e.g. 1.2 km / 10 min
    def reachable_stops( location ) -> dict:
        return { stop: walking_time }
    