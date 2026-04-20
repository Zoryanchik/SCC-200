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
import { fetchRouteLineAtStop, fetchRouteLineWithFallback } from '../services/routeLineApi';

const maxVariantStops = (data) => {
  try {
    const variants = Array.isArray(data?.variants) ? data.variants : [];
    let best = 0;
    for (const v of variants) {
      const n = Array.isArray(v?.stops) ? v.stops.length : 0;
      if (n > best) best = n;
    }
    return best;
  } catch (e) {
    return 0;
  }
};

const variantsContainingAtco = (data, atcoCode) => {
  try {
    const key = String(atcoCode || '').trim();
    if (!key) return { ...(data || {}), variants: Array.isArray(data?.variants) ? data.variants : [] };
    const variants = Array.isArray(data?.variants) ? data.variants : [];
    const kept = variants.filter((v) =>
      Array.isArray(v?.stops) && v.stops.some((s) => String(s?.atco_code || '').trim() === key),
    );
    return { ...(data || {}), variants: kept };
  } catch (e) {
    return { ...(data || {}), variants: [] };
  }
};

export function useRouteLine() {
  // Map<string, routeData> — only contains entries for active lines.
  // We use a ref + a state counter so toggling is O(1) and does NOT
  // recreate the Map on every render (which was the cause of the
  // rapid-refresh bug last time).
  const cacheRef = useRef(new Map());   // line → routeData (persists even after toggle off)
  // line → { data: routeData, style?: { dashed?: boolean } }
  const activeRef = useRef(new Map());  // only currently active

  // Bump this to force a re-render after mutating activeRef
  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick((t) => t + 1), []);

  /**
   * Toggle a route line on or off.  If turning ON and the data
   * isn't cached yet, fetch it from the backend first.
   */
  const toggleRoute = useCallback(
    async (line, opts = {}) => {
      const cacheKey =
        opts && opts.atcoCode
          ? `${String(line)}@@atco:${String(opts.atcoCode)}`
          : `${String(line)}@@global`;
      try {
        console.debug('[useRouteLine] toggleRoute', { line: String(line), cacheKey, opts: opts ? { ...opts } : null, wasActive: activeRef.current.has(line) });
      } catch (e) { /* ignore */ }
      if (activeRef.current.has(line)) {
        // Turn OFF — just remove from the active map
        activeRef.current.delete(line);
        bump();
        return;
      }

      // Turn ON — use cache or fetch
      let data = cacheRef.current.get(cacheKey);
      if (!data) {
        try {
          try { console.debug('[useRouteLine] fetching route line', { line: String(line), atcoCode: opts && opts.atcoCode ? String(opts.atcoCode) : null }); } catch (e) { /* ignore */ }
          if (opts && opts.atcoCode) {
            // 1) Resolve locally at the clicked stop to disambiguate short numeric lines.
            // 2) If this only yields short workings, compare against geo-scoped full-line
            //    variants and prefer the fuller result when clearly longer.
            const localData = await fetchRouteLineAtStop(opts.atcoCode, line);
            let chosenData = localData;

            try {
              const localMax = maxVariantStops(localData);
              const hasGeo = Number.isFinite(Number(opts?.lat)) && Number.isFinite(Number(opts?.lon));
              if (hasGeo) {
                const globalData = await fetchRouteLineWithFallback(line, opts);
                const globalAtStopData = variantsContainingAtco(globalData, opts.atcoCode);
                const globalMax = maxVariantStops(globalAtStopData);
                // Promote to fuller line variants when stop-scoped result appears truncated.
                if (globalMax >= Math.max(10, localMax + 2)) {
                  chosenData = globalAtStopData;
                  try {
                    console.debug('[useRouteLine] promoted stop-scoped route to fuller geo-scoped line (with clicked stop preserved)', {
                      line: String(line),
                      atcoCode: String(opts.atcoCode),
                      localMax,
                      globalMax,
                    });
                  } catch (e) { /* ignore */ }
                }
              }
            } catch (e) {
              // Non-fatal: keep local stop-scoped result.
            }

            data = chosenData;
          } else {
            data = await fetchRouteLineWithFallback(line, opts);
          }
          cacheRef.current.set(cacheKey, data);
        } catch (err) {
          console.error(`[useRouteLine] Failed to fetch line "${line}":`, err);
          return;
        }
      }

      const dashed = Boolean(opts && opts.atcoCode);
      activeRef.current.set(line, { data, style: { dashed } });
      try {
        console.debug('[useRouteLine] active route set', { line: String(line), dashed, variantsLen: data && Array.isArray(data.variants) ? data.variants.length : null });
      } catch (e) { /* ignore */ }
      bump();
    },
    [bump],
  );

  const isActive = useCallback((line) => activeRef.current.has(line), []);

  /** Remove all active route overlays at once. */
  const clearRoutes = useCallback(() => {
    if (activeRef.current.size === 0) return;
    activeRef.current.clear();
    bump();
  }, [bump]);

  // Only rebuild the Map snapshot when tick changes (i.e. after toggleRoute)
  const activeRoutes = useMemo(
    () => new Map(activeRef.current),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tick],
  );

  return { activeRoutes, toggleRoute, isActive, clearRoutes };
}
