/**
 * Transport API Service
 * Handles all API calls to the transport backend
 */

const API_BASE_URL = 'https://transport.scc.lancs.ac.uk';

/**
 * Fetch live bus locations for a given operator
 * @param {string} operatorCode - The operator code (e.g., 'stagecoach')
 * @returns {Promise<Array>} Array of bus location data
 */
export const fetchLiveBusLocations = async (operatorCode) => {
  try {
    const response = await fetch(
      `${API_BASE_URL}/bus/live/${operatorCode}`
    );
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error('Error fetching bus locations:', error);
    throw error;
  }
};

/**
 * Fetch rail departures for a given station
 * @param {string} stationCode - The CRS code for the station (e.g., 'LAN')
 * @returns {Promise<Array>} Array of departure data
 */
export const fetchRailDepartures = async (stationCode) => {
  try {
    const response = await fetch(
      `${API_BASE_URL}/rail/departures/${stationCode}`
    );
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error('Error fetching rail departures:', error);
    throw error;
  }
};

/**
 * Fetch real-time bus arrivals at a specific stop
 * @param {string} stopCode - The NaPTAN stop code
 * @returns {Promise<Array>} Array of arrival data
 */
export const fetchBusArrivals = async (stopCode) => {
  try {
    const response = await fetch(
      `${API_BASE_URL}/bus/arrivals/${stopCode}`
    );
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error('Error fetching bus arrivals:', error);
    throw error;
  }
};

/**
 * Search for stops by name using NaPTAN data
 * @param {string} query - Search query
 * @returns {Promise<Array>} Array of matching stops
 */
export const searchStops = async (query) => {
  try {
    const response = await fetch(
      `${API_BASE_URL}/search/stops?q=${encodeURIComponent(query)}`
    );
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error('Error searching stops:', error);
    throw error;
  }
};

/**
 * Get journey planner results
 * @param {string} fromStop - Origin stop code
 * @param {string} toStop - Destination stop code
 * @param {string} departureTime - Departure time in ISO format
 * @returns {Promise<Array>} Array of journey options
 */
export const getJourneyPlans = async (fromStop, toStop, departureTime) => {
  try {
    const response = await fetch(
      `${API_BASE_URL}/journey/plan`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          fromStop,
          toStop,
          departureTime
        })
      }
    );
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error('Error getting journey plans:', error);
    throw error;
  }
};

/**
 * Get service alerts
 * @returns {Promise<Array>} Array of service alerts
 */
export const fetchServiceAlerts = async () => {
  try {
    const response = await fetch(`${API_BASE_URL}/alerts`);
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error('Error fetching service alerts:', error);
    throw error;
  }
};

/**
 * Get pricing information
 * @param {string} fromStop - Origin stop code
 * @param {string} toStop - Destination stop code
 * @returns {Promise<Object>} Pricing data
 */
export const fetchPricing = async (fromStop, toStop) => {
  try {
    const response = await fetch(
      `${API_BASE_URL}/pricing?from=${fromStop}&to=${toStop}`
    );
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error('Error fetching pricing:', error);
    throw error;
  }
};
