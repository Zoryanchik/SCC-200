class Timetable:
    #type: bus, train, combined
    type: str

    def __init__( self, type ):
        self.type = type

    #get a list of routes passing by the stopPoint
    def getRoutes( stopPoint ):
        return []

    #get the first journey passing by the stoppoint on the route after the time
    def getVehicle( route, time, stopPoint):
        return journey

    #get arrivalTime for each stopPoint on the journey after the given stopPoint
    def arrivalTime( journey, stopPoint ):
        return { stopPoint: arrivalTime }

    #get journey details for a specific journey
    def getJourneyDetail( type, journey, hopOnPoint, hopOffPoint ):
        return { vehicle: "", origin: "", destination: "", hopOnPoint: time, hopOffPoint: time }