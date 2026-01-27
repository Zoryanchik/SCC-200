/**
 * Custom Hooks for Transport Data Management
 */

import { useState, useEffect, useCallback } from 'react';
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

/**
 * Hook for fetching and managing live bus locations
 * @param {string} operatorCode - Bus operator code
 * @param {number} refreshInterval - Refresh interval in milliseconds (default: 30000)
 */
export const useLiveBusLocations = (operatorCode, refreshInterval = 30000) => {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!operatorCode) return;

    const fetchData = async () => {
      try {
        setLoading(true);
        const result = await fetchLiveBusLocations(operatorCode);
        setData(result);
        setError(null);
      } catch (err) {
        setError(err);
        console.error('Error fetching bus locations:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, refreshInterval);

    return () => clearInterval(interval);
  }, [operatorCode, refreshInterval]);

  return { data, loading, error, refetch: () => fetchData() };
};

/**
 * Hook for fetching and managing rail departures
 * @param {string} stationCode - Station CRS code
 * @param {number} refreshInterval - Refresh interval in milliseconds (default: 30000)
 */
export const useLiveDepartures = (stationCode, refreshInterval = 30000) => {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchData = useCallback(async () => {
    if (!stationCode) return;
    try {
      setLoading(true);
      const result = await fetchRailDepartures(stationCode);
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
export const useBusArrivals = (stopCode, refreshInterval = 20000) => {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!stopCode) return;

    const fetchData = async () => {
      try {
        setLoading(true);
        const result = await fetchBusArrivals(stopCode);
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
export const useStopSearch = (query, debounceDelay = 500) => {
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
        const result = await searchStops(query);
        setResults(result);
        setError(null);
      } catch (err) {
        setError(err);
        console.error('Error searching stops:', err);
      } finally {
        setLoading(false);
      }
    }, debounceDelay);

    return () => clearTimeout(timeoutId);
  }, [query, debounceDelay]);

  return { results, loading, error };
};

/**
 * Hook for journey planning
 */
export const useJourneyPlans = (fromStop, toStop, departureTime) => {
  const [routes, setRoutes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchPlans = useCallback(async () => {
    if (!fromStop || !toStop) return;

    try {
      setLoading(true);
      const result = await getJourneyPlans(
        fromStop,
        toStop,
        departureTime || new Date().toISOString()
      );
      setRoutes(result);
      setError(null);
    } catch (err) {
      setError(err);
      console.error('Error fetching journey plans:', err);
    } finally {
      setLoading(false);
    }
  }, [fromStop, toStop, departureTime]);

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
        const result = await fetchServiceAlerts();
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
        const result = await fetchPricing(fromStop, toStop);
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
  const [favorites, setFavorites] = useState(() => {
    try {
      const saved = localStorage.getItem('favoriteRoutes');
      return saved ? JSON.parse(saved) : [];
    } catch (error) {
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
      
      localStorage.setItem('favoriteRoutes', JSON.stringify(updated));
      return updated;
    });
  }, []);

  const removeFavorite = useCallback((fromStop, toStop) => {
    setFavorites(prev => {
      const updated = prev.filter(r => !(r.from === fromStop && r.to === toStop));
      localStorage.setItem('favoriteRoutes', JSON.stringify(updated));
      return updated;
    });
  }, []);

  const clearAll = useCallback(() => {
    setFavorites([]);
    localStorage.removeItem('favoriteRoutes');
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
