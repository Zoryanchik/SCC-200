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
    /**
     * Toggle a route line on or off. If turning ON and the data
     * isn't cached yet, fetch it from the backend first. Optionally
     * provide `opts.preferredLastStop` (string) which will be used to
     * prefer variants whose last stop matches the given destination.
     *
     * @param {string} line
     * @param {string|null} operator
     * @param {{ preferredLastStop?: string }} [opts]
     */
    async (line, operator = null, opts = {}) => {
      // Use a composite cache key so operator-scoped lookups don't
      // collide with earlier no-operator (or different-operator) fetches.
      const opKey = operator ? String(operator) : '';
      const cacheKey = `${line}||${opKey}`;

      if (activeRef.current.has(cacheKey)) {
        // Turn OFF — remove the composite key
        activeRef.current.delete(cacheKey);
        bump();
        return null;
      }

      // Turn ON — use cache or fetch for this (line,operator) pair
      let fullData = cacheRef.current.get(cacheKey);
      if (!fullData) {
        try {
          fullData = await fetchRouteLine(line, operator);
          // Cache the full unfiltered response for future use
          cacheRef.current.set(cacheKey, fullData);
        } catch (err) {
          console.error(`[useRouteLine] Failed to fetch line "${line}" (operator=${operator}):`, err);
          return null;
        }
      }

      // If a preferred last-stop was provided, try to filter variants
      // to those whose final stop matches.  We don't mutate the cached
      // fullData; instead store a filtered copy in activeRef so other
      // callers can still retrieve the complete set from cache.
      const preferred = opts && typeof opts.preferredLastStop === 'string' && opts.preferredLastStop.trim()
        ? String(opts.preferredLastStop).trim()
        : null;

      let activeData = fullData;
      if (preferred && Array.isArray(fullData?.variants) && fullData.variants.length > 0) {
        // Normalise input for comparisons. For ATCO codes we compare a
        // compact lowercased form (remove spaces), for names we create a
        // loose space-normalised lowercase form.
        const compact = (s) => String(s || '').toLowerCase().replace(/\s+/g, '').trim();
        const normName = (s) => String(s || '').toLowerCase().replace(/[\s\-_,.]+/g, ' ').trim();

        const pCompact = compact(preferred);
        const pName = normName(preferred);

        // First pass: try to match by ATCO/code fields exactly (compact form).
        const codeMatched = fullData.variants.filter((v) => {
          if (!Array.isArray(v.stops) || v.stops.length === 0) return false;
          const last = v.stops[v.stops.length - 1] || {};
          const candidates = [last.atco_code, last.atco, last.atcoCode, last.atcoCodeValue, last.atcoCodeId]
            .filter(Boolean)
            .map((c) => compact(c));
          return candidates.some((c) => c === pCompact);
        });

        if (codeMatched.length > 0) {
          activeData = { ...fullData, variants: codeMatched };
        } else {
          // Second pass: fall back to name-based matching (looser equality / startsWith)
          const nameMatched = fullData.variants.filter((v) => {
            if (!Array.isArray(v.stops) || v.stops.length === 0) return false;
            const last = v.stops[v.stops.length - 1] || {};
            if (last.name) {
              const lname = normName(last.name);
              if (lname === pName) return true;
              if (lname.startsWith(pName) || pName.startsWith(lname)) return true;
            }
            return false;
          });
          if (nameMatched.length > 0) activeData = { ...fullData, variants: nameMatched };
        }
      }

      activeRef.current.set(cacheKey, activeData);
      bump();
      return activeData;
    },
    [bump],
  );

  /**
   * isActive(line, operator?) — if operator provided, check the
   * exact composite key; otherwise return true if any active entry
   * exists for the given short line (regardless of operator).
   */
  const isActive = useCallback((line, operator = null) => {
    if (operator) {
      const key = `${line}||${String(operator)}`;
      return activeRef.current.has(key);
    }
    // If no operator provided, see if any composite key for this line exists
    for (const k of activeRef.current.keys()) {
      if (String(k).split('||')[0] === String(line)) return true;
    }
    return false;
  }, []);

  // Only rebuild the Map snapshot when tick changes (i.e. after toggleRoute)
  const activeRoutes = useMemo(
    () => new Map(activeRef.current),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tick],
  );

  return { activeRoutes, toggleRoute, isActive };
}
