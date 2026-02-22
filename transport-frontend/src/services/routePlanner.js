/**
 * RAPTOR (Round-based Public Transit Optimized Router)
 * Implementation for finding optimal routes with transfers
 */

export class RAPTORRouter {
  /**
   * Initialize the RAPTOR router with timetable data
   * @param {Object} timetableData - Contains stops, routes, and timetable information
   */
  constructor(timetableData) {
    this.stops = timetableData.stops || [];
    this.routes = timetableData.routes || [];
    this.stopTimes = timetableData.stopTimes || [];
    this.transfers = timetableData.transfers || [];
  }

  /**
   * Find optimal route(s) between two stops
   * @param {string} fromStopId - Origin stop ID
   * @param {string} toStopId - Destination stop ID
   * @param {number} departureTime - Departure time in seconds since midnight
   * @param {number} maxTransfers - Maximum number of transfers (default: 3)
   * @returns {Array<Object>} Array of route options, sorted by duration
   */
  findRoute(fromStopId, toStopId, departureTime, maxTransfers = 3) {
    const routes = [];

    // Initialize arrival times for all stops
    const arrivalTimes = new Map();
    arrivalTimes.set(fromStopId, departureTime);

    // RAPTOR algorithm: iterate through rounds (transfers)
    for (let round = 0; round <= maxTransfers; round++) {
      const routeUpdates = new Map();

      // For each route, find the earliest boarding
      for (const route of this.routes) {
        const stopSequence = route.stopSequence || [];
        let earliestBoardingIndex = -1;
        let earliestBoardingTime = Infinity;

        // Find earliest boarding on this route
        for (let i = 0; i < stopSequence.length; i++) {
          const stopId = stopSequence[i];
          if (arrivalTimes.has(stopId)) {
            const arrival = arrivalTimes.get(stopId);
            // Find next departure on this route after arrival
            const nextDeparture = this.findNextDeparture(
              route.id,
              stopId,
              arrival
            );
            if (nextDeparture && nextDeparture.time < earliestBoardingTime) {
              earliestBoardingTime = nextDeparture.time;
              earliestBoardingIndex = i;
            }
          }
        }

        // If we can board this route, update arrival times
        if (earliestBoardingIndex >= 0) {
          for (let i = earliestBoardingIndex + 1; i < stopSequence.length; i++) {
            const stopId = stopSequence[i];
            const arrivalTime = this.getArrivalTime(
              route.id,
              stopId,
              earliestBoardingTime
            );
            if (arrivalTime && (!arrivalTimes.has(stopId) || arrivalTimes.get(stopId) > arrivalTime)) {
              routeUpdates.set(stopId, arrivalTime);
            }
          }
        }
      }

      // Apply route updates
      if (routeUpdates.size === 0) break; // No improvements in this round
      routeUpdates.forEach((time, stopId) => arrivalTimes.set(stopId, time));

      // Check if we reached the destination
      if (arrivalTimes.has(toStopId)) {
        const finalArrival = arrivalTimes.get(toStopId);
        routes.push({
          transfers: round,
          departureTime: this.formatTime(departureTime),
          arrivalTime: this.formatTime(finalArrival),
          duration: finalArrival - departureTime,
          durationMinutes: Math.floor((finalArrival - departureTime) / 60)
        });
      }
    }

    // Sort by duration and return top 5 options
    return routes
      .sort((a, b) => a.duration - b.duration)
      .slice(0, 5);
  }

  /**
   * Find the next departure on a route from a given stop after a certain time
   * @private
   */
  findNextDeparture(routeId, stopId, afterTime) {
    const relevantStopTimes = this.stopTimes.filter(
      st => st.routeId === routeId && st.stopId === stopId && st.departureTime >= afterTime
    );

    if (relevantStopTimes.length === 0) return null;

    return relevantStopTimes.reduce((earliest, current) =>
      current.departureTime < earliest.departureTime ? current : earliest
    );
  }

  /**
   * Get arrival time at a stop for a given route and boarding time
   * @private
   */
  getArrivalTime(routeId, stopId, boardingTime) {
    const stopTime = this.stopTimes.find(
      st => st.routeId === routeId && st.stopId === stopId && st.departureTime >= boardingTime
    );
    return stopTime ? stopTime.arrivalTime : null;
  }

  /**
   * Format time from seconds to HH:MM
   * @private
   */
  formatTime(seconds) {
    const hours = Math.floor(seconds / 3600) % 24;
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
  }

  /**
   * Calculate footpath transfers between nearby stops
   * @param {number} maxDistance - Maximum walking distance in meters
   * @returns {Array<Object>} Array of transfer opportunities
   */
  calculateTransfers(maxDistance = 500) {
    const transfers = [];

    for (let i = 0; i < this.stops.length; i++) {
      for (let j = i + 1; j < this.stops.length; j++) {
        const distance = this.calculateDistance(
          this.stops[i].lat,
          this.stops[i].lon,
          this.stops[j].lat,
          this.stops[j].lon
        );

        if (distance <= maxDistance) {
          const walkingTime = Math.ceil(distance / 1.4); // ~1.4 m/s walking speed
          transfers.push({
            fromStopId: this.stops[i].id,
            toStopId: this.stops[j].id,
            distance,
            walkingTime
          });
        }
      }
    }

    return transfers;
  }

  /**
   * Calculate distance between two coordinates (Haversine formula)
   * @private
   */
  calculateDistance(lat1, lon1, lat2, lon2) {
    const R = 6371000; // Earth radius in meters
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon / 2) * Math.sin(dLon / 2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
  }
}

export default RAPTORRouter;
