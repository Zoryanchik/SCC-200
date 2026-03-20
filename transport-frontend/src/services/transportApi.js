/**
 * Transport API Service
 * Handles all API calls to the transport backend
 * Based on Lancaster University transport API feeds
 *
 * API_BASE_URL is read from the VITE_API_BASE_URL environment variable.
 * Defaults to http://localhost:5050 for local development.
 * Set via .env, .env.production, or .env.local (see .env.example).
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5050';

const normalizeStopLocation = (stop) => {
  if (!stop || typeof stop !== 'object') return null;
  const lat = stop.lat ?? stop.latitude;
  const lon = stop.lon ?? stop.longitude;
  if (typeof lat !== 'number' || typeof lon !== 'number') return null;
  return { lat, lon };
};

const normalizeDateTime = (input) => {
  const dateValue = input ? new Date(input) : new Date();
  const date = dateValue.toISOString().slice(0, 10);
  const time = dateValue.toTimeString().slice(0, 8);
  return { date, time };
};

// Augment a backend journey-plan response with user-friendly display fields
// (departure_time_with_offset, arrival_time_with_offset, realtime_*/scheduled_* with offsets,
// and ISO datetimes). This mirrors the augmentation done in getJourneyPlans so
// compareRouters responses are consumable by the UI.
const _makeWithOffset = (timeStr, dayOffset) => {
  if (!timeStr) return null;
  const offset = Number.isFinite(dayOffset) && dayOffset > 0 ? ` (+${dayOffset}d)` : "";
  return `${timeStr}${offset}`;
};

const augmentJourneyResponse = (resp, requestDate) => {
  if (!resp || !Array.isArray(resp.legs)) return resp;
  try {
    resp.legs = (resp.legs || []).map((leg) => {
      const copy = { ...leg };

      copy.departure_time_with_offset = _makeWithOffset(copy.departure_time, copy.departure_day_offset);
      copy.arrival_time_with_offset = _makeWithOffset(copy.arrival_time, copy.arrival_day_offset);

      if (copy.realtime_departure_time) {
        copy.realtime_departure_time_with_offset = _makeWithOffset(copy.realtime_departure_time, copy.departure_day_offset);
      }
      if (copy.realtime_arrival_time) {
        copy.realtime_arrival_time_with_offset = _makeWithOffset(copy.realtime_arrival_time, copy.arrival_day_offset);
      }

      // ISO datetimes
      if (copy.arrival_time) {
        try {
          const [h, m, s] = copy.arrival_time.split(':').map((n) => parseInt(n, 10));
          if (!Number.isNaN(h) && !Number.isNaN(m) && !Number.isNaN(s)) {
            const dt = new Date(`${requestDate}T${copy.arrival_time}Z`);
            if (copy.arrival_day_offset && Number.isFinite(copy.arrival_day_offset) && copy.arrival_day_offset > 0) {
              dt.setUTCDate(dt.getUTCDate() + copy.arrival_day_offset);
            }
            copy.arrival_datetime_iso = dt.toISOString();
          }
        } catch (e) {
          // ignore
        }
      }
      if (copy.departure_time) {
        try {
          const dt2 = new Date(`${requestDate}T${copy.departure_time}Z`);
          if (copy.departure_day_offset && Number.isFinite(copy.departure_day_offset) && copy.departure_day_offset > 0) {
            dt2.setUTCDate(dt2.getUTCDate() + copy.departure_day_offset);
          }
          copy.departure_datetime_iso = dt2.toISOString();
        } catch (e) {
          // ignore
        }
      }

      // Normalise mode -> type for UI compatibility
      if (copy.mode !== undefined && copy.type === undefined) {
        copy.type = copy.mode === 'walking' ? 'walk' : copy.mode;
      }

      return copy;
    });
  } catch (e) {
    // ignore augmentation failures
  }
  return resp;
};

/**
 * Fetch bus times for a specific stop
 * @param {string} stopCode - The stop code (e.g., '2800S12345')
 * @returns {Promise<Array>} Array of bus time data
 */
export const fetchBusTimes = async (stopCode) => {
  try {
    const response = await fetch(
      `${API_BASE_URL}/bus/times/${stopCode}`
    );
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error('Error fetching bus times:', error);
    throw error;
  }
};

/**
 * Fetch live bus locations for a given operator, filtered by map center.
 * @param {string} operatorCode - The operator code (e.g., 'SCCU')
 * @param {Object} options - Optional lat/lon/latTol/lonTol for geo-filtering
 * @param {number} [options.lat] - Center latitude
 * @param {number} [options.lon] - Center longitude
 * @param {number} [options.latTol=0.0002] - Latitude tolerance (half-width)
 * @param {number} [options.lonTol=0.0002] - Longitude tolerance (half-width)
 * @returns {Promise<Array>} Array of bus location data
 */
export const fetchLiveBusLocations = async (operatorCode, { lat, lon, latTol = 0.07, lonTol = 0.07 } = {}) => {
  try {
    let url = `${API_BASE_URL}/bus/live/${operatorCode}`;
    if (typeof lat === 'number' && typeof lon === 'number') {
      const params = new URLSearchParams({
        lat: String(lat),
        lon: String(lon),
        latTol: String(latTol),
        lonTol: String(lonTol),
      });
      url += `?${params.toString()}`;
    }
    const response = await fetch(url);
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
export const searchStops = async (query, { lat, lon } = {}) => {
  try {
    const params = new URLSearchParams({
      q: String(query ?? ''),
      // Default autocomplete limit. Kept small for consistent test expectations.
      limit: '5',
    });
    if (typeof lat === 'number' && Number.isFinite(lat)) params.set('lat', String(lat));
    if (typeof lon === 'number' && Number.isFinite(lon)) params.set('lon', String(lon));
    const response = await fetch(`${API_BASE_URL}/search/stops?${params.toString()}`);
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
export const getJourneyPlans = async (fromStop, toStop, departureTime, options = {}) => {
  try {
    const from = normalizeStopLocation(fromStop);
    const to = normalizeStopLocation(toStop);
    if (!from || !to) {
      throw new Error('fromStop and toStop must include lat/lon');
    }
    const { date, time } = normalizeDateTime(departureTime);
  const maxTransfers = typeof options.maxTransfers === 'number' ? options.maxTransfers : 3;
    const mode = typeof options.mode === 'string' ? options.mode : 'combined';
    const response = await fetch(
      `${API_BASE_URL}/journey/plan`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          fromStop: from,
          toStop: to,
          departureTime: time,
          date,
          maxTransfers,
          mode
        })
      }
    );
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    const responseJson = await response.json();
    // Optionally preserve the raw backend response for debugging
    if (options.includeRaw) {
      try {
        // Prefer structuredClone when available because it's safer and
        // won't invoke toJSON/getters in the same way as JSON.stringify.
        if (typeof structuredClone === 'function') {
          responseJson._raw = structuredClone(responseJson);
        } else {
          responseJson._raw = JSON.parse(JSON.stringify(responseJson));
        }
      } catch (e) {
        // Fall back to a shallow copy if deep cloning fails (avoids
        // attempting to access problematic getters that may throw).
        try {
          responseJson._raw = Array.isArray(responseJson) ? responseJson.slice() : Object.assign({}, responseJson);
        } catch (e2) {
          responseJson._raw = null;
        }
      }
    }
    // Ensure that legs, meta, and routeGeometrics !== null | undefined
    responseJson.legs ??= [];
    responseJson.meta ??= {};
    responseJson.routeGeometries ??= [];

    // Augment legs with user-friendly display strings that include day offsets
    // Backend may return arrival_day_offset / departure_day_offset (integers >= 0).
    // Preserve original arrival_time / departure_time strings for backward-compatibility.
    try {
      const requestDate = date; // YYYY-MM-DD from earlier normalizeDateTime
      responseJson.legs = (responseJson.legs || []).map((leg) => {
        const copy = { ...leg };

        // Normalise backend 'mode' field → frontend 'type' field.
        // api.py build_journey_plan_response() uses key "mode" with values
        // "walking" | "bus" | "train".  UI components (RouteCard, home-page)
        // read "type" with values "walk" | "bus" | "train".
        // We derive 'type' from 'mode' here so both fields are available.
        if (copy.mode !== undefined && copy.type === undefined) {
          copy.type = copy.mode === 'walking' ? 'walk' : copy.mode;
        }

        const makeWithOffset = (timeStr, dayOffset) => {
          if (!timeStr) return null;
          const offset = Number.isFinite(dayOffset) && dayOffset > 0 ? ` (+${dayOffset}d)` : "";
          return `${timeStr}${offset}`;
        };

        // Add friendly combined fields used by UI components. These are additive and
        // won't break callers that expect the original field names.
        copy.arrival_time_with_offset = makeWithOffset(copy.arrival_time, copy.arrival_day_offset);
        copy.departure_time_with_offset = makeWithOffset(copy.departure_time, copy.departure_day_offset);

        // Real-time fields: if the backend provides realtime_departure_time /
        // realtime_arrival_time (bus legs with live delay), expose them with
        // day-offset variants too.  These are separate from the scheduled
        // (planned) times.
        if (copy.realtime_departure_time) {
          copy.realtime_departure_time_with_offset = makeWithOffset(
            copy.realtime_departure_time, copy.departure_day_offset);
        }
        if (copy.realtime_arrival_time) {
          copy.realtime_arrival_time_with_offset = makeWithOffset(
            copy.realtime_arrival_time, copy.arrival_day_offset);
        }

        // Optional: also expose ISO datetimes computed from the requested date.
        // Only add when both date and time exist. These are in UTC-ish ISO format
        // and may need timezone-adjustment depending on app needs.
        if (copy.arrival_time) {
          try {
            const [h, m, s] = copy.arrival_time.split(':').map((n) => parseInt(n, 10));
            if (!Number.isNaN(h) && !Number.isNaN(m) && !Number.isNaN(s)) {
              const dt = new Date(`${requestDate}T${copy.arrival_time}Z`);
              if (copy.arrival_day_offset && Number.isFinite(copy.arrival_day_offset) && copy.arrival_day_offset > 0) {
                dt.setUTCDate(dt.getUTCDate() + copy.arrival_day_offset);
              }
              copy.arrival_datetime_iso = dt.toISOString();
            }
          } catch (e) {
            // ignore conversion errors — keep original fields
          }
        }
        if (copy.departure_time) {
          try {
            const dt2 = new Date(`${requestDate}T${copy.departure_time}Z`);
            if (copy.departure_day_offset && Number.isFinite(copy.departure_day_offset) && copy.departure_day_offset > 0) {
              dt2.setUTCDate(dt2.getUTCDate() + copy.departure_day_offset);
            }
            copy.departure_datetime_iso = dt2.toISOString();
          } catch (e) {
            // ignore
          }
        }

        return copy;
      });
    } catch (e) {
      // If anything goes wrong during augmentation, fall back to the raw response
      // and avoid breaking the app — the original fields are preserved.
      // eslint-disable-next-line no-console
      console.warn('Failed to augment journey legs with day-offset display fields', e);
    }

  return responseJson;
  } catch (error) {
    console.error('Error getting journey plans:', error);
    throw error;
  }
};

/**
 * Compare multiple router implementations (main, eco, lazy, greedy)
 * Returns an object with keys 'main','eco','lazy','greedy' each containing
 * the same journey-plan response shape as /journey/plan.
 */
export const compareRouters = async (fromStop, toStop, departureTime, options = {}) => {
  try {
    const from = normalizeStopLocation(fromStop);
    const to = normalizeStopLocation(toStop);
    if (!from || !to) {
      throw new Error('fromStop and toStop must include lat/lon');
    }
    const { date, time } = normalizeDateTime(departureTime);
    const maxTransfers = typeof options.maxTransfers === 'number' ? options.maxTransfers : 3;
    const mode = typeof options.mode === 'string' ? options.mode : 'combined';
    // Ask backend to embed per-leg geometry (legs[].geometry.coords) so the UI can
    // draw the planner's stop-to-stop sliced route tracks without extra per-leg calls.
    const includeGeometry = options.includeGeometry !== undefined ? !!options.includeGeometry : true;
    const response = await fetch(
      `${API_BASE_URL}/journey/compare`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fromStop: from, toStop: to, departureTime: time, date, maxTransfers, mode, includeGeometry }),
      }
    );
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
    const responseJson = await response.json();
  // The /journey/compare endpoint returns nested journey-plan objects
  // under keys like main, eco, lazy, greedy. Ensure each returned
    // plan is augmented with the same user-friendly fields that
    // getJourneyPlans() provides (arrival_time_with_offset, departure_time_with_offset,
    // iso datetimes) so the UI can display day-shifts consistently.
    try {
      const maybeAugment = (obj) => {
        if (!obj || !obj.route) return;
        try {
          // augmentJourneyResponse mutates the object in-place and returns it
          // when given a journey-plan shape. Use it to ensure legs have
          // arrival_time_with_offset / departure_time_with_offset.
          const augmented = augmentJourneyResponse(obj.route, date);
          obj.route = augmented;
        } catch (e) {
          // ignore augmentation failures — keep original
        }
      };
      maybeAugment(responseJson.main);
      maybeAugment(responseJson.eco);
      maybeAugment(responseJson.lazy);
      maybeAugment(responseJson.greedy);
    } catch (e) {
      // non-fatal
    }
    if (options.includeRaw) {
      try {
        if (typeof structuredClone === 'function') {
          responseJson._raw = structuredClone(responseJson);
        } else {
          responseJson._raw = JSON.parse(JSON.stringify(responseJson));
        }
      } catch (e) {
        try {
          responseJson._raw = Array.isArray(responseJson) ? responseJson.slice() : Object.assign({}, responseJson);
        } catch (e2) {
          responseJson._raw = null;
        }
      }
    }
    return responseJson;
  } catch (error) {
    console.error('Error comparing routers:', error);
    throw error;
  }
};

/**
 * Get weather data for specific coordinates
 * @param {number} lat - Latitude (e.g., 54.05)
 * @param {number} lon - Longitude (e.g., -2.80)
 * @returns {Promise<Object>} Weather data object
 */
export const fetchWeatherData = async (lat = 54.05, lon = -2.80) => {
  try {
    const response = await fetch(
      `${API_BASE_URL}/weather?lat=${lat}&lon=${lon}`
    );
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error('Error fetching weather data:', error);
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
