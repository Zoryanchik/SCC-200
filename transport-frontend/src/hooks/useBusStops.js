import { useState, useEffect, useCallback, useRef } from 'react';
import { getBusStopsWithFallback } from '../services/busStopsApi';

/**
 * React hook that fetches bus stops and exposes them to map components.
 *
 * - Loads stops once on mount (or when `classification` changes).
 * - Provides `loading`, `error`, and `stops` state.
 * - Uses a stale-while-revalidate pattern: previous data stays visible
 *   while a re-fetch is in progress.
 * - Prevents duplicate concurrent requests via a ref flag.
 *
 * @param {Object}  [options]
 * @param {string}  [options.classification] - Optional classification filter
 * @param {string}  [options.bbox]           - Viewport "south,west,north,east"
 * @param {boolean} [options.enabled=true]   - Set false to disable fetching
 * @returns {{ stops: Array, loading: boolean, error: string|null, refetch: Function }}
 */
export function useBusStops({ classification, bbox, enabled = true } = {}) {
  const [stops, setStops] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const fetchingRef = useRef(false);

  const fetchStops = useCallback(async () => {
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    setLoading(true);
    setError(null);

    try {
      const opts = { classification };
      if (bbox != null) opts.bbox = bbox;
      const data = await getBusStopsWithFallback(opts);
      // Deduplicate by stop id (ATCO code) — the API can return duplicates
      const seen = new Set();
      const unique = data.filter((s) => {
        if (seen.has(s.id)) return false;
        seen.add(s.id);
        return true;
      });
      setStops(unique);
    } catch (err) {
      setError(err.message || 'Failed to load bus stops');
    } finally {
      setLoading(false);
      fetchingRef.current = false;
    }
  }, [classification, bbox]);

  useEffect(() => {
    if (enabled) {
      fetchStops();
    }
  }, [fetchStops, enabled]);

  return { stops, loading, error, refetch: fetchStops };
}

export default useBusStops;
