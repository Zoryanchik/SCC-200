/**
 * useRouteLine — manages which bus-route lines are toggled ON and
 * holds the fetched route data so RouteLineLayer can draw polylines.
 *
 * Returns:
 *   activeRoutes : Map<string, routeData>   — line name → API response
 *   toggleRoute  : (line: string) => void   — toggle a line on/off
 *   isActive     : (line: string) => boolean
 */
import { useCallback, useMemo, useRef, useState } from 'react';
import { fetchRouteLine } from '../services/routeLineApi';

export function useRouteLine() {
  // Map<string, routeData> — only contains entries for active lines.
  // We use a ref + a state counter so toggling is O(1) and does NOT
  // recreate the Map on every render (which was the cause of the
  // rapid-refresh bug last time).
  const cacheRef = useRef(new Map());   // line → routeData (persists even after toggle off)
  const activeRef = useRef(new Map());  // line → routeData (only currently active)

  // Bump this to force a re-render after mutating activeRef
  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick((t) => t + 1), []);

  /**
   * Toggle a route line on or off.  If turning ON and the data
   * isn't cached yet, fetch it from the backend first.
   *
   * @param {string} line      – line name, e.g. "51"
   * @param {string} [operator] – optional SIRI operator code, e.g. "SCCU"
   */
  const toggleRoute = useCallback(
    async (line, operator = null) => {
      if (activeRef.current.has(line)) {
        // Turn OFF — just remove from the active map
        activeRef.current.delete(line);
        bump();
        return;
      }

      // Turn ON — use cache or fetch
      let data = cacheRef.current.get(line);
      if (!data) {
        try {
          data = await fetchRouteLine(line, operator);
          cacheRef.current.set(line, data);
        } catch (err) {
          console.error(`[useRouteLine] Failed to fetch line "${line}":`, err);
          return;
        }
      }

      activeRef.current.set(line, data);
      bump();
    },
    [bump],
  );

  const isActive = useCallback((line) => activeRef.current.has(line), []);

  // Only rebuild the Map snapshot when tick changes (i.e. after toggleRoute)
  const activeRoutes = useMemo(
    () => new Map(activeRef.current),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tick],
  );

  return { activeRoutes, toggleRoute, isActive };
}
