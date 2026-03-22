/**
 * Custom Hooks for Transport Data Management
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  fetchLiveBusLocations,
  fetchRailDepartures,
  fetchBusArrivals,
  searchStops,
  getJourneyPlans,
  fetchServiceAlerts,
  fetchPricing
} from '../services/transportApi';
import { liveUpdatesManager } from '../services/liveUpdates';

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const withRetry = async (fn, { retries = 2, baseDelay = 500 } = {}) => {
  let attempt = 0;
  while (true) {
    try {
      return await fn();
    } catch (error) {
      if (attempt >= retries) throw error;
      const wait = baseDelay * (2 ** attempt);
      attempt += 1;
      await delay(wait);
    }
  }
};

/**
 * Hook for fetching and managing live bus locations.
 * Accepts dynamic lat/lon (e.g. from map center) and debounces
 * API calls so rapid map panning does not spam the backend.
 *
 * @param {string}  operatorCode     - Bus operator code (e.g. 'SCCU')
 * @param {Object}  options
 * @param {number}  [options.lat]            - Centre latitude
 * @param {number}  [options.lon]            - Centre longitude
 * @param {number}  [options.refreshInterval=30000] - Auto-refresh interval (ms)
 * @param {number}  [options.debounceMs=800] - Debounce delay for lat/lon changes (ms)
 * @param {number}  [options.latTol]         - Latitude tolerance (passed to API)
 * @param {number}  [options.lonTol]         - Longitude tolerance (passed to API)
 * @param {string}  [options.keep_vehicle_id]- Always return this vehicle
 */
export const useLiveBusLocations = (
  operatorCode,
  { lat, lon, refreshInterval = 30000, debounceMs = 800, latTol, lonTol, keep_vehicle_id = null } = {}
) => {
  // Enforce a minimum refresh interval of 5 seconds to avoid overly
  // aggressive polling from callers that pass very small values.
  const effectiveRefreshInterval = Math.max(5000, Number(refreshInterval || 0));
  const [data, setData] = useState([]);
  // `loading` is true only until the very first fetch completes (initial load).
  // Subsequent background re-fetches are indicated by `refreshing` instead,
  // so the map overlay is not shown on every 30-second poll.
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  // Countdown in seconds until the next automatic refresh.
  const [countdown, setCountdown] = useState(Math.round(effectiveRefreshInterval / 1000));
  const initializedRef = useRef(false);
  const debounceRef = useRef(null);
  const intervalRef = useRef(null);
  const countdownRef = useRef(null);

  // Start a 1 s tick countdown that resets after each fetch.
  const startCountdown = useCallback((seconds) => {
    if (countdownRef.current) clearInterval(countdownRef.current);
    let remaining = seconds;
    setCountdown(remaining);
    countdownRef.current = setInterval(() => {
      remaining -= 1;
      setCountdown(Math.max(0, remaining));
      if (remaining <= 0) clearInterval(countdownRef.current);
    }, 1000);
  }, []);

  const fetchData = useCallback(async (fetchLat, fetchLon) => {
    if (!operatorCode) return;
    const isInitial = !initializedRef.current;
    if (isInitial) {
      setLoading(true);
    } else {
      setRefreshing(true);
    }
    try {
      const opts = {};
      if (typeof fetchLat === 'number' && typeof fetchLon === 'number') {
        opts.lat = fetchLat;
        opts.lon = fetchLon;
      }
      if (typeof latTol === 'number') {
        opts.latTol = latTol;
      }
      if (typeof lonTol === 'number') {
        opts.lonTol = lonTol;
      }
      if (keep_vehicle_id) {
        opts.keep_vehicle_id = keep_vehicle_id;
      }
      const result = await withRetry(
        () => fetchLiveBusLocations(operatorCode, opts),
        { retries: 2, baseDelay: 500 }
      );
      setData(result);
      setError(null);
      if (isInitial) {
        initializedRef.current = true;
        setLoading(false);
      }
      // Restart the countdown after every successful fetch.
    startCountdown(Math.round(effectiveRefreshInterval / 1000));
    } catch (err) {
      setError(err);
      console.error('Error fetching bus locations:', err);
      if (isInitial) setLoading(false);
    } finally {
      setRefreshing(false);
    }
  }, [operatorCode, refreshInterval, startCountdown, keep_vehicle_id]);

  // Debounce lat/lon changes, then set up auto-refresh interval
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (intervalRef.current) clearInterval(intervalRef.current);

    debounceRef.current = setTimeout(() => {
      fetchData(lat, lon);
      intervalRef.current = setInterval(() => {
        fetchData(lat, lon);
      }, effectiveRefreshInterval);
    }, debounceMs);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (intervalRef.current) clearInterval(intervalRef.current);
      if (countdownRef.current) clearInterval(countdownRef.current);
    };
  }, [fetchData, lat, lon, effectiveRefreshInterval, debounceMs]);

  const refetch = useCallback(() => fetchData(lat, lon), [fetchData, lat, lon]);

  return { data, loading, refreshing, countdown, refreshInterval: effectiveRefreshInterval, error, refetch };
};

/**
 * Hook for fetching and managing rail departures
 * @param {string} stationCode - Station CRS code
 * @param {number} refreshInterval - Refresh interval in milliseconds (default: 30000)
 */
export const useLiveDepartures = (stationCode, refreshInterval = 180000) => {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchData = useCallback(async () => {
    if (!stationCode) return;
    try {
      setLoading(true);
      const result = await withRetry(
        () => fetchRailDepartures(stationCode),
        { retries: 2, baseDelay: 500 }
      );
      setData(result);
      setError(null);
    } catch (err) {
      setError(err);
      console.error('Error fetching departures:', err);
    } finally {
      setLoading(false);
    }
  }, [stationCode]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, refreshInterval);
    return () => clearInterval(interval);
  }, [fetchData, refreshInterval]);

  return { data, loading, error, refetch: fetchData };
};

/**
 * Hook for fetching bus arrivals at a specific stop
 * @param {string} stopCode - NaPTAN stop code
 * @param {number} refreshInterval - Refresh interval in milliseconds (default: 20000)
 */
export const useBusArrivals = (stopCode, refreshInterval = 180000) => {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!stopCode) return;

    const fetchData = async () => {
      try {
        setLoading(true);
        const result = await withRetry(
          () => fetchBusArrivals(stopCode),
          { retries: 2, baseDelay: 500 }
        );
        setData(result);
        setError(null);
      } catch (err) {
        setError(err);
        console.error('Error fetching bus arrivals:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, refreshInterval);

    return () => clearInterval(interval);
  }, [stopCode, refreshInterval]);

  return { data, loading, error };
};

/**
 * Hook for searching stops
 */
export const useStopSearch = (query, debounceDelay = 500, mapCenter = null, mapBbox = null) => {
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!query || query.trim().length < 2) {
      setResults([]);
      return;
    }

    const timeoutId = setTimeout(async () => {
      try {
        setLoading(true);
        // Pass map center to backend so it can proximity-rank results.
        const center = (mapCenter && typeof mapCenter === 'object') ? mapCenter : null;
        const lat = center && typeof center.lat === 'number' ? center.lat : undefined;
        const lon = center && typeof center.lon === 'number' ? center.lon : undefined;
        const hasCenter = (typeof lat === 'number' && Number.isFinite(lat)) && (typeof lon === 'number' && Number.isFinite(lon));
        const result = await withRetry(
          () => (hasCenter ? searchStops(query, { lat, lon }) : searchStops(query)),
          { retries: 1, baseDelay: 400 }
        );
        // Normalize coordinate fields (support lat/lon or latitude/longitude)
        let normalized = Array.isArray(result) ? result.map((r) => {
          if (!r || typeof r !== 'object') return r;
          const lat = (typeof r.lat === 'number') ? r.lat : (typeof r.latitude === 'number' ? r.latitude : undefined);
          const lon = (typeof r.lon === 'number') ? r.lon : (typeof r.longitude === 'number' ? r.longitude : undefined);
          return { ...r, lat, lon };
        }) : result;

        // If a map centre is provided, filter out stop-type results that lack
        // numeric coordinates (they're not useful for map-centred actions).
        // If no map centre is provided (e.g. in tests or simple autocompletes),
        // keep all results so callers can decide how to handle missing coords.
        let filteredResult = normalized;
        try {
          if (mapCenter) {
            filteredResult = (filteredResult || []).filter((r) => {
              if (!r) return false;
              if (r.type === 'stop') {
                // require numeric lat/lon for stop suggestions when map-centred
                return typeof r.lat === 'number' && typeof r.lon === 'number';
              }
              return true;
            });
          }
        } catch (e) {
          // if filtering fails, fall back to original normalized list
          filteredResult = normalized;
        }

        // If a map centre is provided, keep stop-type results first
        // (they come from the backend) and sort location-type results
        // by proximity to the map centre so autocomplete prompts favour
        // nearby POIs.
        if (mapCenter && Array.isArray(filteredResult) && filteredResult.length > 0) {
          try {
            const { lat: cLat, lon: cLon } = mapCenter;
            const haversine = (la, lo) => {
              if (typeof la !== 'number' || typeof lo !== 'number') return Number.POSITIVE_INFINITY;
              const toRad = (v) => (v * Math.PI) / 180;
              const R = 6371000; // metres
              const dLat = toRad(la - cLat);
              const dLon = toRad(lo - cLon);
              const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(cLat)) * Math.cos(toRad(la)) * Math.sin(dLon / 2) ** 2;
              const d = 2 * R * Math.asin(Math.sqrt(a));
              return d;
            };
            let stops = filteredResult.filter((r) => r && r.type === 'stop');
            const locs = filteredResult.filter((r) => r && r.type === 'location');
            // Sort both stops and locations by proximity to map center
            stops.sort((a, b) => (haversine(a.lat, a.lon) - haversine(b.lat, b.lon)));
            locs.sort((a, b) => (haversine(a.lat, a.lon) - haversine(b.lat, b.lon)));
            setResults([...stops, ...locs]);
          } catch (e) {
            setResults(filteredResult);
          }
        } else {
          setResults(filteredResult);
        }
        setError(null);
      } catch (err) {
        setError(err);
        console.error('Error searching stops:', err);
      } finally {
        setLoading(false);
      }
    }, debounceDelay);

    return () => {
      clearTimeout(timeoutId);
    };
  // NOTE: mapBbox is accepted for call-site compatibility; we don't
  // currently use it in the backend request, but including it here ensures
  // changes to bbox restart the debounce and don't leave stale requests.
  }, [query, debounceDelay, mapCenter, mapBbox]);

  return { results, loading, error };
};

/**
 * Hook for journey planning
 */
export const useJourneyPlans = (fromStop, toStop, departureTime, maxTransfers = 3) => {
  const [routes, setRoutes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchPlans = useCallback(async () => {
    if (!fromStop || !toStop) return;

    try {
      setLoading(true);
      const result = await withRetry(
        () => getJourneyPlans(
          fromStop,
          toStop,
          departureTime || new Date().toISOString(),
          { maxTransfers }
        ),
        { retries: 1, baseDelay: 600 }
      );
      setRoutes(result);
      setError(null);
    } catch (err) {
      setError(err);
      console.error('Error fetching journey plans:', err);
    } finally {
      setLoading(false);
    }
  }, [fromStop, toStop, departureTime, maxTransfers]);

  return { routes, loading, error, fetchPlans };
};

/**
 * Hook for service alerts
 * @param {number} refreshInterval - Refresh interval in milliseconds (default: 60000)
 */
export const useServiceAlerts = (refreshInterval = 60000) => {
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchAlerts = async () => {
      try {
        setLoading(true);
        const result = await withRetry(
          () => fetchServiceAlerts(),
          { retries: 2, baseDelay: 500 }
        );
        setAlerts(result);
        setError(null);
      } catch (err) {
        setError(err);
        console.error('Error fetching alerts:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchAlerts();
    const interval = setInterval(fetchAlerts, refreshInterval);

    return () => clearInterval(interval);
  }, [refreshInterval]);

  return { alerts, loading, error };
};

/**
 * Hook for pricing information
 */
export const usePricing = (fromStop, toStop) => {
  const [pricing, setPricing] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!fromStop || !toStop) return;

    const fetchPrice = async () => {
      try {
        setLoading(true);
        const result = await withRetry(
          () => fetchPricing(fromStop, toStop),
          { retries: 1, baseDelay: 600 }
        );
        setPricing(result);
        setError(null);
      } catch (err) {
        setError(err);
        console.error('Error fetching pricing:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchPrice();
  }, [fromStop, toStop]);

  return { pricing, loading, error };
};

/**
 * Hook for managing favorite/recent routes with localStorage
 */
export const useFavoriteRoutes = () => {
  // Safely access localStorage: in some test environments (or unusual browsers)
  // localStorage may be missing or its methods not available. Provide a
  // no-op fallback to avoid throwing during hook initialization.
  const safeLS = (typeof window !== 'undefined' && window.localStorage && typeof window.localStorage.getItem === 'function')
    ? window.localStorage
    : { getItem: () => null, setItem: () => {}, removeItem: () => {} };

  const [favorites, setFavorites] = useState(() => {
    try {
      const saved = safeLS.getItem('favoriteRoutes');
      return saved ? JSON.parse(saved) : [];
    } catch (error) {
      // Keep console.error but continue with empty favorites in tests.
      console.error('Error loading favorites:', error);
      return [];
    }
  });

  const saveFavorite = useCallback((route) => {
    setFavorites(prev => {
      const updated = [
        { ...route, savedAt: new Date().toISOString() },
        ...prev.filter(r => !(r.from === route.from && r.to === route.to))
      ].slice(0, 20); // Keep only last 20
      
      try {
        safeLS.setItem('favoriteRoutes', JSON.stringify(updated));
      } catch (e) {
        // ignore storage write errors (e.g., quota or not available in tests)
      }
      return updated;
    });
  }, []);

  const removeFavorite = useCallback((fromStop, toStop) => {
    setFavorites(prev => {
      const updated = prev.filter(r => !(r.from === fromStop && r.to === toStop));
      try {
        safeLS.setItem('favoriteRoutes', JSON.stringify(updated));
      } catch (e) {
        // ignore
      }
      return updated;
    });
  }, []);

  const clearAll = useCallback(() => {
    setFavorites([]);
    try {
      safeLS.removeItem('favoriteRoutes');
    } catch (e) {
      // ignore
    }
  }, []);

  return { favorites, saveFavorite, removeFavorite, clearAll };
};

/**
 * Hook for live updates via WebSocket/STOMP
 */
export const useLiveUpdates = (subscriptionType = 'train') => {
  const [data, setData] = useState(null);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const connect = async () => {
      try {
        await liveUpdatesManager.connect();
        setIsConnected(true);

        let subscriptionId;
        const handleMessage = (message) => {
          setData(message);
        };

        if (subscriptionType === 'train') {
          subscriptionId = liveUpdatesManager.subscribeToTrainMovements(handleMessage);
        } else if (subscriptionType === 'bus') {
          subscriptionId = liveUpdatesManager.subscribeToBusMovements(handleMessage);
        } else if (subscriptionType === 'alerts') {
          subscriptionId = liveUpdatesManager.subscribeToAlerts(handleMessage);
        }

        return () => {
          if (subscriptionId) {
            liveUpdatesManager.unsubscribe(subscriptionId);
          }
        };
      } catch (err) {
        setError(err);
        console.error('Error connecting to live updates:', err);
      }
    };

    const cleanup = connect();

    return async () => {
      if (cleanup) await cleanup;
      await liveUpdatesManager.disconnect();
      setIsConnected(false);
    };
  }, [subscriptionType]);

  return { data, isConnected, error };
};
