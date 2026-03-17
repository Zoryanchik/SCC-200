import React, { useMemo, useState, useEffect, useCallback, lazy, Suspense } from "react";
import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Popover from '@mui/material/Popover';
import IconButton from "@mui/material/IconButton";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import MenuItem from '@mui/material/MenuItem';
import Divider from "@mui/material/Divider";
import InputAdornment from "@mui/material/InputAdornment";
import Paper from "@mui/material/Paper";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogTitle from "@mui/material/DialogTitle";
import DialogContent from "@mui/material/DialogContent";
import DialogActions from "@mui/material/DialogActions";
import { AlertCircle, Bus, Clock, MapPin, Navigation as NavIcon, Crosshair, Train, Heart, X } from "lucide-react";
import { useStopSearch, useFavoriteRoutes, useLiveDepartures, useServiceAlerts, useLiveUpdates, useLiveBusLocations } from "../hooks/useTransportData";
import { getJourneyPlans, compareRouters } from "../services/transportApi";
import DepartureCard from "../components/common/DepartureCard";
import RouteCard from "../components/common/RouteCard";
import WeatherWidget from "../components/common/WeatherWidget";
import RemoveDangerousHTML from "../components/common/RemoveDangerousHTML";

const MapViewMap = lazy(() => import("../components/map/MapViewMap"));

/**
 * Convert the backend journey-plan response ({success, legs, meta, …})
 * into a single RouteCard-compatible object ({id, duration, transfers,
 * steps, walkMinutes}).  Returns null when legs are empty.
 */
function journeyToRouteCard(journey) {
  // Debug: print the raw journey object received so we can verify
  // whether arrival_day_offset / departure_day_offset and related
  // augmented fields are present at runtime.
  try {
    // eslint-disable-next-line no-console
    console.debug('[journeyToRouteCard] incoming journey:', journey);
  } catch (e) {
    // ignore
  }
  const legs = journey?.legs;
  if (!Array.isArray(legs) || legs.length === 0) return null;

  // Build step list that RouteCard understands
  const steps = legs.map((leg) => {
    const mode = (leg.mode || "walk").toLowerCase();
    const type = mode === "walking" ? "walk" : mode;
    const durSec = leg.duration_seconds ?? 0;
    const durMin = Math.round(durSec / 60);

    const fromName = leg.from_stop?.name || "";
    const toName = leg.to_stop?.name || "";
  const fromClassification = leg.from_stop?.classification || null;
  const toClassification = leg.to_stop?.classification || null;

    return {
      type,
      route: leg.line_name || "",
      duration: durMin >= 60
        ? `${Math.floor(durMin / 60)}h ${durMin % 60} mins`
        : `${durMin} mins`,
  from: fromName,
  to: toName,
  from_classification: fromClassification,
  to_classification: toClassification,
      journey_origin: leg.journey_origin || null,
      journey_destination: leg.journey_destination || null,
      // keep original times for the time display row in RouteCard
      // Prefer the _with_offset display fields, but fall back to raw times
      departure_time_with_offset: leg.departure_time_with_offset ?? leg.departure_time ?? null,
      arrival_time_with_offset: leg.arrival_time_with_offset ?? leg.arrival_time ?? null,
      // Real-time delay info for bus/train legs
      scheduled_departure_time: leg.scheduled_departure_time ?? leg.departure_time ?? null,
      scheduled_arrival_time: leg.scheduled_arrival_time ?? leg.arrival_time ?? null,
      realtime_departure_time_with_offset: leg.realtime_departure_time_with_offset ?? leg.realtime_departure_time ?? null,
      realtime_arrival_time_with_offset: leg.realtime_arrival_time_with_offset ?? leg.realtime_arrival_time ?? null,
      delay_seconds: leg.delay_seconds ?? null,
      status: leg.status ?? null,
    };
  });

  // Count transit transfers (non-walk legs minus 1, minimum 0)
  const transitLegs = steps.filter((s) => s.type !== "walk").length;
  const transfers = Math.max(0, transitLegs - 1);

  // Total walk minutes
  const walkMinutes = legs
    .filter((l) => (l.mode || "").toLowerCase() === "walking")
    .reduce((sum, l) => sum + Math.round((l.duration_seconds ?? 0) / 60), 0);

  // Overall duration from meta — prefer the canonical total_duration
  // string when the backend provides it (arrival − departure start).
  const meta = journey.meta || {};
  let duration = "";
  // Also compute a numeric totalSeconds to allow programmatic comparisons
  // Backend sends "total_duration_seconds" (not "total_seconds")
  const totalSec = meta.total_duration_seconds ?? meta.total_seconds ?? legs.reduce((s, l) => s + (l.duration_seconds ?? 0), 0);
  if (meta.total_duration) {
    duration = meta.total_duration;
  } else {
    const totalMin = Math.round(totalSec / 60);
    duration = totalMin >= 60
      ? `${Math.floor(totalMin / 60)}h ${totalMin % 60} mins`
      : `${totalMin} mins`;
  }

  // initial departure seconds — backend sends initial_departure_time (string)
  // and initial_departure_day_offset (int), NOT initial_departure_secs.
  // Parse the time string and combine with the day offset.
  let initialDepartureSecsMeta = null;
  let initialDepartureDayShiftMeta = 0;
  try {
    if (meta.initial_departure_time) {
      const parsed = parseTimeWithOffset(meta.initial_departure_time);
      if (Number.isFinite(parsed.secs)) {
        initialDepartureSecsMeta = parsed.secs;
        initialDepartureDayShiftMeta = parsed.dayShift || (Number.isFinite(meta.initial_departure_day_offset) ? meta.initial_departure_day_offset : 0);
      }
    }
  } catch (e) {
    // ignore
  }

  // parse a time string like "HH:MM" or "HH:MM:SS" possibly with a suffix like "(+1d)"
  // Returns { secs: number|null, dayShift: number }
  const parseTimeWithOffset = (timeStr) => {
    if (!timeStr || typeof timeStr !== 'string') return { secs: null, dayShift: 0 };
    // extract day-shift suffix like "(+1d)" or "(+2 days)"
    let dayShift = 0;
    try {
      const m = timeStr.match(/\(\s*\+\s*(\d+)\s*d/i);
      if (m && m[1]) dayShift = Number(m[1]) || 0;
    } catch (e) {
      dayShift = 0;
    }
    const core = timeStr.split('(')[0].trim();
    const parts = core.split(':').map((p) => parseInt(p, 10));
    if (parts.length < 2 || Number.isNaN(parts[0]) || Number.isNaN(parts[1])) return { secs: null, dayShift };
    const hh = parts[0];
    const mm = parts[1];
    const ss = parts.length >= 3 && !Number.isNaN(parts[2]) ? parts[2] : 0;
    if (hh < 0 || hh > 23 || mm < 0 || mm > 59 || ss < 0 || ss > 59) return { secs: null, dayShift };
    return { secs: hh * 3600 + mm * 60 + ss, dayShift };
  };

  // Prefer meta-provided departure time, else derive from the first leg's departure time.
  let initialDepartureSecs = initialDepartureSecsMeta ?? null;
  let initialDepartureDayShift = initialDepartureDayShiftMeta || 0;
  try {
    if (!Number.isFinite(initialDepartureSecs) && Array.isArray(journey?.legs) && journey.legs.length > 0) {
      const first = journey.legs[0];
      const candidates = [
        first.realtime_departure_time_with_offset,
        first.departure_time_with_offset,
        first.scheduled_departure_time,
        first.departure_time,
      ];
      for (const t of candidates) {
        const parsed = parseTimeWithOffset(t);
        if (Number.isFinite(parsed.secs)) {
          initialDepartureSecs = parsed.secs;
          initialDepartureDayShift = parsed.dayShift || 0;
          break;
        }
      }
    }
  } catch (e) {
    // ignore parse errors
  }

  // Compute arrival day shift from the last leg's arrival_time_with_offset (if available)
  let arrivalDayShift = 0;
  let finalArrivalWithOffset = null;
  try {
    const last = journey.legs[journey.legs.length - 1];
    const arrivalCandidates = [
      last.realtime_arrival_time_with_offset,
      last.arrival_time_with_offset,
      last.scheduled_arrival_time,
      last.arrival_time,
    ];
    for (const t of arrivalCandidates) {
      const parsed = parseTimeWithOffset(t);
      if (parsed && Number.isFinite(parsed.dayShift)) {
        arrivalDayShift = parsed.dayShift || 0;
        // capture the first human-friendly arrival-with-offset string if present
        if (!finalArrivalWithOffset && typeof t === 'string') finalArrivalWithOffset = t;
        break;
      }
    }
  } catch (e) {
    arrivalDayShift = 0;
  }

  // Also capture numeric final arrival seconds (secs since midnight) + dayShift*86400
  let finalArrivalSecs = null;
  try {
    const last = journey.legs[journey.legs.length - 1];
    const arrivalCandidates = [
      last.realtime_arrival_time_with_offset,
      last.arrival_time_with_offset,
      last.scheduled_arrival_time,
      last.arrival_time,
    ];
    for (const t of arrivalCandidates) {
      const parsed = parseTimeWithOffset(t);
      if (parsed && Number.isFinite(parsed.secs)) {
        finalArrivalSecs = parsed.secs + (parsed.dayShift || 0) * 86400;
        break;
      }
    }
  } catch (e) {
    finalArrivalSecs = null;
  }

  return {
    id: 1,
    duration,
    totalSeconds: totalSec,
  initialDepartureSecs,
  initialDepartureDayShift,
  // dayShift is the absolute day offset of the final arrival relative to
  // the requested date (0 = same day, 1 = next day).
  dayShift: (Number.isFinite(arrivalDayShift) ? arrivalDayShift : 0),
    transfers,
    steps,
    walkMinutes,
    finalArrivalWithOffset,
    // Pricing: £2.10 per bus leg (single ticket), train legs are not priced here
    busLegs: steps.filter((s) => s.type === 'bus').length,
    price: (() => {
      const n = steps.filter((s) => s.type === 'bus').length;
      if (n === 0) return null;
      const total = (n * 2.10).toFixed(2);
      return `£${total}`;
    })(),
    finalArrivalSecs,
  };
}

// Initial mock markers removed to prevent UI confusion
const MOCK_MARKERS = [
  { id: 'mk-bus-1', type: 'bus', position: [54.0480, -2.8010], name: 'To: Lancaster University', routeNumber: '1', delayMinutes: 0, operator: 'Stagecoach' },
  { id: 'mk-bus-2', type: 'bus', position: [54.0460, -2.7990], name: 'To: Morecambe', routeNumber: '2', delayMinutes: 4, operator: 'Stagecoach' },
  { id: 'mk-train-1', type: 'train', position: [54.0435, -2.8055], name: 'Glasgow Central' }
];

const DEFAULT_CENTER = { lat: 54.050556, lon: -2.800556 };

/**
 * Normalize raw coordinate arrays to [[lat, lon], ...].
 * If the first value is outside the latitude range (-90..90), assume it's
 * [lon, lat] and swap.  Stateless — safe to call from any scope.
 */
const normalizeCoords = (raw) => {
  if (!Array.isArray(raw)) return [];
  const out = [];
  for (const pt of raw) {
    if (!Array.isArray(pt) || pt.length < 2) continue;
    const a = Number(pt[0]);
    const b = Number(pt[1]);
    if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
    if (a < -90 || a > 90) out.push([b, a]); else out.push([a, b]);
  }
  return out;
};

/**
 * Fetch OSRM road-following geometry for every leg of a journey plan
 * **independently** (one request per leg, all in parallel).
 *
 * Walking legs → OSRM foot profile.
 * Transit legs (bus / train) → OSRM driving profile.
 *
 * Returns an array of segment objects ready for `JourneyRouteLayer`:
 *   { id, name, coords, color, mode }
 *
 * Falls back to a straight [from, to] line for any leg where OSRM fails.
 */
const fetchGeometryForLegs = async (legs) => {
  if (!Array.isArray(legs) || legs.length === 0) return [];

  // Some planners expose a plan-level identifier that should be threaded through
  // so /route/leg-geometry can fetch stored track subsegments.
  // New canonical key: route_int (dense in-memory route index).
  const fallbackRouteInt = (
    (legs && (legs.route_int ?? legs.routeInt ?? legs._route_int ?? legs._routeInt)) ??
    null
  );
  // Back-compat only: route_id may still exist in older responses.
  const fallbackRouteId = (
    (legs && (legs.route_id || legs.routeId || legs._route_id || legs._routeId)) ||
    null
  );

  const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:5050').replace(/\/$/, '');

  const promises = legs.map(async (leg, i) => {
    const fs = leg?.from_stop;
    const ts = leg?.to_stop;
    const fromLat = fs?.lat;
    const fromLon = fs?.lon;
    const toLat = ts?.lat;
    const toLon = ts?.lon;

  // Determine mode string for colouring / styling
  const rawMode = (leg.mode && String(leg.mode).toLowerCase()) || '';
  const isWalk = rawMode === 'walking';
  const mode = rawMode || (leg.line_name ? 'transit' : 'walking');

    // Build fallback straight-line coords from endpoints
    const fallbackCoords = [];
    if (typeof fromLat === 'number' && typeof fromLon === 'number')
      fallbackCoords.push([fromLat, fromLon]);
    if (typeof toLat === 'number' && typeof toLon === 'number')
      fallbackCoords.push([toLat, toLon]);

    const segment = {
      id: isWalk ? `walk-${i}` : `seg-${i}`,
      name: leg.line_name || mode || `Segment ${i}`,
      coords: fallbackCoords,
      color: isWalk ? '#000000' : '#1a73e8',
      mode,
    };

    // Prefer embedded geometry from the journey response when available.
    // This keeps the map consistent with the planner's stop-to-stop slicing
    // and avoids overriding a correct embedded track with a later per-leg call.
    try {
      const embedded = leg?.geometry;
      const embeddedCoords = embedded && Array.isArray(embedded.coords) ? embedded.coords : null;
      if (embeddedCoords && embeddedCoords.length >= 2) {
        const normEmbedded = normalizeCoords(embeddedCoords);
        if (normEmbedded.length >= 2) {
          segment.coords = normEmbedded;
          return segment;
        }
      }
    } catch (_e) {
      // ignore
    }

    // Need both endpoints to request OSRM geometry
    if (typeof fromLat !== 'number' || typeof fromLon !== 'number' ||
        typeof toLat !== 'number' || typeof toLon !== 'number') {
      return segment;
    }

    try {
  // Prefer backend-provided mode if present. For transit legs we still
  // ask the backend for 'bus' so it can prefer stored route_tracks and
  // subsegment by stops.
  const isBusLikeLeg = !isWalk && (rawMode === 'bus' || rawMode === 'transit' || !!leg?.line_name);
  // IMPORTANT: For transit legs, ask the backend for mode=bus so it can
  // prefer timetable route_tracks + stop-to-stop fragments. Using `driving`
  // can fall back to OSRM road-following geometry, which may look wrong.
  const requestMode = isWalk ? 'walking' : (isBusLikeLeg ? 'bus' : 'driving');
      // If the leg carries an explicit route_id (or metadata with route_id)
      // include it so the backend can return stored track subsegments when
      // OSRM is unavailable. If no route_id but a line name is available,
      // try to fetch variant geometry for that line as a fallback before
      // calling OSRM.
  // Canonical route id is provided by the backend in leg.meta.route_id.
  // Avoid falling back to other ids (journey ids / legacy ids) because
  // they can refer to a different variant and therefore draw a different
  // track.
  const routeInt = (
    (leg?.meta && (leg.meta.route_int ?? leg.meta.routeInt ?? leg.meta._route_int ?? leg.meta._routeInt)) ??
    leg?.route_int ??
    leg?.routeInt ??
    fallbackRouteInt ??
    null
  );
  const routeId = (
    (leg?.meta && (leg.meta.route_id || leg.meta.canonical_route_id || leg.meta.routeId)) ||
    leg?.route_id ||
    leg?.routeId ||
    (leg?.meta && leg.meta.route && (leg.meta.route.id || leg.meta.route.route_id)) ||
    fallbackRouteId ||
    null
  );

      // NOTE: We intentionally do NOT fall back to /routes/line/{line} geometry.
      // That endpoint can return multiple variants and selecting the wrong one
      // is exactly how we end up drawing a different track than the chosen
      // journey leg. Instead, we rely on /route/leg-geometry (with route_id and
      // stop ids) which is tied to the journey's canonical route_id.

  let url = `${API_BASE}/route/leg-geometry?from_lat=${encodeURIComponent(fromLat)}&from_lon=${encodeURIComponent(fromLon)}&to_lat=${encodeURIComponent(toLat)}&to_lon=${encodeURIComponent(toLon)}&mode=${encodeURIComponent(requestMode)}`;
      // Prefer route_int for in-memory track lookup, fall back to route_id for older backends.
      if (routeInt != null && routeInt !== '') {
        url += `&route_int=${encodeURIComponent(routeInt)}`;
      } else if (routeId) {
        url += `&route_id=${encodeURIComponent(routeId)}`;
      }

      // If we're routing a bus/transit leg, pass stop ids (ATCO codes) so the backend
      // can return a stored-track subsegment rather than the full route.
      try {
        const isBusLeg = isBusLikeLeg;
        const fromStopId = fs?.id || fs?.atco_code || fs?.atco || fs?.atcoCode || null;
        const toStopId = ts?.id || ts?.atco_code || ts?.atco || ts?.atcoCode || null;
        if ((routeInt != null || routeId) && isBusLeg && fromStopId && toStopId) {
          url += `&from_stop_id=${encodeURIComponent(fromStopId)}`;
          url += `&to_stop_id=${encodeURIComponent(toStopId)}`;
        }
      } catch (_) {
        // ignore
      }
      const resp = await fetch(url);
      if (!resp.ok) return segment;
      const data = await resp.json();
      if (data && Array.isArray(data.coords) && data.coords.length >= 2) {
        // Normalize coords — endpoint already returns [lat,lon] but be safe
        const norm = normalizeCoords(data.coords);
        if (norm.length >= 2) {
          // Ensure the returned geometry includes exact endpoints (from/to stops).
          // Smoothing or OSRM snapping can omit the exact stop coordinates; force them
          // so the drawn track always covers the stop locations.
          try {
            const first = norm[0];
            const last = norm[norm.length - 1];
            const eps = 1e-6;
            if (!first || Math.abs(first[0] - fromLat) > eps || Math.abs(first[1] - fromLon) > eps) {
              norm[0] = [fromLat, fromLon];
            }
            if (!last || Math.abs(last[0] - toLat) > eps || Math.abs(last[1] - toLon) > eps) {
              norm[norm.length - 1] = [toLat, toLon];
            }
          } catch (_) {
            // ignore and fall back to norm as-is
          }
          segment.coords = norm;
        }
      }
    } catch (_e) {
      // OSRM failure — keep fallback straight-line coords
    }

    return segment;
  });

  return Promise.all(promises);
};

export default function HomePage() {
  const LABEL_DESCRIPTIONS = {
    'EA': 'Earliest Arrival',
    'E·ARR': 'Earliest Arrival',
    'ED': 'Earliest Departure',
    'E·DEP': 'Earliest Departure',
    'FASTEST': 'Smallest Total Time',
    'ECO': 'Prefer Walking',
    'GREEDY': 'Least Transfers',
    'COSY': 'No Transfer Within Same Mode',
    'LAZY': 'Shorter Walks — Fewer Transfers',
  };
  // Colour map used for label chips — keep in sync with the chip rendering
  const LABEL_COLORS = {
    'E·ARR': '#FFF8E1',
    'E·DEP': '#E0BBE4',
    'FASTEST': '#87CEEB',
    'ECO': '#C6F6D5',
    'GREEDY': '#FF7F50',
    'COSY': '#9DC183',
    'LAZY': '#FFF59D',
  };

  const _hexToRgb = (hex) => {
    if (!hex) return null;
    const h = hex.replace('#', '');
    if (h.length === 3) {
      return [parseInt(h[0] + h[0], 16), parseInt(h[1] + h[1], 16), parseInt(h[2] + h[2], 16)];
    }
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  };

  // Pick white or black text depending on background luminance for legibility
  const textColorForBg = (hex) => {
    const rgb = _hexToRgb(hex || '#000000');
    if (!rgb) return '#000000';
    const [r, g, b] = rgb.map((v) => v / 255);
    // Perceived luminance formula
    const L = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    return L > 0.65 ? '#000000' : '#ffffff';
  };
  const [fromLocation, setFromLocation] = useState("");
  const [toLocation, setToLocation] = useState("");
  const [selectedFromStop, setSelectedFromStop] = useState(null);
  const [selectedToStop, setSelectedToStop] = useState(null);
  const [isSearching, setIsSearching] = useState(false);
  // Journey search controls: date, time, max transfers
  const pad2 = (n) => (n < 10 ? `0${n}` : `${n}`);
  const now = new Date();
  const defaultDate = now.toISOString().slice(0, 10); // YYYY-MM-DD
  const defaultTime = `${pad2(now.getHours())}:${pad2(now.getMinutes())}`; // HH:MM
  const [departureDate, setDepartureDate] = useState(defaultDate);
  const [departureClock, setDepartureClock] = useState(defaultTime);
  // Date selection split into year/month/day for the column picker
  const [dateSelYear, setDateSelYear] = useState(() => Number(defaultDate.slice(0, 4)));
  const [dateSelMonth, setDateSelMonth] = useState(() => Number(defaultDate.slice(5, 7)));
  const [dateSelDay, setDateSelDay] = useState(() => Number(defaultDate.slice(8, 10)));

  // Time split state: use 24-hour display
  const parseClock = (c) => {
    if (!c || typeof c !== 'string') return { h: 0, m: 0 };
    const parts = c.split(':').map((s) => parseInt(s, 10));
    if (parts.length < 2 || parts.some((x) => Number.isNaN(x))) return { h: 0, m: 0 };
    const hh = parts[0];
    const mm = parts[1];
    return { h: hh, m: mm };
  };
  const initTime = parseClock(defaultTime);
  const [timeSelHour, setTimeSelHour] = useState(initTime.h);
  const [timeSelMinute, setTimeSelMinute] = useState(initTime.m);

  // Keep split time in sync if departureClock changes externally
  useEffect(() => {
    try {
      const parsed = parseClock(departureClock);
      setTimeSelHour(parsed.h);
      setTimeSelMinute(parsed.m);
    } catch (e) {
      // ignore
    }
  }, [departureClock]);

  const daysInMonth = (y, m) => new Date(y, m, 0).getDate();

  // Keep the split selection in sync if departureDate changes externally
  useEffect(() => {
    try {
      if (typeof departureDate === 'string' && departureDate.length >= 10) {
        const y = Number(departureDate.slice(0, 4));
        const mo = Number(departureDate.slice(5, 7));
        const d = Number(departureDate.slice(8, 10));
        if (Number.isFinite(y) && Number.isFinite(mo) && Number.isFinite(d)) {
          setDateSelYear(y);
          setDateSelMonth(mo);
          setDateSelDay(d);
        }
      }
    } catch (e) {
      // ignore
    }
  }, [departureDate]);
  // default transfers set to 3
  const [maxTransfers, setMaxTransfers] = useState(3);
  // mode selector for journey planner: 'all' | 'bus' | 'train' (UI value); map 'all' -> 'combined' for API
  const [transportMode, setTransportMode] = useState('all');
  const { favorites, saveFavorite, removeFavorite } = useFavoriteRoutes();
  // ---- Map + live-bus state (needed for stop search proximity) ----
  const [markers, setMarkers] = useState(MOCK_MARKERS);
  const [filters, setFilters] = useState({ showBuses: true, showTrains: true });
  const [openPopupId, setOpenPopupId] = useState(null);
  const [openPopupSignature, setOpenPopupSignature] = useState(null);
  const [mapInstance, setMapInstance] = useState(null);
  const [mapCenter, setMapCenter] = useState(DEFAULT_CENTER);
  const [mapBbox, setMapBbox] = useState(null);
  const [geoError, setGeoError] = useState(null);
  const [userLocation, setUserLocation] = useState(null);
  const [locationStatus, setLocationStatus] = useState('idle');
  const [locationError, setLocationError] = useState(null);
  const [autoLocated, setAutoLocated] = useState(false);
  
  const { results: fromStopResults, loading: fromLoading } = useStopSearch(fromLocation, 800, mapCenter, mapBbox);
  const { results: toStopResults, loading: toLoading } = useStopSearch(toLocation, 800, mapCenter, mapBbox);

  const { alerts: serviceAlerts, loading: alertsLoading } = useServiceAlerts();
  const { data: liveAlertUpdate, isConnected: alertsConnected } = useLiveUpdates("alerts");
  const [liveAlerts, setLiveAlerts] = useState([]);
  // Array of { card, routeGeometries } — one entry per alternative route
  const [routeOptions, setRouteOptions] = useState([]);
  // Index of the card the user has clicked / selected (controls map geometry)
  const [selectedRouteIdx, setSelectedRouteIdx] = useState(null);
  const [showSuggested, setShowSuggested] = useState(false);
  // Geometry drawn on the map: prefer embedded per-leg geometry (legs[].geometry)
  // from the journey response. Only fall back to prefetched `routeGeometries`
  // (from per-leg /route/leg-geometry calls) when embedded geometry is missing.
  const journeyRoute = (() => {
    if (typeof selectedRouteIdx !== 'number') return null;
    const opt = routeOptions?.[selectedRouteIdx];
    if (!opt) return null;

    // Debug: show what geometry containers exist for the selected option.
    // This is meant for diagnosing cases where the backend returns walking
    // segments but the map only receives a bus segment.
    try {
      // eslint-disable-next-line no-console
      console.debug('[journeyRoute] option geometry containers', {
        selectedRouteIdx,
        optHasRouteGeometries: Array.isArray(opt?.routeGeometries) ? opt.routeGeometries.length : 0,
        optRouteHasRouteGeometries: Array.isArray(opt?.route?.routeGeometries) ? opt.route.routeGeometries.length : 0,
        sourceRouteGeometriesLens: (() => {
          const srcVals = opt?.sources ? Object.values(opt.sources) : [];
          const out = [];
          for (const s of srcVals) {
            const j = s?.route?.route ? s.route.route : (s?.route ? s.route : s);
            out.push(Array.isArray(j?.routeGeometries) ? j.routeGeometries.length : 0);
          }
          return out;
        })(),
      });
    } catch (_e) {
      // ignore
    }

    // Pull the selected plan in a consistent way across /journey/plan and /journey/compare.
    // In compare mode, opt.sources is a map from label -> {router?, route:{legs...}}.
    // Some labels may be placeholders (no route). Don’t assume the first entry has legs.
    const findLegs = (option) => {
      // Prefer any explicit plan attached on the option
      if (Array.isArray(option?.legs) && option.legs.length > 0) return option.legs;
      if (option?.route && Array.isArray(option.route.legs) && option.route.legs.length > 0) return option.route.legs;

      const srcVals = option?.sources ? Object.values(option.sources) : [];
      for (const src of srcVals) {
        if (!src) continue;
        if (src.route && Array.isArray(src.route.legs) && src.route.legs.length > 0) return src.route.legs;
        if (Array.isArray(src.legs) && src.legs.length > 0) return src.legs;
        // Some responses nest as {route:{route:{legs}}}
        if (src.route && src.route.route && Array.isArray(src.route.route.legs) && src.route.route.legs.length > 0) return src.route.route.legs;
      }
      return null;
    };

    const legs = findLegs(opt);

    // Prefer routeGeometries from the underlying journey object when present.
    // In compare mode, the selected option may store the journey under:
    // - opt.route (we inject this when building routeOptions)
    // - opt.sources[label].route (or route.route)
    // This avoids the embedded-legs geometry path which can be bus-only.
    const findRouteGeometries = (option) => {
      try {
        if (Array.isArray(option?.routeGeometries) && option.routeGeometries.length > 0) return option.routeGeometries;
        if (Array.isArray(option?.route?.routeGeometries) && option.route.routeGeometries.length > 0) return option.route.routeGeometries;
        // Some shapes put routeGeometries directly on the journey object.
        if (Array.isArray(option?.route?.routeGeometries) && option.route.routeGeometries.length > 0) return option.route.routeGeometries;
        const srcVals = option?.sources ? Object.values(option.sources) : [];
        for (const src of srcVals) {
          const j = src?.route?.route ? src.route.route : (src?.route ? src.route : src);
          if (Array.isArray(j?.routeGeometries) && j.routeGeometries.length > 0) return j.routeGeometries;
        }
      } catch (_e) {
        // ignore
      }
      return null;
    };

    const preferredGeos = findRouteGeometries(opt);

    // Extract start/destination from whichever nested journey object we can
    // find. We attach these to the segments array so MapViewMap can place
    // endpoint markers at the true requested origin/destination instead of
    // guessing from polyline endpoints.
    const extractMetaEndpoints = (option) => {
      try {
        // Prefer explicit plan attached on the option
        if (option?.meta && option.meta.start_point && option.meta.destination) {
          return option.meta;
        }
        if (option?.route?.meta && option.route.meta.start_point && option.route.meta.destination) {
          return option.route.meta;
        }
        const srcVals = option?.sources ? Object.values(option.sources) : [];
        for (const src of srcVals) {
          if (!src) continue;
          if (src?.route?.meta) return src.route.meta;
          if (src?.meta) return src.meta;
          if (src?.route?.route?.meta) return src.route.route.meta;
        }
      } catch (_e) {
        // ignore
      }
      return null;
    };

    const meta = extractMetaEndpoints(opt);
    const startPoint = (meta && Array.isArray(meta.start_point) && meta.start_point.length >= 2) ? meta.start_point : null;
    const destinationPoint = (meta && Array.isArray(meta.destination) && meta.destination.length >= 2) ? meta.destination : null;

    // Build segments directly from legs[].geometry when present.
    // NOTE: we only do this when routeGeometries is not available, because
    // routeGeometries includes explicit walking segments.
    try {
      if ((!preferredGeos || preferredGeos.length === 0) && legs && legs.length > 0) {
        const segments = [];
        let startFromMeta = null;
        let endFromMeta = null;
        try {
          // Pull start/destination from the selected journey meta (compare/plan)
          // so the map start marker reflects the true requested origin.
          const m = extractMetaEndpoints(opt) || {};
          const sp = m?.start_point;
          const dp = m?.destination;
          if (Array.isArray(sp) && sp.length >= 2 && Number.isFinite(sp[0]) && Number.isFinite(sp[1])) {
            startFromMeta = [sp[0], sp[1]];
          }
          if (Array.isArray(dp) && dp.length >= 2 && Number.isFinite(dp[0]) && Number.isFinite(dp[1])) {
            endFromMeta = [dp[0], dp[1]];
          }
        } catch (_e) {
          startFromMeta = null;
          endFromMeta = null;
        }

        for (let i = 0; i < legs.length; i++) {
          const leg = legs[i];
          const rawMode = (leg?.mode && String(leg.mode).toLowerCase()) || '';
          const isWalk = rawMode === 'walking' || rawMode === 'walk';
          const mode = rawMode || (leg?.line_name ? 'transit' : 'walking');
		  const fromPt = (leg?.from_stop && Array.isArray(leg.from_stop.coords) && leg.from_stop.coords.length >= 2) ? leg.from_stop.coords : null;
		  const toPt = (leg?.to_stop && Array.isArray(leg.to_stop.coords) && leg.to_stop.coords.length >= 2) ? leg.to_stop.coords : null;
          const embeddedCoords = leg?.geometry && Array.isArray(leg.geometry.coords) ? leg.geometry.coords : null;
          if (!embeddedCoords || embeddedCoords.length < 2) continue;
          const norm = normalizeCoords(embeddedCoords);
          if (norm.length < 2) continue;
          segments.push({
            id: isWalk ? `walk-${i}` : `seg-${i}`,
            name: leg?.line_name || mode || `Segment ${i}`,
            coords: norm,
            color: isWalk ? '#888888' : '#1a73e8',
            mode,
            isWalk,
			  // These are used by the map to clip full-route tracks to the leg
			  // and to mark intermediate stops.
			  _from: fromPt ? [fromPt[0], fromPt[1]] : null,
			  _to: toPt ? [toPt[0], toPt[1]] : null,
          });
        }
        // Force the visual start/end markers to match the requested start/destination
        // when the backend provides them (journey.meta.start_point / destination).
        try {
          if (segments.length > 0 && startFromMeta) {
            const first = segments[0];
            if (first && Array.isArray(first.coords) && first.coords.length > 0) {
              first.coords[0] = startFromMeta;
            }
          }
          if (segments.length > 0 && endFromMeta) {
            const last = segments[segments.length - 1];
            if (last && Array.isArray(last.coords) && last.coords.length > 0) {
              last.coords[last.coords.length - 1] = endFromMeta;
            }
          }
        } catch (_e) {
          // best-effort only
        }
        if (segments.length > 0) {
          try {
            if (startPoint) segments._start = [startPoint[0], startPoint[1]];
            if (destinationPoint) segments._end = [destinationPoint[0], destinationPoint[1]];
          } catch (_e) {
            // ignore
          }
          return segments;
        }
      }
    } catch (_e) {
      // If anything goes wrong, fall back to prefetched routeGeometries below.
    }

    // Fall back to backend-provided routeGeometries. These already include
    // walking segments, but we still normalize and tag them so MapViewMap
    // can render walking differently.
    try {
      // In some formats (especially compare mode), routeGeometries may be nested.
      // Prefer the most specific one we can find.
      const geos = preferredGeos;
      if (geos && geos.length > 0) {
        // Try to obtain start/end from meta for start-marker correctness.
        let startFromMeta = null;
        let endFromMeta = null;
        try {
          const srcVals = opt?.sources ? Object.values(opt.sources) : [];
          let j = null;
          for (const src of srcVals) {
            if (src?.route?.meta) { j = src.route; break; }
            if (src?.meta) { j = src; break; }
          }
          const meta = j?.meta || {};
          const sp = meta?.start_point;
          const dp = meta?.destination;
          if (Array.isArray(sp) && sp.length >= 2 && Number.isFinite(sp[0]) && Number.isFinite(sp[1])) startFromMeta = [sp[0], sp[1]];
          if (Array.isArray(dp) && dp.length >= 2 && Number.isFinite(dp[0]) && Number.isFinite(dp[1])) endFromMeta = [dp[0], dp[1]];
        } catch (_e) {
          startFromMeta = null;
          endFromMeta = null;
        }

        const segs = [];
        for (let i = 0; i < geos.length; i++) {
          const g = geos[i];
          if (!g || !Array.isArray(g.coords) || g.coords.length < 2) continue;
          const norm = normalizeCoords(g.coords);
          if (norm.length < 2) continue;
          const rawMode = (g?.mode && String(g.mode).toLowerCase()) || '';
          const isWalk = rawMode === 'walking' || rawMode === 'walk' || (typeof g?.id === 'string' && g.id.startsWith('walk-'));

          // Prefer explicit per-segment endpoints if attachEndpoints() added them,
          // otherwise fall back to the geometry endpoints.
          const fromPt = (g?._from && Array.isArray(g._from) && g._from.length >= 2) ? [g._from[0], g._from[1]] : null;
          const toPt = (g?._to && Array.isArray(g._to) && g._to.length >= 2) ? [g._to[0], g._to[1]] : null;
          const geoFrom = (norm && norm.length > 0) ? norm[0] : null;
          const geoTo = (norm && norm.length > 0) ? norm[norm.length - 1] : null;
          segs.push({
            ...(g || {}),
            coords: norm,
            mode: rawMode || (isWalk ? 'walking' : 'transit'),
            isWalk,
            color: isWalk ? '#888888' : (g?.color || '#1a73e8'),
            _from: fromPt || (geoFrom ? [geoFrom[0], geoFrom[1]] : null),
            _to: toPt || (geoTo ? [geoTo[0], geoTo[1]] : null),
          });
        }
        // Force route endpoints to match requested origin/destination when present.
        try {
          if (segs.length > 0 && startFromMeta) {
            const first = segs[0];
            if (first?.coords?.length > 0) first.coords[0] = startFromMeta;
          }
          if (segs.length > 0 && endFromMeta) {
            const last = segs[segs.length - 1];
            if (last?.coords?.length > 0) last.coords[last.coords.length - 1] = endFromMeta;
          }
        } catch (_e) {
          // ignore
        }
        if (segs.length > 0) {
          try {
            if (startPoint) segs._start = [startPoint[0], startPoint[1]];
            if (destinationPoint) segs._end = [destinationPoint[0], destinationPoint[1]];
          } catch (_e) {
            // ignore
          }
          return segs;
        }
      }
    } catch (_e) {
      // ignore
    }

    return opt.routeGeometries ?? null;
  })();

  // State for showing router/label details when a label chip is clicked
  const [labelDetail, setLabelDetail] = useState({ open: false, label: '', content: null });

  /** Called by MapViewMap whenever the user finishes panning / zooming. */
  const handleMoveEnd = useCallback(({ lat, lon, bounds }) => {
    setMapCenter({ lat, lon });
    try {
      if (bounds && typeof bounds === 'object') {
        // supports either {south, west, north, east} or a Leaflet LatLngBounds-like object
        const south = typeof bounds.south === 'number' ? bounds.south : (typeof bounds.getSouth === 'function' ? bounds.getSouth() : null);
        const west = typeof bounds.west === 'number' ? bounds.west : (typeof bounds.getWest === 'function' ? bounds.getWest() : null);
        const north = typeof bounds.north === 'number' ? bounds.north : (typeof bounds.getNorth === 'function' ? bounds.getNorth() : null);
        const east = typeof bounds.east === 'number' ? bounds.east : (typeof bounds.getEast === 'function' ? bounds.getEast() : null);
        if ([south, west, north, east].every((v) => typeof v === 'number')) {
          setMapBbox({ south, west, north, east });
        }
      }
    } catch (_e) {
      // ignore bbox tracking errors
    }
  }, []);

  const handleUseMyLocation = useCallback(() => {
    if (!navigator || !navigator.geolocation) {
      setGeoError('Geolocation not supported by your browser');
      setTimeout(() => setGeoError(null), 4000);
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const lat = pos.coords.latitude;
        const lon = pos.coords.longitude;
        const loc = {
          name: 'My location',
          display_name: 'My location',
          lat,
          lon,
          type: 'location',
        };
        setSelectedFromStop(loc);
        setFromLocation('My location');
        setMapCenter({ lat, lon });
        // also set userLocation for map-level centering/follow
        setUserLocation([lat, lon]);
        setLocationStatus('granted');
        setLocationError(null);
        setGeoError(null);
      },
      (err) => {
        setGeoError(err.message || 'Failed to get location');
        setLocationStatus('error');
        setLocationError(err.message || 'Failed to get location');
        setTimeout(() => setGeoError(null), 4000);
      },
      { enableHighAccuracy: true, timeout: 10000 }
    );
  }, [setSelectedFromStop, setFromLocation, setMapCenter]);

  // Auto-fill the From field with the user's current location once on first load
  useEffect(() => {
    if (autoLocated) return;
    if (!navigator || !navigator.geolocation) {
      setAutoLocated(true);
      return;
    }
    // Try to get a quick fix; failure should be silent (user can click the button)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const lat = pos.coords.latitude;
        const lon = pos.coords.longitude;
        const loc = {
          name: 'My location',
          display_name: 'My location',
          lat,
          lon,
          type: 'location',
        };
        setSelectedFromStop(loc);
        setFromLocation('My location');
        setUserLocation([lat, lon]);
        try {
          setMapCenter({ lat, lon });
        } catch (e) {
          // ignore if mapCenter setter not ready
        }
        setLocationStatus('granted');
        setLocationError(null);
        setAutoLocated(true);
      },
      () => {
        // on error, mark attempted so we don't keep asking
        setAutoLocated(true);
      },
      { enableHighAccuracy: false, timeout: 5000, maximumAge: 60000 }
    );
  }, [autoLocated, setSelectedFromStop, setFromLocation, setUserLocation, setMapCenter]);

  const requestLocation = useCallback(() => {
    if (!navigator || !navigator.geolocation) {
      setLocationStatus('error');
      setLocationError('Geolocation not supported by your browser');
      setTimeout(() => setLocationError(null), 4000);
      return;
    }
    setLocationStatus('loading');
    setLocationError(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const coords = [pos.coords.latitude, pos.coords.longitude];
        setUserLocation(coords);
        setLocationStatus('granted');
        setLocationError(null);
        // centre map if we have the instance
        if (mapInstance && typeof mapInstance.flyTo === 'function') {
          try {
            mapInstance.flyTo(coords, Math.max(mapInstance.getZoom(), 12), { duration: 1.0 });
          } catch (e) {
            // ignore
          }
        }
      },
      (err) => {
        setLocationStatus('error');
        setLocationError(err?.message || 'Location permission denied');
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
    );
  }, [mapInstance]);

  const handleCenterOnUser = useCallback(() => {
    if (!userLocation || !mapInstance) return;
    try {
      mapInstance.flyTo(userLocation, Math.max(mapInstance.getZoom(), 12), { duration: 1.0 });
    } catch (e) {
      // ignore
    }
  }, [userLocation, mapInstance]);

  // Debounced live bus data tied to the current map center
  const {
    data: busLocations,
    loading: busLoading,
    refreshing: busRefreshing,
    countdown: busCountdown,
    refreshInterval: busRefreshInterval,
    error: busError,
  } = useLiveBusLocations("SCCU", {
    lat: mapCenter.lat,
    lon: mapCenter.lon,
    refreshInterval: 20000,
    // Don't auto-refresh during zoom/pan; only refresh when the timer is up.
    debounceOnMove: false,
  });
  const { data: trainDepartures, loading: trainLoading, error: trainError } = useLiveDepartures("LAN", 180000);

  // Update markers when real bus API data arrives
  useEffect(() => {
    if (
      (Array.isArray(busLocations) && busLocations.length > 0) ||
      (Array.isArray(trainDepartures) && trainDepartures.length > 0)
    ) {
      const newMarkers = [];
      let id = 1;
      if (Array.isArray(busLocations)) {
        busLocations.forEach((bus) => {
          const lat = bus.latitude ?? bus.lat;
          const lon = bus.longitude ?? bus.lon;
          // copy backend-provided fields into meta but exclude coords
          const meta = { ...bus };
          // Remove coordinate fields and any fields already shown in the header
          delete meta.lat;
          delete meta.lon;
          delete meta.latitude;
          delete meta.longitude;
          // Avoid duplicating displayed fields in the popup
          delete meta.line;
          delete meta.destination;

          // Extract operator robustly and keep it separate from the display name
          let operatorName = null;
          if (bus?.operator) {
            if (typeof bus.operator === 'string') operatorName = bus.operator;
            else if (typeof bus.operator === 'object') {
              operatorName = bus.operator.name || bus.operator.operator_name || bus.operator.operatorName || null;
            }
          }
          operatorName = operatorName || bus?.operator_name || bus?.operatorName || bus?.operator_ref || bus?.operatorRef || null;
          // Remove any operator-like keys from meta so it doesn't duplicate
          ['operator', 'operator_name', 'operatorName', 'operator_ref', 'operatorRef'].forEach((k) => delete meta[k]);
          // Remove delay/status keys – shown in the popup header, not in meta
          ['delay_minutes', 'delayMinutes', 'status'].forEach((k) => delete meta[k]);

          // Show destination as the primary label line (e.g. "To: Night Stop").
          // Route/line number is shown separately in the popup pill.
          const displayName = bus.destination
                              ? `To: ${bus.destination}`
                              : (bus.name || (bus.line ? String(bus.line) : `Bus ${bus.id || ''}`));

          const delayMinutes = bus.delay_minutes ?? bus.delayMinutes ?? null;

          // Derive a human-friendly status when the backend doesn't provide one.
          // Rules:
          // - delay >= 2 mins => "Delayed N min(s)"
          // - delay <= -2 mins => "Early N min(s)"
          // - otherwise => "On time"
          const derivedStatus = (() => {
            if (delayMinutes == null || !Number.isFinite(Number(delayMinutes))) return "On time";
            const dm = Number(delayMinutes);
            if (dm >= 2) return `Delayed ${Math.round(dm)} min`;
            if (dm <= -2) return `Early ${Math.round(Math.abs(dm))} min`;
            return "On time";
          })();

          newMarkers.push({
            id: id++,
            position: [lat, lon],
            name: displayName,
            type: "bus",
            status: bus.status || derivedStatus,
            routeNumber: bus.routeNumber || bus.route || bus.line,
            delayMinutes: delayMinutes == null ? null : Number(delayMinutes),
            operator: operatorName,
            bearing: bus.bearing ?? bus.Bearing ?? bus.bearing_degrees ?? null,
            meta: meta,
          });
        });
      }
      if (Array.isArray(trainDepartures)) {
        const stations = {};
        trainDepartures.forEach((service) => {
          // Build new markers with each station having a list of its current services
          if (!stations[service.stationName]) {
            stations[service.stationName] = {
              id: id++,
              name: service.stationName,
              position: [service.lat, service.lon],
              type: 'train',
              services: []
            }
          }

          stations[service.stationName].services.push({
            status: service.status,
            destination: service.destination,
            departureTime: service.departureTime,
            delayMins: service.delayMins,
          });
        });
        newMarkers.push(...Object.values(stations));
      }
      setMarkers(newMarkers);
      // Validate existing popup ownership: if a popup was open, ensure the
      // refreshed markers still represent the same logical vehicle. If not,
      // clear the popup to avoid it appearing on a different bus.
      try {
        if (openPopupId) {
          const found = newMarkers.find((m) => m && m.id === openPopupId);
          if (!found) {
            try { setOpenPopupId(null); } catch (e) { /* ignore */ }
            try { setOpenPopupSignature(null); } catch (e) { /* ignore */ }
          } else if (openPopupSignature) {
            const sig = (found && (found.meta && (found.meta.logged_journey_id || found.meta.journey_id))) || found.routeNumber || `${String(found.id)}|${Math.round((found.position?.[0]||0)*1e5)}|${Math.round((found.position?.[1]||0)*1e5)}`;
            if (sig !== openPopupSignature) {
              try { setOpenPopupId(null); } catch (e) { /* ignore */ }
              try { setOpenPopupSignature(null); } catch (e) { /* ignore */ }
            }
          }
        }
      } catch (e) { /* ignore */ }
    }
  }, [busLocations, trainDepartures]);

  const filteredMarkers = useMemo(
    () => markers.filter((m) => (m.type === "bus" && filters.showBuses) || (m.type === "train" && filters.showTrains)),
    [markers, filters.showBuses, filters.showTrains]
  );

  const nearestStop = useMemo(() => {
    if (!userLocation || !Array.isArray(filteredMarkers) || filteredMarkers.length === 0) return null;
    const toRadians = (deg) => (deg * Math.PI) / 180;
    const R = 6371000;
    const [ulat, ulon] = userLocation;
    let nearest = null;
    for (const marker of filteredMarkers) {
      const [mlat, mlon] = marker.position;
      const dLat = toRadians(mlat - ulat);
      const dLon = toRadians(mlon - ulon);
      const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRadians(ulat)) * Math.cos(toRadians(mlat)) * Math.sin(dLon / 2) ** 2;
      const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
      const dist = R * c;
      if (!nearest || dist < nearest.distance) nearest = { ...marker, distance: dist };
    }
    return nearest;
  }, [userLocation, filteredMarkers]);
  // ---- end map state ----

  const getCoordsFromOption = (option) => {
    if (!option || typeof option === "string") return null;
    const lat = option.lat ?? option.latitude;
    const lon = option.lon ?? option.longitude;
    if (typeof lat !== "number" || typeof lon !== "number") return null;
    return { lat, lon };
  };

  const fromCoords = useMemo(() => getCoordsFromOption(selectedFromStop), [selectedFromStop]);
  const toCoords = useMemo(() => getCoordsFromOption(selectedToStop), [selectedToStop]);

  useEffect(() => {
    if (!liveAlertUpdate) return;
    const updates = Array.isArray(liveAlertUpdate) ? liveAlertUpdate : [liveAlertUpdate];
    const normalized = updates
      .map((alert, idx) => ({
        id: alert?.id || alert?.alertId || Date.now() + "-" + idx,
        severity: alert?.severity || alert?.level || "info",
        message: alert?.message || alert?.description || alert?.text || "Service update",
      }))
      .filter((alert) => alert.message);
    if (normalized.length === 0) return;
    setLiveAlerts((prev) => {
      const merged = [...normalized, ...prev];
      const seen = new Set();
      const deduped = [];
      for (const item of merged) {
        const key = item.severity + "-" + item.message;
        if (seen.has(key)) continue;
        seen.add(key);
        deduped.push(item);
      }
      return deduped.slice(0, 5);
    });
  }, [liveAlertUpdate]);

  // FIXME: 'Departures' only tracks trains - should this use markers instead, for buses + trains?
  const liveDepartures = useMemo(() => {
    if (!Array.isArray(trainDepartures) || trainDepartures.length === 0) {
      return [
        { id: 1, type: "bus", route: "2", destination: "Blackpool", time: "2 mins", status: "On time" },
        { id: 2, type: "train", route: "Northern", destination: "Manchester", time: "5 mins", status: "Delayed 3 mins" },
        { id: 3, type: "bus", route: "100", destination: "Morecambe", time: "8 mins", status: "On time" },
      ];
    }
    // Get the first 3 departures (by scheduled time, not actual departure time)
    return trainDepartures.toSorted((a, b) => a.scheduledTime ? a.scheduledTime - b.scheduledTime : 0).slice(0, 3).map((dep, idx) => ({
      id: idx + 1,
      type: dep.type || "train",
      route: dep.routeNumber || dep.route || (dep.stationName ? "From: " + dep.stationName : "\u2014"),
      destination: dep.destination || dep.to || "Unknown",
      time: dep.departureTime ?? (dep.minutesToDeparture ? dep.minutesToDeparture + " mins" : dep.time || "\u2014"),
      status: dep.status || (dep.delayMinutes ? "Delayed " + dep.delayMinutes + " mins" : "On time"),
    }));
  }, [trainDepartures]);

  const alerts = useMemo(() => {
    const apiAlerts =
      Array.isArray(serviceAlerts) && serviceAlerts.length > 0
        ? serviceAlerts.slice(0, 3).map((alert, idx) => ({
            id: alert?.id || idx + 1,
            severity: alert?.severity || "info",
            message: alert?.message || alert?.description || "Service update",
          }))
        : [];
    const combined = [...liveAlerts, ...apiAlerts];
    if (combined.length > 0) return combined.slice(0, 3);
    return [
      { id: 1, severity: "warning", message: "M6 delays between J33-J36: 15 mins" },
      { id: 2, severity: "info", message: "Bus route 2 diversion via King Street" },
    ];
  }, [serviceAlerts, liveAlerts]);

  const allStops = useMemo(() => {
    const buildOptions = (results) => {
      if (!Array.isArray(results) || results.length === 0) return [];
      const strings = [];
      const stops = [];
      const locations = [];
      for (const r of results) {
        if (typeof r === "string") strings.push(r);
        else if (r && r.type === "location") locations.push(r);
        else stops.push(r);
      }
      return [...strings, ...stops, ...locations];
    };

    const fromResults = fromLoading ? [] : buildOptions(fromStopResults);
    const toResults = toLoading ? [] : buildOptions(toStopResults);
    return { from: fromResults, to: toResults };
  }, [fromLoading, toLoading, fromStopResults, toStopResults]);

  // Debug: log Autocomplete options to help diagnose missing stop entries
  useEffect(() => {
    try {
      // Print concise option summaries to the console for debugging in dev
      const summarize = (arr) => (Array.isArray(arr) ? arr.map((o) => {
        if (typeof o === 'string') return { type: 'string', label: o };
        return { type: o.type || 'stop', label: o.display_name || o.name || o.atco_code || '' };
      }) : []);
      // eslint-disable-next-line no-console
      console.log('DEBUG autocomplete FROM options:', summarize(allStops.from));
      // eslint-disable-next-line no-console
      console.log('DEBUG autocomplete TO options:', summarize(allStops.to));
    } catch (e) {
      // ignore during production or tests
    }
  }, [allStops.from, allStops.to]);

  const handleSearch = async () => {
    if (!fromCoords || !toCoords) return;
    setShowSuggested(true);
    setIsSearching(true);
  setRouteOptions([]);
    try {
      const isoString = new Date(`${departureDate}T${departureClock}:00`).toISOString();
      const apiMode = transportMode === 'all' ? 'combined' : transportMode;

      // Instead of running multiple transfer-variant requests, call the
      // /journey/compare endpoint once. That returns results from multiple
      // router implementations (main/original, eco, lazy, greedy). We'll
      // display those results (one slot per router) and label them accordingly.
    const compareResp = await compareRouters(fromCoords, toCoords, isoString, { maxTransfers: maxTransfers, mode: apiMode });

      // Desired display order (human-friendly)
      const routerOrder = [
        { key: 'main', label: 'E·ARR' },
        { key: 'eco', label: 'Eco' },
        { key: 'cosy', label: 'COSY' },
        { key: 'lazy', label: 'LAZY' },
        { key: 'greedy', label: 'Greedy' },
      ];

      // Build options but merge identical journeys (same steps) and collect their labels.
      const optionsMap = new Map(); // signature -> option

      // Helper: attach from/to coords from journey.legs to the returned routeGeometries
      const attachEndpoints = (journey) => {
        if (!journey) return [];
        const geosIn = Array.isArray(journey.routeGeometries) ? journey.routeGeometries : [];
        const geos = geosIn.map((g) => ({ ...(g || {}) }));
        const legs = Array.isArray(journey.legs) ? journey.legs : [];

        // If the backend provided an explicit leg_idx for each geometry segment,
        // use it to align geometries to legs deterministically.
        //
        // This avoids the “index drift” bug where a valid bus fragment is
        // accidentally attached to the wrong leg’s endpoints.
        const hasLegIdx = geos.some((g) => Number.isInteger(g?.leg_idx));
        if (hasLegIdx) {
          const byLegIdx = new Map();
          geos.forEach((g) => {
            if (Number.isInteger(g?.leg_idx) && !byLegIdx.has(g.leg_idx)) {
              byLegIdx.set(g.leg_idx, g);
            }
          });
          const out = [];
          for (let i = 0; i < legs.length; i++) {
            const leg = legs[i];
            const g = byLegIdx.get(i);
            if (!g) continue;

            // Attach endpoints from leg if missing.
            const fs = leg?.from_stop;
            const ts = leg?.to_stop;
            if (fs && ts && typeof fs.lat === 'number' && typeof fs.lon === 'number' && typeof ts.lat === 'number' && typeof ts.lon === 'number') {
              const from = [fs.lat, fs.lon];
              const to = [ts.lat, ts.lon];
              if (!g._from) g._from = from;
              if (!g._to) g._to = to;
            }

            // Ensure geometry.mode is set for styling.
            if (!g.mode && leg?.mode) g.mode = String(leg.mode).toLowerCase();
            out.push(g);
          }

          // Defensive: include any geometry segments that reference a leg_idx
          // outside the legs[] range.
          geos.forEach((g) => {
            if (Number.isInteger(g?.leg_idx) && (g.leg_idx < 0 || g.leg_idx >= legs.length)) {
              out.push(g);
            }
          });

          // Sort by leg_idx so map drawing stays in travel order.
          out.sort((a, b) => {
            const ai = Number.isInteger(a?.leg_idx) ? a.leg_idx : 1e9;
            const bi = Number.isInteger(b?.leg_idx) ? b.leg_idx : 1e9;
            return ai - bi;
          });
          return out;
        }

        // If backend didn't include geometry for walking legs, synthesize a simple
        // 2-point segment so walking is still visible and styled correctly.
        // We preserve the original travel order based on legs.
        const out = [];
        let geoIdx = 0;

        const geoLooksLikeWalking = (g) => {
          try {
            const m = (g?.mode && String(g.mode).toLowerCase()) || '';
            if (m === 'walking' || m === 'walk') return true;
            if (typeof g?.id === 'string' && g.id.startsWith('walk-')) return true;
          } catch (_e) {
            // ignore
          }
          return false;
        };

        const legIsWalking = (leg) => {
          const m = (leg?.mode && String(leg.mode).toLowerCase()) || '';
          return m === 'walking' || m === 'walk';
        };

        const endpointPairFromLeg = (leg) => {
          const fs = leg?.from_stop;
          const ts = leg?.to_stop;
          if (!fs || !ts) return null;
          if (typeof fs.lat !== 'number' || typeof fs.lon !== 'number') return null;
          if (typeof ts.lat !== 'number' || typeof ts.lon !== 'number') return null;
          return {
            from: [fs.lat, fs.lon],
            to: [ts.lat, ts.lon],
          };
        };

        for (let i = 0; i < legs.length; i++) {
          const leg = legs[i];
          const isWalkLeg = legIsWalking(leg);

          // Find the next geometry segment (if any)
          const g = geoIdx < geos.length ? geos[geoIdx] : null;
          const gIsWalk = g ? geoLooksLikeWalking(g) : false;

          // If leg is walking but next geometry isn't walking (or missing), synthesize.
          if (isWalkLeg && (!g || !gIsWalk)) {
            const ep = endpointPairFromLeg(leg);
            if (ep) {
              out.push({
                id: `walk-${i}`,
                name: 'Walk',
                mode: 'walking',
                color: '#888888',
                dash: true,
                coords: [ep.from, ep.to],
                _from: ep.from,
                _to: ep.to,
              });
              continue;
            }
            // If endpoints missing, just skip synthesizing and fall through.
          }

          // For a walking leg, only consume a walking geometry segment.
          // For a non-walking leg, only consume a non-walking geometry segment.
          // If we consume the wrong kind, segments get misaligned (e.g. a bus
          // fragment gets anchored to the start/hop-off stops of a different leg).
          if (g && ((isWalkLeg && gIsWalk) || (!isWalkLeg && !gIsWalk))) {
            const ep = endpointPairFromLeg(leg);
            if (ep) {
              if (!g._from) g._from = ep.from;
              if (!g._to) g._to = ep.to;
            }
            // Ensure mode is set so map can style walking
            if (!g.mode) {
              g.mode = isWalkLeg ? 'walking' : ((leg?.mode && String(leg.mode).toLowerCase()) || 'transit');
            }
            out.push(g);
            geoIdx += 1;
          } else if (!isWalkLeg) {
            // If we're missing a transit segment (or the next geometry is walking),
            // skip consumption here; the map will fall back to embedded per-leg
            // geometry or straight endpoint lines.
          }
        }

        // Append any remaining geometry segments (defensive)
        while (geoIdx < geos.length) {
          out.push(geos[geoIdx]);
          geoIdx += 1;
        }

        return out;
      };

      const makeSignature = (card) => {
        if (!card || !Array.isArray(card.steps)) return JSON.stringify(card || {});
        return card.steps.map(s => `${s.type}|${s.route}|${s.from}|${s.to}|${s.duration}`).join('||');
      };

      routerOrder.forEach((item) => {
        const res = compareResp[item.key];
        if (res && res.route) {
          const journey = res.route || res;
          const card = journeyToRouteCard(journey);
          if (card) {
            const sig = makeSignature(card);
            if (optionsMap.has(sig)) {
              const existing = optionsMap.get(sig);
              if (!existing.labels.includes(item.label)) {
                existing.labels.push(item.label);
                existing.label = existing.labels.join(' · ');
                // record the per-label source for detail view
                existing.sources = existing.sources || {};
                existing.sources[item.label] = res;
              }
            } else {
              const id = optionsMap.size + 1;
              optionsMap.set(sig, {
                id,
                card: { ...card, id },
                routeGeometries: attachEndpoints(journey),
                // Some parts of the UI (notably the map selector) will look for
                // nested routeGeometries under `option.route.routeGeometries`.
                // Keep them in sync so walking segments are never dropped.
                route: { ...(journey || {}), routeGeometries: attachEndpoints(journey) },
                labels: [item.label],
                label: item.label,
                sources: { [item.label]: res },
              });
            }
            return;
          }
        }

        // No route returned for this router — treat as a placeholder but try to merge
        const placeholder = {
          id: null,
          card: {
            id: null,
            duration: 'No route found',
            transfers: maxTransfers,
            steps: [],
            walkMinutes: 0,
          },
          routeGeometries: [],
          labels: [item.label],
          label: item.label,
        };
        const sig = `__placeholder__${item.label}`;
        if (optionsMap.has(sig)) {
          const existing = optionsMap.get(sig);
          if (!existing.labels.includes(item.label)) {
            existing.labels.push(item.label);
            existing.label = existing.labels.join(' · ');
            existing.sources = existing.sources || {};
            existing.sources[item.label] = res;
          }
        } else {
          const id = optionsMap.size + 1;
          placeholder.id = id;
          placeholder.card.id = id;
          // attach sources map even for placeholders so the UI can show detail
          placeholder.sources = { [item.label]: res };
          optionsMap.set(sig, placeholder);
        }
      });

      const options = Array.from(optionsMap.values());

      // Debug: print a per-leg geometry alignment report so we can see exactly
      // which polylines the map will draw for each option.
      try {
        const debug = (import.meta?.env?.VITE_DEBUG_JOURNEY_ROUTE === '1');
        if (debug) {
          const summarizeOpt = (opt) => {
            const srcs = opt?.sources ? Object.values(opt.sources) : [];
            const primarySrc = srcs && srcs.length > 0 ? srcs[0] : null;
            const journey = primarySrc ? (primarySrc.route ? primarySrc.route : primarySrc) : (opt?.route || opt);
            const legs = Array.isArray(journey?.legs) ? journey.legs : [];
            const geos = Array.isArray(opt?.routeGeometries)
              ? opt.routeGeometries
              : (Array.isArray(journey?.routeGeometries) ? journey.routeGeometries : []);

            const byLegIdx = new Map();
            geos.forEach((g) => {
              if (Number.isInteger(g?.leg_idx) && !byLegIdx.has(g.leg_idx)) byLegIdx.set(g.leg_idx, g);
            });

            const rows = legs.map((leg, i) => {
              const fs = leg?.from_stop || {};
              const ts = leg?.to_stop || {};
              const g = byLegIdx.get(i);
              const coordsLen = Array.isArray(g?.coords) ? g.coords.length : null;
              return {
                i,
                legMode: leg?.mode,
                from: fs?.name,
                to: ts?.name,
                legGeoSource: leg?.geometry_source,
                geoMode: g?.mode,
                geoCoords: coordsLen,
                geoFromId: g?.from_stop_id,
                geoToId: g?.to_stop_id,
                geoSource: g?.source,
              };
            });

            // eslint-disable-next-line no-console
            console.debug('[journeyRoute] geometry alignment', {
              optLabel: opt?.label,
              optLabels: opt?.labels,
              legs: legs.length,
              geos: geos.length,
              hasLegIdx: geos.some((g) => Number.isInteger(g?.leg_idx)),
              rows,
            });
          };

          options.forEach((opt) => summarizeOpt(opt));
        }
      } catch (_e) {
        // ignore
      }

  // Add special labels:
  // - "E·DEP": mark options with the earliest initial departure
  // - "FASTEST": mark options with the smallest totalSeconds
  // - "E·ARR": mark options with the earliest arrival
      //
      // Helper: compute departure seconds (day-shift aware) from a source or card
      const getDepSecs = (o) => {
        // Try source meta first — backend sends initial_departure_time (string)
        // and initial_departure_day_offset (integer).
        const srcs = o.sources ? Object.values(o.sources) : [];
        for (const s of srcs) {
          if (s && s.meta && s.meta.initial_departure_time) {
            const parts = String(s.meta.initial_departure_time).split('(')[0].trim().split(':').map(Number);
            if (parts.length >= 2 && Number.isFinite(parts[0]) && Number.isFinite(parts[1])) {
              const secs = parts[0] * 3600 + parts[1] * 60 + (parts[2] || 0);
              const dayOff = Number.isFinite(s.meta.initial_departure_day_offset) ? s.meta.initial_departure_day_offset : 0;
              return secs + dayOff * 86400;
            }
          }
        }
        // Fallback: card values (already parsed with day shift by journeyToRouteCard)
        if (o.card && Number.isFinite(o.card.initialDepartureSecs)) {
          const dayShift = Number.isFinite(o.card.initialDepartureDayShift) ? Number(o.card.initialDepartureDayShift) : 0;
          return Number(o.card.initialDepartureSecs) + dayShift * 86400;
        }
        return null;
      };

      // Helper: compute arrival seconds (day-shift aware) from a source or card
      const getArrSecs = (o) => {
        // Prefer card.finalArrivalSecs which is already day-shift adjusted
        if (o.card && Number.isFinite(o.card.finalArrivalSecs)) {
          return Number(o.card.finalArrivalSecs);
        }
        // Fallback: departure + total duration
        const dep = getDepSecs(o);
        const dur = (o.card && Number.isFinite(o.card.totalSeconds)) ? Number(o.card.totalSeconds) : null;
        // Also check source meta for total_duration_seconds
        const srcs = o.sources ? Object.values(o.sources) : [];
        for (const s of srcs) {
          if (s && s.meta && Number.isFinite(s.meta.total_duration_seconds)) {
            if (Number.isFinite(dep)) return dep + Number(s.meta.total_duration_seconds);
          }
        }
        if (Number.isFinite(dep) && Number.isFinite(dur)) return dep + dur;
        return null;
      };

      try {
          // E·DEP: earliest departure
        const withInit = options.map((o) => ({ opt: o, init: getDepSecs(o) }));
        const initVals = withInit.map((w) => w.init).filter((v) => Number.isFinite(v));
        if (initVals.length > 0) {
          const minInit = Math.min(...initVals);
          const INIT_TOL = 10; // seconds tolerance for earliest-departure ties
          for (const w of withInit) {
            if (Number.isFinite(w.init) && Math.abs(w.init - minInit) <= INIT_TOL) {
              if (!w.opt.labels.includes('E·DEP')) w.opt.labels.unshift('E·DEP');
            }
          }
        }

        // FASTEST: compare numeric totalSeconds on the card
        const totalVals = options.map((o) => (o.card && Number.isFinite(o.card.totalSeconds) ? o.card.totalSeconds : null)).filter((v) => Number.isFinite(v));
        if (totalVals.length > 0) {
          const minTotal = Math.min(...totalVals);
          const EPS = 10; // seconds tolerance for "equally fastest"
          for (const o of options) {
            if (o.card && Number.isFinite(o.card.totalSeconds) && Math.abs(o.card.totalSeconds - minTotal) <= EPS) {
              if (!o.labels.includes('FASTEST')) o.labels.push('FASTEST');
            }
          }
        }

  // E·ARR: earliest arrival
        try {
          const withArr = options.map((o) => ({ opt: o, arr: getArrSecs(o) }));
          const arrVals = withArr.map((w) => w.arr).filter((v) => Number.isFinite(v));
          if (arrVals.length > 0) {
            const minArr = Math.min(...arrVals);
            const EPS_A = 10; // seconds tolerance for ties
            for (const w of withArr) {
              if (Number.isFinite(w.arr) && Math.abs(w.arr - minArr) <= EPS_A) {
                if (!w.opt.labels.includes('E·ARR')) w.opt.labels.unshift('E·ARR');
              }
            }
          }
        } catch (e) {
            // ignore arrival computation failures
          }
      } catch (e) {
  // don't block rendering on label computation failures
  // eslint-disable-next-line no-console
  console.warn('Failed to compute E·DEP/FASTEST labels', e);
      }

      // If compare returned no routes for any router, fall back to a single
      // main /journey/plan call so the user still sees results when compare
      // failed to produce routes.
      const anyFound = options.some((o) => o.card && Array.isArray(o.card.steps) && o.card.steps.length > 0);
      if (!anyFound) {
        try {
          const mainJourney = await getJourneyPlans(fromCoords, toCoords, isoString, { maxTransfers: maxTransfers, mode: apiMode, includeRaw: true });
          // eslint-disable-next-line no-console
          console.debug('RAW /journey/plan response:', mainJourney._raw ?? mainJourney);
          const mainCard = journeyToRouteCard(mainJourney);
          if (mainCard) {
            // Build a single-option array compatible with the compare flow so
            // label computation (E·DEP / FASTEST) runs consistently.
            const singleOpt = {
              id: 1,
              card: { ...mainCard, id: 1 },
              routeGeometries: attachEndpoints(mainJourney),
              labels: ['E·ARR'],
              label: 'E·ARR',
              sources: { 'E·ARR': mainJourney },
            };

            const optionsArr = [singleOpt];
            // Compute E·DEP / FASTEST labels for this single option as well
            // (reuse helpers defined above if in scope, else inline)
            try {
              const withInit = optionsArr.map((o) => ({ opt: o, init: (() => {
                // Use card values (already parsed with day shift by journeyToRouteCard)
                if (o.card && Number.isFinite(o.card.initialDepartureSecs)) {
                  const dayShift = Number.isFinite(o.card.initialDepartureDayShift) ? Number(o.card.initialDepartureDayShift) : 0;
                  return Number(o.card.initialDepartureSecs) + dayShift * 86400;
                }
                return null;
              })() }));

              const initVals = withInit.map((w) => w.init).filter((v) => Number.isFinite(v));
              if (initVals.length > 0) {
                const minInit = Math.min(...initVals);
                for (const w of withInit) {
                  if (Number.isFinite(w.init) && Math.abs(w.init - minInit) <= 10) {
                      if (!w.opt.labels.includes('E·DEP')) w.opt.labels.unshift('E·DEP');
                    }
                }
              }

              const totalVals = optionsArr.map((o) => (o.card && Number.isFinite(o.card.totalSeconds) ? o.card.totalSeconds : null)).filter((v) => Number.isFinite(v));
              if (totalVals.length > 0) {
                const minTotal = Math.min(...totalVals);
                const EPS = 10; // seconds tolerance for "equally fastest"
                for (const o of optionsArr) {
                  if (o.card && Number.isFinite(o.card.totalSeconds) && Math.abs(o.card.totalSeconds - minTotal) <= EPS) {
                    if (!o.labels.includes('FASTEST')) o.labels.push('FASTEST');
                  }
                }
              }
            } catch (e) {
              // ignore label computation failures
            }

            // sort optionsArr so routes with more labels appear first
            const sortedArr = optionsArr.slice().sort((a, b) => {
              const la = Array.isArray(a.labels) ? a.labels.length : (a.label ? 1 : 0);
              const lb = Array.isArray(b.labels) ? b.labels.length : (b.label ? 1 : 0);
              if (lb !== la) return lb - la;
              const ta = a.card && Number.isFinite(a.card.totalSeconds) ? a.card.totalSeconds : Infinity;
              const tb = b.card && Number.isFinite(b.card.totalSeconds) ? b.card.totalSeconds : Infinity;
              return ta - tb;
            });
            // Mark the cheapest priced option so UI can style it
            try {
              const priceVals = sortedArr
                .map((o) => (o.card && o.card.price ? parseFloat(String(o.card.price).replace(/[^0-9.]/g, '')) : null))
                .filter((v) => Number.isFinite(v));
              if (priceVals.length > 0) {
                const minPrice = Math.min(...priceVals);
                for (const o of sortedArr) {
                  if (!o.card) continue;
                  const p = o.card.price ? parseFloat(String(o.card.price).replace(/[^0-9.]/g, '')) : null;
                  o.card = { ...o.card, isCheapest: Number.isFinite(p) && Math.abs(p - minPrice) < 1e-6 };
                }
              }
            } catch (e) {
              // ignore price marking failures
            }
            // Mark best/least values for other comparable metrics so UI can style them
            try {
              // Earliest arrival
              const arrVals = sortedArr
                .map((o) => (o.card && Number.isFinite(o.card.finalArrivalSecs) ? o.card.finalArrivalSecs : null))
                .filter((v) => Number.isFinite(v));
              if (arrVals.length > 0) {
                const minArr = Math.min(...arrVals);
                for (const o of sortedArr) {
                  if (!o.card) continue;
                  const a = o.card && Number.isFinite(o.card.finalArrivalSecs) ? o.card.finalArrivalSecs : null;
                  o.card = { ...o.card, isEarliestArrival: Number.isFinite(a) && Math.abs(a - minArr) <= 10 };
                }
              }
            } catch (e) {
              // ignore
            }
            try {
              // Fastest duration (totalSeconds)
              const totalVals = sortedArr
                .map((o) => (o.card && Number.isFinite(o.card.totalSeconds) ? o.card.totalSeconds : null))
                .filter((v) => Number.isFinite(v));
              if (totalVals.length > 0) {
                const minTotal = Math.min(...totalVals);
                for (const o of sortedArr) {
                  if (!o.card) continue;
                  const t = o.card && Number.isFinite(o.card.totalSeconds) ? o.card.totalSeconds : null;
                  o.card = { ...o.card, isFastestDuration: Number.isFinite(t) && Math.abs(t - minTotal) <= 10 };
                }
              }
            } catch (e) {}
            try {
              // Fewest transfers
              const transVals = sortedArr
                .map((o) => (o.card && Number.isFinite(o.card.transfers) ? o.card.transfers : null))
                .filter((v) => Number.isFinite(v));
              if (transVals.length > 0) {
                const minTrans = Math.min(...transVals);
                for (const o of sortedArr) {
                  if (!o.card) continue;
                  const tr = o.card && Number.isFinite(o.card.transfers) ? o.card.transfers : null;
                  o.card = { ...o.card, isFewestTransfers: Number.isFinite(tr) && tr === minTrans };
                }
              }
            } catch (e) {}
            try {
              // Least walking
              const walkVals = sortedArr
                .map((o) => (o.card && Number.isFinite(o.card.walkMinutes) ? o.card.walkMinutes : null))
                .filter((v) => Number.isFinite(v));
              if (walkVals.length > 0) {
                const minWalk = Math.min(...walkVals);
                for (const o of sortedArr) {
                  if (!o.card) continue;
                  const w = o.card && Number.isFinite(o.card.walkMinutes) ? o.card.walkMinutes : null;
                  o.card = { ...o.card, isLeastWalk: Number.isFinite(w) && w === minWalk };
                }
              }
            } catch (e) {}
            setRouteOptions(sortedArr);
            // Prefetch per-leg OSRM geometry for the first option
            try {
              const firstOpt = sortedArr[0];
              const firstSrc = firstOpt ? Object.values(firstOpt.sources)[0] : null;
              const plan = firstSrc ? (firstSrc.route ? firstSrc.route : firstSrc) : null;
              const planLegs = plan?.legs || [];
              // Some planner responses only expose canonical route_id at the plan level.
              // Attach it to the legs array so geometry fetches can pick it up as a fallback.
              try {
                if (plan && plan.meta && plan.meta.route_id && Array.isArray(planLegs)) {
                  planLegs._route_id = plan.meta.route_id;
                }
              } catch (e) {}
              if (planLegs.length === 0) {
                setSelectedRouteIdx(null);
                setIsSearching(false);
                return;
              }
              const segments = await fetchGeometryForLegs(planLegs);
              if (Array.isArray(segments) && segments.length > 0) {
                const newOptions = sortedArr.slice();
                newOptions[0] = { ...newOptions[0], routeGeometries: segments };
                setRouteOptions(newOptions);
              }
              // Do not auto-select the prefetched geometry — let the user pick
              setIsSearching(false);
              return;
            } catch (e) {
              // If prefetch fails, clear any selection and continue — user may select a route
              // eslint-disable-next-line no-console
              console.warn('Prefetch smoothed geometry failed', e);
              setSelectedRouteIdx(null);
              setIsSearching(false);
              return;
            }
          }
        } catch (e) {
          // ignore and fall back to showing placeholders below
        }
      }

      // Filter out placeholder/no-route cards (routers that returned no route)
      const hasSteps = (o) => o && o.card && Array.isArray(o.card.steps) && o.card.steps.length > 0;
      const displayOptions = options.filter(hasSteps);

      // If none of the routers returned an actual route, show the single "No routes found" message
      // by clearing routeOptions (the JSX below already shows a message when routeOptions.length === 0).
      if (displayOptions.length === 0) {
        setRouteOptions([]);
        setSelectedRouteIdx(null);
        setIsSearching(false);
        return;
      }

      // Show routes ordered by the number of labels (more labels first), then by fastest totalSeconds
      const sortedOptions = displayOptions.slice().sort((a, b) => {
        const la = Array.isArray(a.labels) ? a.labels.length : (a.label ? 1 : 0);
        const lb = Array.isArray(b.labels) ? b.labels.length : (b.label ? 1 : 0);
        if (lb !== la) return lb - la;
        const ta = a.card && Number.isFinite(a.card.totalSeconds) ? a.card.totalSeconds : Infinity;
        const tb = b.card && Number.isFinite(b.card.totalSeconds) ? b.card.totalSeconds : Infinity;
        return ta - tb;
      });
      // Mark the cheapest priced option so UI can style it
      try {
        const priceVals = sortedOptions
          .map((o) => (o.card && o.card.price ? parseFloat(String(o.card.price).replace(/[^0-9.]/g, '')) : null))
          .filter((v) => Number.isFinite(v));
        if (priceVals.length > 0) {
          const minPrice = Math.min(...priceVals);
          for (const o of sortedOptions) {
            if (!o.card) continue;
            const p = o.card.price ? parseFloat(String(o.card.price).replace(/[^0-9.]/g, '')) : null;
            o.card = { ...o.card, isCheapest: Number.isFinite(p) && Math.abs(p - minPrice) < 1e-6 };
          }
        }
      } catch (e) {
        // ignore price marking failures
      }
      // Mark best/least values for other comparable metrics so UI can style them
      try {
        // Earliest arrival
        const arrVals = sortedOptions
          .map((o) => (o.card && Number.isFinite(o.card.finalArrivalSecs) ? o.card.finalArrivalSecs : null))
          .filter((v) => Number.isFinite(v));
        if (arrVals.length > 0) {
          const minArr = Math.min(...arrVals);
          for (const o of sortedOptions) {
            if (!o.card) continue;
            const a = o.card && Number.isFinite(o.card.finalArrivalSecs) ? o.card.finalArrivalSecs : null;
            o.card = { ...o.card, isEarliestArrival: Number.isFinite(a) && Math.abs(a - minArr) <= 10 };
          }
        }
      } catch (e) {}
      try {
        // Fastest duration
        const totalVals = sortedOptions
          .map((o) => (o.card && Number.isFinite(o.card.totalSeconds) ? o.card.totalSeconds : null))
          .filter((v) => Number.isFinite(v));
        if (totalVals.length > 0) {
          const minTotal = Math.min(...totalVals);
          for (const o of sortedOptions) {
            if (!o.card) continue;
            const t = o.card && Number.isFinite(o.card.totalSeconds) ? o.card.totalSeconds : null;
            o.card = { ...o.card, isFastestDuration: Number.isFinite(t) && Math.abs(t - minTotal) <= 10 };
          }
        }
      } catch (e) {}
      try {
        // Fewest transfers
        const transVals = sortedOptions
          .map((o) => (o.card && Number.isFinite(o.card.transfers) ? o.card.transfers : null))
          .filter((v) => Number.isFinite(v));
        if (transVals.length > 0) {
          const minTrans = Math.min(...transVals);
          for (const o of sortedOptions) {
            if (!o.card) continue;
            const tr = o.card && Number.isFinite(o.card.transfers) ? o.card.transfers : null;
            o.card = { ...o.card, isFewestTransfers: Number.isFinite(tr) && tr === minTrans };
          }
        }
      } catch (e) {}
      try {
        // Least walking
        const walkVals = sortedOptions
          .map((o) => (o.card && Number.isFinite(o.card.walkMinutes) ? o.card.walkMinutes : null))
          .filter((v) => Number.isFinite(v));
        if (walkVals.length > 0) {
          const minWalk = Math.min(...walkVals);
          for (const o of sortedOptions) {
            if (!o.card) continue;
            const w = o.card && Number.isFinite(o.card.walkMinutes) ? o.card.walkMinutes : null;
            o.card = { ...o.card, isLeastWalk: Number.isFinite(w) && w === minWalk };
          }
        }
      } catch (e) {}
      setRouteOptions(sortedOptions);
      // Prefetch per-leg geometry for the first displayed option
      try {
        const firstOpt = sortedOptions[0];
        const firstSrc = firstOpt ? Object.values(firstOpt.sources)[0] : null;
        const plan = firstSrc ? (firstSrc.route ? firstSrc.route : firstSrc) : null;
        const planLegs = plan?.legs;
        // Some planner responses only expose canonical route_id at the plan level.
        // Attach it to the legs array so geometry fetches can pick it up as a fallback.
        try {
          if (plan && plan.meta && plan.meta.route_id && Array.isArray(planLegs)) {
            planLegs._route_id = plan.meta.route_id;
          }
        } catch (e) {}
        if (!planLegs || !Array.isArray(planLegs) || planLegs.length === 0) {
          setSelectedRouteIdx(null);
        } else {
          const segments = await fetchGeometryForLegs(planLegs);
          if (segments.length > 0) {
            const newOptions = sortedOptions.slice();
            newOptions[0] = { ...newOptions[0], routeGeometries: segments };
            setRouteOptions(newOptions);
          } else {
            setSelectedRouteIdx(null);
          }
        }
      } catch (e) {
        console.warn('Prefetch per-leg geometry failed', e);
        setSelectedRouteIdx(null);
      }
      // Background: prefetch per-leg geometry for remaining options so
      // vehicle legs follow roads when possible. This updates routeOptions
      // incrementally as OSRM results arrive without blocking selection.
      (async () => {
        try {
          const fetchGeomForOption = async (optIdx) => {
            const opt = sortedOptions[optIdx];
            const src = opt ? Object.values(opt.sources)[0] : null;
            const plan = src ? (src.route ? src.route : src) : null;
            const planLegs = plan?.legs;
            // Some planner responses only expose canonical route_id at the plan level.
            // Attach it to the legs array so geometry fetches can pick it up as a fallback.
            try {
              if (plan && plan.meta && plan.meta.route_id && Array.isArray(planLegs)) {
                planLegs._route_id = plan.meta.route_id;
              }
            } catch (e) {}
            if (!planLegs || !Array.isArray(planLegs) || planLegs.length === 0) return;
            try {
              const segments = await fetchGeometryForLegs(planLegs);
              if (segments.length === 0) return;
              setRouteOptions((prev) => {
                if (!Array.isArray(prev)) return prev;
                const copy = prev.slice();
                copy[optIdx] = { ...copy[optIdx], routeGeometries: segments };
                return copy;
              });
            } catch (err) {
              // ignore per-option failures
            }
          };

          // Fire off prefetches for all non-first options in parallel but non-blocking
          for (let i = 1; i < sortedOptions.length; i++) {
            void fetchGeomForOption(i);
          }
        } catch (err) {
          // ignore
        }
      })();
    } catch (error) {
      console.error('Journey search error:', error);
      setRouteOptions([]);
    } finally {
      setIsSearching(false);
    }
  };

  const handleSaveRoute = (route) => {
    saveFavorite({
      from: selectedFromStop?.code,
      fromName: selectedFromStop?.name,
      to: selectedToStop?.code,
      toName: selectedToStop?.name,
      ...route,
    });
  };

  const isFavorited = (route) => {
    return favorites.some(
      (fav) => fav.from === selectedFromStop?.code && fav.to === selectedToStop?.code && fav.id === route.id
    );
  };

  const handleSelectRoute = async (idx) => {
    // Determine whether this click will select or deselect the route
    const willSelect = selectedRouteIdx !== idx;

    // If deselecting, just clear selection immediately
    if (!willSelect) {
      setSelectedRouteIdx(null);
      try { setLabelDetail({ open: false, label: '', content: null }); setLabelAnchorEl(null); } catch (e) {}
      return;
    }

    const opt = routeOptions?.[idx];
    if (!opt || !opt.sources) {
      setSelectedRouteIdx(idx);
      try { setLabelDetail({ open: false, label: '', content: null }); setLabelAnchorEl(null); } catch (e) {}
      return;
    }

    const firstSrc = Object.values(opt.sources)[0];
    const plan = firstSrc ? (firstSrc.route ? firstSrc.route : firstSrc) : null;
    const planLegs = plan?.legs;

    // Some planner responses only expose canonical route_id at the plan level.
    // Attach it to the legs array so geometry fetches can pick it up as a fallback.
    try {
      if (plan && plan.meta && plan.meta.route_id && Array.isArray(planLegs)) {
        planLegs._route_id = plan.meta.route_id;
      }
    } catch (e) {}

    // If no legs available, just select without geometry
    if (!planLegs || !Array.isArray(planLegs) || planLegs.length === 0) {
      setSelectedRouteIdx(idx);
      try { setLabelDetail({ open: false, label: '', content: null }); setLabelAnchorEl(null); } catch (e) {}
      return;
    }

    // If geometry was already prefetched, just select
    if (opt.routeGeometries && opt.routeGeometries.length > 0) {
      setSelectedRouteIdx(idx);
      try { setLabelDetail({ open: false, label: '', content: null }); setLabelAnchorEl(null); } catch (e) {}
      return;
    }

    try {
      const segments = await fetchGeometryForLegs(planLegs);
      if (segments.length > 0) {
        const newOptions = routeOptions.slice();
        newOptions[idx] = { ...newOptions[idx], routeGeometries: segments };
        setRouteOptions(newOptions);
      }
      setSelectedRouteIdx(idx);
    } catch (e) {
      console.warn('Failed to fetch per-leg geometry', e);
      setSelectedRouteIdx(idx);
      try { setLabelDetail({ open: false, label: '', content: null }); setLabelAnchorEl(null); } catch (err) {}
    }
  };

  const [labelAnchorEl, setLabelAnchorEl] = useState(null);

  const handleOpenLabelDetail = (event, opt, label) => {
    // Toggle popup: close if same label clicked
    const sameLabel = labelDetail.open && String(labelDetail.label || '').toUpperCase() === String(label || '').toUpperCase();
    if (sameLabel) {
      setLabelDetail({ open: false, label: '', content: null });
      setLabelAnchorEl(null);
      return;
    }
    const content = opt?.sources?.[label] ?? null;
    setLabelDetail({ open: true, label, content });
    setLabelAnchorEl(event.currentTarget);
  };

  const MapFallback = () => (
    <Skeleton variant="rounded" sx={{ width: "100%", height: { xs: 350, md: 450 } }} />
  );

  // Build the suggested routes panel so it can be injected into the map side column
  const suggestedRoutesPanel = (
    <Paper elevation={1} sx={{ p: { xs: 2.5, md: 3 }, position: 'relative', zIndex: 1050, height: '100%', maxHeight: { xs: '70dvh', md: '100%' }, minHeight: { xs: 320, md: 'auto' }, overflow: 'hidden', display: 'flex', flexDirection: 'column', border: '1px solid', borderColor: '#00bcd4' }}>
      <Stack spacing={2} sx={{ flex: '0 0 auto' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 1, position: 'relative', zIndex: 1051 }}>
          <Typography variant="h6" fontWeight={700}>
            Suggested routes
          </Typography>
          <Button
            size="small"
            variant="contained"
            onClick={() => { setShowSuggested(false); setSelectedRouteIdx(null); try { setLabelDetail({ open: false, label: '', content: null }); setLabelAnchorEl(null); } catch (e) {} }}
            aria-label="Close suggested routes"
            startIcon={<X size={16} />}
            sx={{
              textTransform: 'none',
              fontWeight: 700,
              backgroundColor: '#00BCD4',
              color: '#ffffff',
              '&:hover': {
                backgroundColor: '#00acc1',
              },
            }}
          >
            Close
          </Button>
        </Box>
      </Stack>

      {/* Make the list area scrollable and fill remaining height */}
      <Box sx={{ flex: '1 1 auto', overflowY: 'auto', pr: { xs: 0, sm: 1 }, mt: 1 }}>
        {isSearching ? (
          <Stack spacing={2}>
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} height={120} variant="rounded" />
            ))}
          </Stack>
        ) : routeOptions.length > 0 ? (
          <Stack spacing={2}>
            {routeOptions.map((opt, idx) => (
              <Box
                key={opt.id}
                onClick={() => handleSelectRoute(idx)}
                sx={{ cursor: 'pointer', height: 'auto', display: 'block' }}
              >
                {/* Option label + selected indicator: render multiple chips (uppercase) clickable for details */}
                <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mb: 0.5, flexWrap: 'wrap' }}>
                  <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap', alignItems: 'center' }}>
                    {(() => {
                      // enforce requested display order and colours
                      const rawLabels = Array.isArray(opt.labels) ? opt.labels : [opt.label];
                      const ORDER = ['E·ARR', 'E·DEP', 'FASTEST', 'ECO', 'GREEDY', 'COSY', 'LAZY'];
                      const orderKey = (s) => String(s || '').toUpperCase();
                      const ORDER_UP = ORDER.map((o) => o.toUpperCase());
                      const colorMap = {
                        'E·ARR': '#FFF8E1', // light ivory
                        'E·DEP': '#E0BBE4', // lilac
                        'FASTEST': '#87CEEB', // sky blue
                        'ECO': '#C6F6D5', // mint (soft)
                        'GREEDY': '#FF7F50', // coral
                        // COSY: sage colour
                        'COSY': '#9DC183', // sage
                        'LAZY': '#FFF59D', // lemon (Shorter Walks)
                      };

                      const sorted = rawLabels.slice().sort((a, b) => {
                        const A = orderKey(a);
                        const B = orderKey(b);
                        const ia = ORDER_UP.indexOf(A);
                        const ib = ORDER_UP.indexOf(B);
                        if (ia === -1 && ib === -1) return A.localeCompare(B);
                        if (ia === -1) return 1;
                        if (ib === -1) return -1;
                        return ia - ib;
                      });

                      return sorted.map((lab) => {
                        const labUp = String(lab || '').toUpperCase();
                        // match keys in colorMap by normalizing '·' vs '.' etc
                        const key = Object.keys(colorMap).find((k) => k.toUpperCase() === labUp) || labUp;
                        const bg = colorMap[key] || 'grey.300';
                        return (
                          <Box key={lab} sx={{ display: 'inline-flex', alignItems: 'center', mr: 0.5 }}>
                            <Chip
                              size="small"
                              label={labUp}
                              onClick={(e) => { e.stopPropagation(); handleOpenLabelDetail(e, opt, lab); }}
                              sx={{
                                fontWeight: 700,
                                borderRadius: 0.5,
                                backgroundColor: bg,
                                color: '#000000', // font black per request
                                border: '1px solid #00BCD4', // frame cyan
                                height: 18,
                                fontSize: '0.52rem',
                                paddingLeft: '6px',
                                paddingRight: '6px',
                                minWidth: '0px',
                                lineHeight: '16px',
                                cursor: 'pointer',
                                px: 0.3,
                                letterSpacing: '0.2px',
                              }}
                            />
                          </Box>
                        );
                      });
                    })()}
                  </Stack>
                </Stack>
                <Box
                  sx={{
                    outline: idx === selectedRouteIdx ? '2px solid' : '1px solid',
                    outlineColor: idx === selectedRouteIdx ? '#00bcd4' : 'divider',
                    borderRadius: 2,
                    transition: 'outline 0.15s',
                    height: 'auto',
                    display: 'block',
                  }}
                >
                  <RouteCard route={opt.card} onSave={handleSaveRoute} isSaved={isFavorited(opt.card)} isSelected={idx === selectedRouteIdx} fullHeight={routeOptions.length === 1} />
                </Box>
              </Box>
            ))}
          </Stack>
        ) : (
          <Typography variant="body2" color="text.secondary">
            No routes found. Try adjusting your search.
          </Typography>
        )}
      </Box>
      
    </Paper>
  );

  return (
    <>
      <Stack spacing={{ xs: 1.5, md: 2 }}>
      {/* Label popups are rendered inline above each chip (see chips rendering) */}
      <Paper
        elevation={0}
        sx={{
          p: { xs: 0.5, md: 0.7 },
          background: "linear-gradient(135deg, #6366F1 0%, #EC4899 100%)",
          color: "white",
          borderRadius: "16px",
        }}
      >
        <Stack direction="row" spacing={1.5} alignItems="center">
          <Bus size={24} />
          <Typography variant="h5" fontWeight={550}>
            Dashboard
          </Typography>
          <Chip
            label="Live"
            sx={{ fontWeight: 550, backgroundColor: "rgba(255,255,255,0.25)", color: "white" }}
            size="small"
          />
        </Stack>
      </Paper>

      <Paper
        elevation={0}
        sx={{
          p: { xs: 2, md: 3 },
          borderRadius: "16px",
          border: "1px solid",
          borderColor: "divider",
        }}
      >
          <Stack spacing={2}>
            <Stack direction="row" alignItems="center" spacing={2} justifyContent="center">
              {/* Quick journey search heading removed per UI update */}

              {/* Date/time/transfers moved below the search inputs */}
            </Stack>

                <Box sx={{ display: 'grid', gap: 1, gridTemplateColumns: { xs: '1fr', md: '48px 1fr 56px 1fr auto' }, alignItems: 'center', width: '100%' }}>
                  {/* Icon */}
                  <Box sx={{ gridColumn: { md: 1 } }}>
                    <IconButton aria-label="Use my location" onClick={handleUseMyLocation} size="large" sx={{ alignSelf: 'center' }}>
                      <Crosshair size={18} />
                    </IconButton>
                  </Box>

                  {/* From input */}
                  <Box sx={{ gridColumn: { md: 2 } }}>
                    <Autocomplete
                      fullWidth
                      freeSolo
                      filterOptions={(x) => x}
                      options={allStops.from}
                      ListboxProps={{ sx: { maxHeight: '510px' } }}
                      getOptionLabel={(option) => (typeof option === "string" ? option : (option.display_name || option.name || ""))}
                      value={selectedFromStop}
                      onChange={(e, value) => {
                        if (typeof value === "string") {
                          setSelectedFromStop(null);
                          setFromLocation(value);
                          return;
                        }
                        setSelectedFromStop(value);
                        if (value && typeof value === "object") {
                          setFromLocation(value.display_name || value.name || "");
                          try {
                            const lat = value.lat ?? value.latitude;
                            const lon = value.lon ?? value.longitude;
                            if (mapInstance && typeof lat === 'number' && typeof lon === 'number') {
                              mapInstance.flyTo([lat, lon], Math.max(mapInstance.getZoom(), 15), { duration: 1.2 });
                            }
                          } catch (err) {
                            // ignore
                          }
                        }
                      }}
                      inputValue={fromLocation}
                      onInputChange={(e, value) => setFromLocation(value)}
                      loading={fromLoading}
                      renderOption={(props, option) => {
                        const label = typeof option === "string" ? option : (option.display_name || option.name);
                        const optionType = typeof option === "string" ? "stop" : option.type || "stop";
                        return (
                          <Box component="li" {...props} sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                            <Box sx={{ color: (theme) => theme.palette.mode === 'light' && optionType === 'location' ? '#8B5E3C' : undefined }}>
                              {optionType === "location" ? <MapPin size={16} /> : <Bus size={16} />}
                            </Box>
                            <Box sx={{ flexGrow: 1 }}>
                              <Typography variant="body2" fontWeight={600}>{label}</Typography>
                              {optionType === "location" && (
                                <Typography variant="caption" sx={{ color: (theme) => theme.palette.mode === 'light' ? '#8B5E3C' : undefined }}>Location</Typography>
                              )}
                            </Box>
                            <Chip
                              label={optionType === "location" ? "Location" : "Stop"}
                              size="small"
                              variant="outlined"
                              onClick={(e) => {
                                // clicking the chip should behave like selecting the stop
                                e.stopPropagation();
                                try {
                                  // set selected value for the From input and centre the map
                                  if (typeof option === 'object') {
                                    setSelectedFromStop(option);
                                    const lat = option.lat ?? option.latitude;
                                    const lon = option.lon ?? option.longitude;
                                    if (mapInstance && typeof lat === 'number' && typeof lon === 'number') {
                                      mapInstance.flyTo([lat, lon], Math.max(mapInstance.getZoom(), 15), { duration: 1.2 });
                                    }
                                  }
                                } catch (err) {
                                  // ignore
                                }
                              }}
                            />
                            {option && Array.isArray(option.lines) && option.lines.length > 0 && (
                              <Box sx={{ display: 'inline-flex', gap: 0.5, ml: 1 }}>
                                {option.lines.slice(0,3).map((ln) => (
                                  <Button key={ln} size="small" onClick={(ev) => {
                                    ev.stopPropagation();
                                    try {
                                      // Prefer the safe dispatch helper which will call
                                      // the registered toggle or queue the request.
                                      if (window.__dispatchBusRouteToggle) {
                                        void window.__dispatchBusRouteToggle(String(ln));
                                      } else if (window.__busRouteToggle) {
                                        void window.__busRouteToggle(String(ln));
                                      } else {
                                        window.__busRouteToggleQueue = window.__busRouteToggleQueue || [];
                                        window.__busRouteToggleQueue.push(String(ln));
                                      }
                                    } catch (e) {
                                      // ignore
                                    }
                                  }} sx={{ minWidth: 0, px: 0.6, py: 0.3, fontWeight: 700, fontSize: '0.6rem' }}>{ln}</Button>
                                ))}
                              </Box>
                            )}
                          </Box>
                        );
                      }}
                      renderInput={(params) => (
                        <TextField
                          {...params}
                          label="From"
                          InputProps={{
                            ...params.InputProps,
                            startAdornment: (
                              <InputAdornment position="start"><MapPin size={18} /></InputAdornment>
                            ),
                            endAdornment: fromLoading ? (<CircularProgress color="inherit" size={20} />) : params.InputProps.endAdornment,
                          }}
                          sx={{
                            "& .MuiOutlinedInput-root": { "& fieldset": { borderColor: '#00BCD4' }, "&:hover fieldset": { borderColor: '#00BCD4' }, "&.Mui-focused fieldset": { borderColor: '#00BCD4' }, boxShadow: 'none' },
                            "& .MuiInputBase-input": { color: (theme) => theme.palette.mode === "dark" ? "#fff" : undefined, textOverflow: "ellipsis" },
                            "& .MuiInputLabel-root": { color: (theme) => theme.palette.mode === "dark" ? "rgba(255,255,255,0.7)" : undefined },
                          }}
                        />
                      )}
                    />
                  </Box>

                  {/* Swap button */}
                  <Box sx={{ gridColumn: { md: 3 }, display: 'flex', justifyContent: 'center' }}>
                    <IconButton aria-label="Swap start and destination" onClick={() => {
                      setSelectedFromStop((prevFrom) => { const oldFrom = prevFrom; setSelectedToStop(oldFrom); return selectedToStop; });
                      setSelectedToStop((prev) => prev);
                      setFromLocation((prevFromLoc) => { const oldFromLoc = prevFromLoc; setToLocation(oldFromLoc); return toLocation; });
                    }} size="large" sx={{ width: 56, height: 56, p: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: '12px', '&:focus-visible': { outline: '2px solid', outlineOffset: 2 } }}>
                      <span style={{ fontSize: 22, lineHeight: 1 }}>⇄</span>
                    </IconButton>
                  </Box>

                  {/* To input */}
                  <Box sx={{ gridColumn: { md: 4 } }}>
                    <Autocomplete
                      fullWidth
                      freeSolo
                      filterOptions={(x) => x}
                      options={allStops.to}
                      ListboxProps={{ sx: { maxHeight: '510px' } }}
                      getOptionLabel={(option) => (typeof option === "string" ? option : (option.display_name || option.name || ""))}
                      value={selectedToStop}
                      onChange={(e, value) => {
                        if (typeof value === "string") { setSelectedToStop(null); setToLocation(value); return; }
                        setSelectedToStop(value);
                        if (value && typeof value === "object") {
                          setToLocation(value.display_name || value.name || "");
                          try {
                            const lat = value.lat ?? value.latitude;
                            const lon = value.lon ?? value.longitude;
                            if (mapInstance && typeof lat === 'number' && typeof lon === 'number') {
                              mapInstance.flyTo([lat, lon], Math.max(mapInstance.getZoom(), 15), { duration: 1.2 });
                            }
                          } catch (err) {}
                        }
                      }}
                      inputValue={toLocation}
                      onInputChange={(e, value) => setToLocation(value)}
                      loading={toLoading}
                      renderOption={(props, option) => {
                        const label = typeof option === "string" ? option : (option.display_name || option.name);
                        const optionType = typeof option === "string" ? "stop" : option.type || "stop";
                        return (
                          <Box component="li" {...props} sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                            <Box sx={{ color: (theme) => theme.palette.mode === 'light' && optionType === 'location' ? '#8B5E3C' : undefined }}>{optionType === "location" ? <MapPin size={16} /> : <Bus size={16} />}</Box>
                            <Box sx={{ flexGrow: 1 }}><Typography variant="body2" fontWeight={600}>{label}</Typography>{optionType === "location" && (<Typography variant="caption" sx={{ color: (theme) => theme.palette.mode === 'light' ? '#8B5E3C' : undefined }}>Location</Typography>)}</Box>
                            <Chip
                              label={optionType === "location" ? "Location" : "Stop"}
                              size="small"
                              variant="outlined"
                              onClick={(e) => {
                                e.stopPropagation();
                                try {
                                  if (typeof option === 'object') {
                                    setSelectedToStop(option);
                                    const lat = option.lat ?? option.latitude;
                                    const lon = option.lon ?? option.longitude;
                                    if (mapInstance && typeof lat === 'number' && typeof lon === 'number') {
                                      mapInstance.flyTo([lat, lon], Math.max(mapInstance.getZoom(), 15), { duration: 1.2 });
                                    }
                                  }
                                } catch (err) {}
                              }}
                            />
                                {option && Array.isArray(option.lines) && option.lines.length > 0 && (
                              <Box sx={{ display: 'inline-flex', gap: 0.5, ml: 1 }}>
                                {option.lines.slice(0,3).map((ln) => (
                                  <Button key={ln} size="small" onClick={(ev) => { ev.stopPropagation(); try {
                                      // Prefer the dispatch helper (may queue) then fall back to direct toggle
                                      if (window.__dispatchBusRouteToggle) {
                                        void window.__dispatchBusRouteToggle(String(ln));
                                      } else if (window.__busRouteToggle) {
                                        void window.__busRouteToggle(String(ln));
                                      } else {
                                        window.__busRouteToggleQueue = window.__busRouteToggleQueue || [];
                                        window.__busRouteToggleQueue.push(String(ln));
                                      }
                                    } catch(e) {} }} sx={{ minWidth: 0, px: 0.6, py: 0.3, fontWeight: 700, fontSize: '0.6rem' }}>{ln}</Button>
                                ))}
                              </Box>
                            )}
                          </Box>
                        );
                      }}
                      renderInput={(params) => (
                        <TextField {...params} label="To" InputProps={{ ...params.InputProps, startAdornment: (<InputAdornment position="start"><NavIcon size={18} /></InputAdornment>), endAdornment: toLoading ? (<CircularProgress color="inherit" size={20} />) : params.InputProps.endAdornment }} sx={{ "& .MuiOutlinedInput-root": { "& fieldset": { borderColor: '#00BCD4' }, "&:hover fieldset": { borderColor: '#00BCD4' }, "&.Mui-focused fieldset": { borderColor: '#00BCD4' }, boxShadow: 'none' }, "& .MuiInputBase-input": { color: (theme) => theme.palette.mode === "dark" ? "#fff" : undefined, textOverflow: "ellipsis" }, "& .MuiInputLabel-root": { color: (theme) => theme.palette.mode === "dark" ? "rgba(255,255,255,0.7)" : undefined } }} />
                      )}
                    />
                  </Box>

                  {/* Search button */}
                  <Box sx={{ gridColumn: { md: 5 }, display: 'flex', justifyContent: { xs: 'stretch', md: 'flex-end' } }}>
                    <Button variant="contained" size="large" sx={{ minWidth: { xs: "100%", md: 180 }, height: 56, flexShrink: 0 }} onClick={handleSearch} disabled={!fromCoords || !toCoords || isSearching}>{isSearching ? <CircularProgress size={24} color="inherit" /> : "Search routes"}</Button>
                  </Box>

                  {/* Date/time group aligned under From */}
                  <Box sx={{ gridColumn: { xs: '1 / -1', md: 2 }, display: 'flex', gap: 1, alignItems: 'stretch', flexWrap: 'wrap', mt: { xs: 1, md: 0 } }}>
                    <TextField label="Hour" select size="small" value={timeSelHour} onChange={(e) => { const h = Number(e.target.value); setTimeSelHour(h); setDepartureClock(`${pad2(h)}:${pad2(timeSelMinute)}`); }} SelectProps={{ renderValue: () => pad2(timeSelHour), SelectDisplayProps: { sx: { textAlign: 'center' } }, MenuProps: { PaperProps: { sx: { maxHeight: 240 } } }, }} sx={{ width: { xs: 'calc(50% - 4px)', sm: 90 }, '& .MuiSelect-select': { textAlign: 'center' } }}>
                      {Array.from({ length: 24 }, (_, i) => i).map((h) => (<MenuItem key={h} value={h} sx={{ textAlign: 'center' }}>{pad2(h)}</MenuItem>))}
                    </TextField>
                    <TextField label="Minute" select size="small" value={timeSelMinute} onChange={(e) => { const mm = Number(e.target.value); setTimeSelMinute(mm); setDepartureClock(`${pad2(timeSelHour)}:${pad2(mm)}`); }} SelectProps={{ renderValue: () => pad2(timeSelMinute), SelectDisplayProps: { sx: { textAlign: 'center' } }, MenuProps: { PaperProps: { sx: { maxHeight: 240 } } }, }} sx={{ width: { xs: 'calc(50% - 4px)', sm: 90 }, '& .MuiSelect-select': { textAlign: 'center' } }}>
                      {Array.from({ length: 60 }, (_, i) => i).map((mm) => (<MenuItem key={mm} value={mm} sx={{ textAlign: 'center' }}>{pad2(mm)}</MenuItem>))}
                    </TextField>
                    <TextField label="Day" select size="small" value={dateSelDay} onChange={(e) => { const newD = Number(e.target.value); setDateSelDay(newD); setDepartureDate(`${String(dateSelYear)}-${pad2(dateSelMonth)}-${pad2(newD)}`); }} SelectProps={{ SelectDisplayProps: { sx: { textAlign: 'center' } }, MenuProps: { PaperProps: { sx: { maxHeight: 240 } } } }} sx={{ width: { xs: 'calc(50% - 4px)', sm: 90 }, '& .MuiSelect-select': { textAlign: 'center' } }}>
                      {Array.from({ length: daysInMonth(dateSelYear, dateSelMonth) }, (_, i) => i + 1).map((d) => (<MenuItem key={d} value={d} sx={{ textAlign: 'center' }}>{d}</MenuItem>))}
                    </TextField>
                    <TextField
                      label="Month / Year"
                      select
                      size="small"
                      value={`${dateSelYear}-${pad2(dateSelMonth)}`}
                      onChange={(e) => {
                        const val = String(e.target.value || '');
                        const [yStr, mStr] = val.split('-');
                        const newYear = Number(yStr);
                        const newMonth = Number(mStr);
                        if (!Number.isFinite(newYear) || !Number.isFinite(newMonth)) return;
                        let newDay = dateSelDay;
                        const maxD = daysInMonth(newYear, newMonth);
                        if (newDay > maxD) newDay = maxD;
                        setDateSelYear(newYear);
                        setDateSelMonth(newMonth);
                        setDateSelDay(newDay);
                        setDepartureDate(`${String(newYear)}-${pad2(newMonth)}-${pad2(newDay)}`);
                      }}
                      SelectProps={{ renderValue: () => `${new Date(dateSelYear, dateSelMonth - 1, 1).toLocaleString(undefined, { month: 'short' }).toUpperCase()}${'\u00A0\u00A0\u00A0\u00A0'}/${'\u00A0\u00A0\u00A0\u00A0'}${dateSelYear}`, SelectDisplayProps: { sx: { textAlign: 'center' } }, MenuProps: { PaperProps: { sx: { maxHeight: 240 } } } }}
                      sx={{ width: { xs: '100%', sm: 180 }, '& .MuiSelect-select': { textAlign: 'center' } }}
                    >
                      {(() => {
                        // Start three months earlier than now, and produce a 7-year (84 month) range
                        const start = new Date();
                        start.setMonth(start.getMonth() - 3);
                        const totalMonths = 7 * 12; // 7 years
                        const opts = [];
                        for (let i = 0; i < totalMonths; i++) {
                          const d = new Date(start.getFullYear(), start.getMonth() + i, 1);
                          const y = d.getFullYear();
                          const m = d.getMonth() + 1;
                          const val = `${y}-${pad2(m)}`;
                          const label = `${new Date(y, m - 1, 1).toLocaleString(undefined, { month: 'short' }).toUpperCase()}${'\u00A0\u00A0\u00A0\u00A0'}/${'\u00A0\u00A0\u00A0\u00A0'}${y}`;
                          opts.push(<MenuItem key={val} value={val} sx={{ textAlign: 'center' }}>{label}</MenuItem>);
                        }
                        return opts;
                      })()}
                    </TextField>
                    
                  </Box>

                  {/* Transfers/mode group aligned under To */}
                  <Box sx={{ gridColumn: { xs: '1 / -1', md: 4 }, display: 'flex', gap: 1, alignItems: 'stretch', flexWrap: 'wrap', mt: { xs: 1, md: 0 }, justifyContent: { xs: 'flex-start', md: 'flex-end' } }}>
                    <TextField select size="small" value={transportMode} onChange={(e) => { const val = e.target.value; if (val !== null) setTransportMode(val); }} SelectProps={{ renderValue: (selected) => { if (!selected) return ''; return selected === 'all' ? 'All' : selected.charAt(0).toUpperCase() + selected.slice(1); }, SelectDisplayProps: { sx: { textAlign: 'center' } }, MenuProps: { PaperProps: { sx: { maxHeight: 240 } } }, }} sx={{ width: { xs: 'calc(50% - 4px)', sm: 90 }, '& .MuiSelect-select': { textAlign: 'center' } }}>
                      <MenuItem value="all" sx={{ textAlign: 'center' }}>All</MenuItem>
                      <MenuItem value="bus" sx={{ textAlign: 'center' }}>Bus</MenuItem>
                      <MenuItem value="train" sx={{ textAlign: 'center' }}>Train</MenuItem>
                    </TextField>
                    <TextField label="Transfers" select size="small" value={maxTransfers} onChange={(e) => { const v = Number(e.target.value); setMaxTransfers(Number.isFinite(v) ? Math.max(0, Math.min(5, v)) : 0); }} SelectProps={{ SelectDisplayProps: { sx: { textAlign: 'center' } }, MenuProps: { PaperProps: { sx: { maxHeight: 240 } } } }} sx={{ width: { xs: 'calc(50% - 4px)', sm: 90 }, '& .MuiSelect-select': { textAlign: 'center' } }}>
                      {[0,1,2,3,4,5].map((n) => (<MenuItem key={n} value={n} sx={{ textAlign: 'center' }}>{n}</MenuItem>))}
                    </TextField>
                  </Box>
                </Box>
          {geoError && (
            <Box sx={{ mt: 1 }}>
              <Alert severity="error">{geoError}</Alert>
            </Box>
          )}

          {favorites.length > 0 && (
            <Box>
              <Typography variant="caption" fontWeight={700} display="block" mb={1}>
                Recent Journeys
              </Typography>
              <Stack spacing={1}>
                    {favorites.slice(0, 3).map((fav, idx) => (
                      <Box
                        key={idx}
                        onClick={() => {
                          setFromLocation(fav.fromName);
                          setToLocation(fav.toName);
                        }}
                        sx={{
                          p: 1,
                          borderRadius: 1,
                          backgroundColor: "#f5f5f5",
                          cursor: "pointer",
                          "&:hover": { backgroundColor: "#eeeeee" },
                        }}
                      >
                        <Typography variant="caption" fontWeight={600}>
                          {fav.fromName} \u2192 {fav.toName}
                        </Typography>
                      </Box>
                    ))}
                  </Stack>
                </Box>
              )}
        </Stack>
      </Paper>

      {/* ---- Inline live transport map ---- */}
      <Paper
        elevation={0}
        sx={{ p: { xs: 2, md: 3 }, borderRadius: "16px", border: "1px solid", borderColor: "divider" }}
      >
        <Stack direction="row" spacing={1.5} alignItems="center" mb={0}>
          {/* Map header - icon intentionally removed */}
        </Stack>

        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} mb={2} flexWrap="wrap" alignItems={{ xs: 'stretch', sm: 'center' }}>
          <Box
            data-testid="filter-buses"
            onClick={() => setFilters((f) => ({ ...f, showBuses: !f.showBuses }))}
            sx={{
              padding: "8px 16px",
              border: "2px solid " + (filters.showBuses ? "#6366F1" : "#E2E8F0"),
              borderRadius: "12px",
              minHeight: 48,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: 'center',
              width: { xs: '100%', sm: 'auto' },
              gap: 1,
              cursor: "pointer",
              backgroundColor: filters.showBuses ? "#6366F1" : "transparent",
              color: filters.showBuses ? "white" : "inherit",
              fontWeight: 600,
              transition: "all 0.3s ease",
            }}
          >
            <Bus size={18} /> Buses {filteredMarkers.filter((m) => m.type === "bus").length}
          </Box>
          <Box
            data-testid="filter-trains"
            onClick={() => setFilters((f) => ({ ...f, showTrains: !f.showTrains }))}
            sx={{
              padding: "8px 16px",
              border: "2px solid " + (filters.showTrains ? "#10B981" : "#E2E8F0"),
              borderRadius: "12px",
              minHeight: 48,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: 'center',
              width: { xs: '100%', sm: 'auto' },
              gap: 1,
              cursor: "pointer",
              backgroundColor: filters.showTrains ? "#10B981" : "transparent",
              color: filters.showTrains ? "white" : "inherit",
              fontWeight: 600,
              transition: "all 0.3s ease",
            }}
          >
            <Train size={18} /> Trains {filteredMarkers.filter((m) => m.type === "train").length}
          </Box>
          <Box sx={{ flex: 1, display: { xs: 'none', sm: 'block' } }} />
          {!userLocation && (
            <Button
              variant="outlined"
              size="large"
              onClick={requestLocation}
              disabled={locationStatus === 'loading'}
              sx={(theme) => ({
                borderRadius: '12px',
                textTransform: 'none',
                width: { xs: '100%', sm: 160 },
                height: 48,
                px: 2,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: theme.palette.mode === 'dark' ? 'white' : undefined,
                borderColor: theme.palette.mode === 'dark' ? 'rgba(255,255,255,0.7)' : undefined,
                // ensure icon/text inside follow the color
                '& .MuiButton-startIcon, & .MuiTypography-root': { color: theme.palette.mode === 'dark' ? 'white' : undefined },
              })}
            >
              {locationStatus === 'loading' ? (
                <Stack direction="row" spacing={1} alignItems="center">
                  <CircularProgress size={18} />
                  <Typography variant="body2">Locating</Typography>
                </Stack>
              ) : (
                'Use my location'
              )}
            </Button>
          )}
          {userLocation && (
            <Button
              variant="contained"
              size="large"
              onClick={handleCenterOnUser}
              sx={{
                borderRadius: '12px',
                textTransform: 'none',
                width: { xs: '100%', sm: 160 },
                height: 48,
                px: 2,
                backgroundColor: '#D97974',
                color: '#ffffff',
                '&:hover': { backgroundColor: '#c86b66' },
              }}
            >
              Center on me
            </Button>
          )}
          {/* Compact weather aligned to the right of the filter row */}
          <Box sx={{ display: { xs: 'none', sm: 'flex' }, ml: 1 }}>
            <WeatherWidget variant="inline" />
          </Box>
        </Stack>

        <Suspense fallback={<MapFallback />}>
          <MapViewMap
            filteredMarkers={filteredMarkers}
            openPopupId={openPopupId}
            onOpenPopup={setOpenPopupId}
            onOpenPopupSignature={setOpenPopupSignature}
            onClosePopup={() => { setOpenPopupId(null); try { setOpenPopupSignature(null); } catch(e) {} }}
            userLocation={userLocation}
            nearestStop={nearestStop}
            busLoading={busLoading}
            busRefreshing={busRefreshing}
            busCountdown={busCountdown}
            busRefreshInterval={busRefreshInterval}
            trainLoading={trainLoading}
            onMapReady={setMapInstance}
            onMoveEnd={handleMoveEnd}
            sideContent={suggestedRoutesPanel}
            showSideOverlay={showSuggested}
            showRouteLines={true}
            journeyRoute={journeyRoute}
          />
        </Suspense>
      </Paper>

      {/* ---- Alerts ---- */}
      <Paper
        elevation={0}
        sx={{
          p: { xs: 2, md: 3 },
          borderRadius: "16px",
          border: "1px solid",
          borderColor: "divider",
          background: "transparent",
        }}
      >
        <Stack direction="row" spacing={1.5} alignItems="center" mb={2}>
          <AlertCircle size={20} color="#EC4899" />
          <Typography variant="subtitle1" fontWeight={700}>
            Service alerts
          </Typography>
          {alertsConnected && <Chip label="Live" size="small" color="primary" variant="outlined" sx={{ ml: 1 }} />}
        </Stack>
        <Stack spacing={1.5}>
          {alertsLoading ? (
            <Stack spacing={1}>
              {[1, 2].map((i) => (
                <Skeleton key={i} height={44} variant="rounded" />
              ))}
            </Stack>
          ) : alerts.length > 0 ? (
            alerts.map((alert) => (
              <Alert
                key={alert.id}
                severity={alert.severity === "warning" ? "warning" : "info"}
                variant="outlined"
                sx={{
                  borderRadius: "8px",
                  backgroundColor:
                    alert.severity === "warning" ? "rgba(245, 158, 11, 0.05)" : "rgba(59, 130, 246, 0.05)",
                }}
              >
                {/* Some Messages contain HTML to be rendered, which must first be sanitised */}
                <RemoveDangerousHTML rawHTML={alert.message} />
              </Alert>
            ))
          ) : (
            <Typography variant="body2" color="text.secondary">
              No service alerts right now.
            </Typography>
          )}
        </Stack>
      </Paper>

      <Paper elevation={1} sx={{ p: { xs: 2, md: 3 } }}>
        <Stack spacing={2}>
          <Stack direction="row" spacing={1} alignItems="center">
            <Clock size={18} />
            <Typography variant="h6" fontWeight={700}>
              Upcoming Train Departures
            </Typography>
          </Stack>
          <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
            {trainLoading ? (
              [1, 2, 3].map((i) => (
                <Skeleton key={i} height={80} sx={{ flex: 1 }} variant="rounded" />
              ))
            ) : (
              liveDepartures.map((dep) => (
                <Box key={dep.id} sx={{ flex: 1 }}>
                  <DepartureCard departure={dep} />
                </Box>
              ))
            )}
          </Stack>
        </Stack>
      </Paper>

      {/* Bottom weather widget removed — now shown inline in the map filter row */}
      </Stack>
      <Popover
        open={labelDetail.open}
        anchorEl={labelAnchorEl}
        onClose={() => { setLabelDetail({ open: false, label: '', content: null }); setLabelAnchorEl(null); }}
        anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
        transformOrigin={{ vertical: 'bottom', horizontal: 'center' }}
        disableRestoreFocus
        PaperProps={{
          elevation: 0,
          sx: (() => {
            const lab = String(labelDetail.label || '').toUpperCase();
            const bg = LABEL_COLORS[lab] || '#00BCD4';
            return { backgroundColor: bg, boxShadow: 'none', border: 'none', borderRadius: '10px' };
          })(),
        }}
      >
        {(() => {
          const lab = String(labelDetail.label || '').toUpperCase();
          const bg = LABEL_COLORS[lab] || '#00BCD4';
          // Force GREEDY popover text to black for consistent legibility
          const fg = (lab === 'GREEDY') ? '#000000' : textColorForBg(bg);
          return (
            <Box sx={{ p: '8px 12px', minWidth: 140, backgroundColor: 'transparent', color: fg, display: 'flex', alignItems: 'center', justifyContent: 'center', textAlign: 'center' }}>
              <Typography variant="body2" sx={{ color: fg, fontWeight: 700 }}>
                {LABEL_DESCRIPTIONS[lab] || lab}
              </Typography>
            </Box>
          );
        })()}
      </Popover>
    </>
  );
}
