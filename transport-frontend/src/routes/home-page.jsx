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
  const totalSec = meta.total_seconds ?? legs.reduce((s, l) => s + (l.duration_seconds ?? 0), 0);
  if (meta.total_duration) {
    duration = meta.total_duration;
  } else {
    const totalMin = Math.round(totalSec / 60);
    duration = totalMin >= 60
      ? `${Math.floor(totalMin / 60)}h ${totalMin % 60} mins`
      : `${totalMin} mins`;
  }

  // initial departure seconds may be provided by the backend (meta.initial_departure_secs)
  const initialDepartureSecsMeta = meta.initial_departure_secs ?? null;

  // Helper: parse a time string like "HH:MM" or "HH:MM:SS" possibly with " (+Nd)" suffix
  const parseTimeToSecondsOfDay = (timeStr) => {
    if (!timeStr || typeof timeStr !== 'string') return null;
    // strip any day offset suffix like " (+1d)"
    const core = timeStr.split('(')[0].trim();
    const parts = core.split(':').map((p) => parseInt(p, 10));
    if (parts.length < 2 || Number.isNaN(parts[0]) || Number.isNaN(parts[1])) return null;
    const hh = parts[0];
    const mm = parts[1];
    const ss = parts.length >= 3 && !Number.isNaN(parts[2]) ? parts[2] : 0;
    if (hh < 0 || hh > 23 || mm < 0 || mm > 59 || ss < 0 || ss > 59) return null;
    return hh * 3600 + mm * 60 + ss;
  };

  // Prefer meta-provided epoch seconds, else derive seconds-of-day from the first leg's departure time.
  let initialDepartureSecs = initialDepartureSecsMeta ?? null;
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
        const secs = parseTimeToSecondsOfDay(t);
        if (Number.isFinite(secs)) {
          initialDepartureSecs = secs;
          break;
        }
      }
    }
  } catch (e) {
    // ignore parse errors
  }

  return {
    id: 1,
    duration,
    totalSeconds: totalSec,
    initialDepartureSecs,
    transfers,
    steps,
    walkMinutes,
    // Pricing: £2.10 per bus leg (single ticket), train legs are not priced here
    busLegs: steps.filter((s) => s.type === 'bus').length,
    price: (() => {
      const n = steps.filter((s) => s.type === 'bus').length;
      if (n === 0) return null;
      const total = (n * 2.10).toFixed(2);
      return `£${total}`;
    })(),
  };
}

const MOCK_MARKERS = [
  { id: 1, position: [54.050556, -2.800556], name: "Lancaster Bus Station", type: "bus", status: "On time" },
  { id: 2, position: [54.048889, -2.802500], name: "Lancaster Train Station", type: "train", status: "On time" },
  { id: 3, position: [54.064560, -2.798890], name: "Lancaster City Center Stop", type: "bus", status: "On time" },
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

    // Need both endpoints to request OSRM geometry
    if (typeof fromLat !== 'number' || typeof fromLon !== 'number' ||
        typeof toLat !== 'number' || typeof toLon !== 'number') {
      return segment;
    }

    try {
      const osrmMode = isWalk ? 'walking' : 'driving';
      const url = `${API_BASE}/route/leg-geometry?from_lat=${encodeURIComponent(fromLat)}&from_lon=${encodeURIComponent(fromLon)}&to_lat=${encodeURIComponent(toLat)}&to_lon=${encodeURIComponent(toLon)}&mode=${encodeURIComponent(osrmMode)}`;
      const resp = await fetch(url);
      if (!resp.ok) return segment;
      const data = await resp.json();
      if (data && Array.isArray(data.coords) && data.coords.length >= 2) {
        // Normalize coords — endpoint already returns [lat,lon] but be safe
        const norm = normalizeCoords(data.coords);
        if (norm.length >= 2) segment.coords = norm;
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
    'E·A': 'Earliest Arrival',
    'ED': 'Earliest Departure',
    'E·D': 'Earliest Departure',
    'FASTEST': 'Smallest Total Time',
    'ECO': 'Prefer Walking',
    'GREEDY': 'Least Transfers',
    'COSY': 'No Transfer Within Same Mode',
    'LAZY': 'Shorter Walks — Fewer Transfers',
  };
  // Colour map used for label chips — keep in sync with the chip rendering
  const LABEL_COLORS = {
    'E·A': '#FFF8E1',
    'E·D': '#E0BBE4',
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
  // default transfers set to 3
  const [maxTransfers, setMaxTransfers] = useState(3);
  // mode selector for journey planner: 'all' | 'bus' | 'train' (UI value); map 'all' -> 'combined' for API
  const [transportMode, setTransportMode] = useState('all');
  const { favorites, saveFavorite, removeFavorite } = useFavoriteRoutes();
  // ---- Map + live-bus state (needed for stop search proximity) ----
  const [markers, setMarkers] = useState(MOCK_MARKERS);
  const [filters, setFilters] = useState({ showBuses: true, showTrains: true });
  const [openPopupId, setOpenPopupId] = useState(null);
  const [mapInstance, setMapInstance] = useState(null);
  const [mapCenter, setMapCenter] = useState(DEFAULT_CENTER);
  const [geoError, setGeoError] = useState(null);
  const [userLocation, setUserLocation] = useState(null);
  const [locationStatus, setLocationStatus] = useState('idle');
  const [locationError, setLocationError] = useState(null);
  const [autoLocated, setAutoLocated] = useState(false);
  
  const { results: fromStopResults, loading: fromLoading } = useStopSearch(fromLocation, 800, mapCenter);
  const { results: toStopResults, loading: toLoading } = useStopSearch(toLocation, 800, mapCenter);

  const { alerts: serviceAlerts, loading: alertsLoading } = useServiceAlerts();
  const { data: liveAlertUpdate, isConnected: alertsConnected } = useLiveUpdates("alerts");
  const [liveAlerts, setLiveAlerts] = useState([]);
  // Array of { card, routeGeometries } — one entry per alternative route
  const [routeOptions, setRouteOptions] = useState([]);
  // Index of the card the user has clicked / selected (controls map geometry)
  const [selectedRouteIdx, setSelectedRouteIdx] = useState(null);
  const [showSuggested, setShowSuggested] = useState(false);
  // Geometry drawn on the map: derived from the selected option
  const journeyRoute = (typeof selectedRouteIdx === 'number') ? (routeOptions[selectedRouteIdx]?.routeGeometries ?? null) : null;

  // State for showing router/label details when a label chip is clicked
  const [labelDetail, setLabelDetail] = useState({ open: false, label: '', content: null });

  /** Called by MapViewMap whenever the user finishes panning / zooming. */
  const handleMoveEnd = useCallback(({ lat, lon }) => {
    setMapCenter({ lat, lon });
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
    refreshInterval: 10000,
    debounceMs: 800,
  });
  const { data: trainDepartures, loading: trainLoading, error: trainError } = useLiveDepartures("LAN", 180000);
  const { data: liveBusUpdate } = useLiveUpdates("bus");

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

          newMarkers.push({
            id: id++,
            position: [lat, lon],
            name: displayName,
            type: "bus",
            status: bus.status || (delayMinutes != null && delayMinutes >= 2 ? `Delayed ${Math.round(delayMinutes)} min` : "On time"),
            routeNumber: bus.routeNumber || bus.route || bus.line,
            delayMinutes,
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
    }
  }, [busLocations, trainDepartures]);

  // Merge live WebSocket bus updates into markers
  useEffect(() => {
    const updates = Array.isArray(liveBusUpdate) ? liveBusUpdate : liveBusUpdate ? [liveBusUpdate] : [];
    const normalized = updates
      .map((item) => {
        const lat = item?.latitude ?? item?.lat;
        const lon = item?.longitude ?? item?.lon;
        if (typeof lat !== "number" || typeof lon !== "number") return null;
        return {
          id: item?.vehicleId || item?.id || "bus-" + lat + "-" + lon,
          position: [lat, lon],
          name: item?.name || ("Bus " + (item?.route || "")).trim(),
          type: "bus",
          status: item?.status || "On time",
          routeNumber: item?.routeNumber || item?.route,
        };
      })
      .filter(Boolean);
    if (normalized.length === 0) return;
    setMarkers((prev) => {
      const next = new Map(prev.map((m) => [m.id, m]));
      for (const item of normalized) next.set(item.id, { ...next.get(item.id), ...item });
      return Array.from(next.values());
    });
  }, [liveBusUpdate]);

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
        { key: 'main', label: 'E·A' },
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
        const geos = Array.isArray(journey.routeGeometries) ? journey.routeGeometries.map(g => ({ ...(g || {}) })) : [];
        const legs = Array.isArray(journey.legs) ? journey.legs : [];
        const n = Math.min(geos.length, legs.length);
        for (let i = 0; i < n; i++) {
          const geo = geos[i];
          const leg = legs[i];
          if (!geo) continue;
          if (leg && leg.from_stop && typeof leg.from_stop.lat === 'number' && typeof leg.from_stop.lon === 'number') {
            geo._from = [leg.from_stop.lat, leg.from_stop.lon];
          }
          if (leg && leg.to_stop && typeof leg.to_stop.lat === 'number' && typeof leg.to_stop.lon === 'number') {
            geo._to = [leg.to_stop.lat, leg.to_stop.lon];
          }
        }
        return geos;
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

      // Add special labels:
      // - "E·D": mark options with the earliest initial departure (if provided by backend in meta.initial_departure_secs)
      // - "FASTEST": mark options with the smallest totalSeconds
      try {
        const withInit = options.map((o) => ({ opt: o, init: (() => {
          // prefer meta-provided initial seconds from any source
          const srcs = o.sources ? Object.values(o.sources) : [];
          for (const s of srcs) {
            if (s && s.meta && Number.isFinite(s.meta.initial_departure_secs)) return Number(s.meta.initial_departure_secs);
            if (s && s.meta && s.meta.initial_departure_time) {
              const t = Date.parse(s.meta.initial_departure_time);
              if (!Number.isNaN(t)) return Math.floor(t / 1000);
            }
          }
          // fallback: use card.initialDepartureSecs if journeyToRouteCard populated it (meta->card mapping)
          if (o.card && Number.isFinite(o.card.initialDepartureSecs)) return Number(o.card.initialDepartureSecs);
          return null;
        })() }));

        const initVals = withInit.map((w) => w.init).filter((v) => Number.isFinite(v));
        if (initVals.length > 0) {
          const minInit = Math.min(...initVals);
          for (const w of withInit) {
            if (Number.isFinite(w.init) && w.init === minInit) {
              if (!w.opt.labels.includes('E·D')) w.opt.labels.unshift('E·D');
            }
          }
        }

        // FASTEST: compare numeric totalSeconds on the card
        const totalVals = options.map((o) => (o.card && Number.isFinite(o.card.totalSeconds) ? o.card.totalSeconds : null)).filter((v) => Number.isFinite(v));
        if (totalVals.length > 0) {
          const minTotal = Math.min(...totalVals);
          const EPS = 1; // seconds tolerance for "equally fastest"
          for (const o of options) {
            if (o.card && Number.isFinite(o.card.totalSeconds) && Math.abs(o.card.totalSeconds - minTotal) <= EPS) {
              if (!o.labels.includes('FASTEST')) o.labels.push('FASTEST');
            }
          }
        }

          // E·A: earliest arrival. Prefer meta-provided initial departure + total_seconds
          try {
            const arrivalVals = options.map((o) => {
              // look through any provided sources' meta for initial + total
              const srcs = o.sources ? Object.values(o.sources) : [];
              for (const s of srcs) {
                if (s && s.meta && Number.isFinite(s.meta.initial_departure_secs) && Number.isFinite(s.meta.total_seconds)) {
                  return Number(s.meta.initial_departure_secs) + Number(s.meta.total_seconds);
                }
                if (s && s.meta && Number.isFinite(s.meta.final_arrival_secs)) {
                  return Number(s.meta.final_arrival_secs);
                }
              }
              // fallback: use card initialDepartureSecs + card.totalSeconds
              if (o.card && Number.isFinite(o.card.initialDepartureSecs) && Number.isFinite(o.card.totalSeconds)) {
                return Number(o.card.initialDepartureSecs) + Number(o.card.totalSeconds);
              }
              return null;
            }).filter((v) => Number.isFinite(v));
            if (arrivalVals.length > 0) {
              const minArrival = Math.min(...arrivalVals);
              const EPS_A = 1; // seconds tolerance for ties
              for (const o of options) {
                // compute candidate arrival as above
                let cand = null;
                const srcs = o.sources ? Object.values(o.sources) : [];
                for (const s of srcs) {
                  if (s && s.meta && Number.isFinite(s.meta.initial_departure_secs) && Number.isFinite(s.meta.total_seconds)) {
                    cand = Number(s.meta.initial_departure_secs) + Number(s.meta.total_seconds);
                    break;
                  }
                  if (s && s.meta && Number.isFinite(s.meta.final_arrival_secs)) {
                    cand = Number(s.meta.final_arrival_secs);
                    break;
                  }
                }
                if (cand === null && o.card && Number.isFinite(o.card.initialDepartureSecs) && Number.isFinite(o.card.totalSeconds)) {
                  cand = Number(o.card.initialDepartureSecs) + Number(o.card.totalSeconds);
                }
                if (Number.isFinite(cand) && Math.abs(cand - minArrival) <= EPS_A) {
                  if (!o.labels.includes('E·A')) o.labels.unshift('E·A');
                }
              }
            }
          } catch (e) {
            // ignore arrival computation failures
          }
      } catch (e) {
        // don't block rendering on label computation failures
        // eslint-disable-next-line no-console
        console.warn('Failed to compute E·D/FASTEST labels', e);
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
            // label computation (E·D / FASTEST) runs consistently.
            const singleOpt = {
              id: 1,
              card: { ...mainCard, id: 1 },
              routeGeometries: attachEndpoints(mainJourney),
              labels: ['E·A'],
              label: 'E·A',
              sources: { 'E·A': mainJourney },
            };

            const optionsArr = [singleOpt];
            // Compute E·D / FASTEST labels for this single option as well
            try {
              const withInit = optionsArr.map((o) => ({ opt: o, init: (() => {
                const srcs = o.sources ? Object.values(o.sources) : [];
                for (const s of srcs) {
                  if (s && s.meta && Number.isFinite(s.meta.initial_departure_secs)) return Number(s.meta.initial_departure_secs);
                  if (s && s.meta && s.meta.initial_departure_time) {
                    const t = Date.parse(s.meta.initial_departure_time);
                    if (!Number.isNaN(t)) return Math.floor(t / 1000);
                  }
                }
                if (o.card && Number.isFinite(o.card.initialDepartureSecs)) return Number(o.card.initialDepartureSecs);
                return null;
              })() }));

              const initVals = withInit.map((w) => w.init).filter((v) => Number.isFinite(v));
              if (initVals.length > 0) {
                const minInit = Math.min(...initVals);
                for (const w of withInit) {
                  if (Number.isFinite(w.init) && w.init === minInit) {
                    if (!w.opt.labels.includes('E·D')) w.opt.labels.unshift('E·D');
                  }
                }
              }

              const totalVals = optionsArr.map((o) => (o.card && Number.isFinite(o.card.totalSeconds) ? o.card.totalSeconds : null)).filter((v) => Number.isFinite(v));
              if (totalVals.length > 0) {
                const minTotal = Math.min(...totalVals);
                const EPS = 1; // seconds tolerance for "equally fastest"
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
            setRouteOptions(sortedArr);
            // Prefetch per-leg OSRM geometry for the first option
            try {
              const firstOpt = sortedArr[0];
              const firstSrc = firstOpt ? Object.values(firstOpt.sources)[0] : null;
              const plan = firstSrc ? (firstSrc.route ? firstSrc.route : firstSrc) : null;
              const planLegs = plan?.legs || [];
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
      setRouteOptions(sortedOptions);
      // Prefetch per-leg geometry for the first displayed option
      try {
        const firstOpt = sortedOptions[0];
        const firstSrc = firstOpt ? Object.values(firstOpt.sources)[0] : null;
        const plan = firstSrc ? (firstSrc.route ? firstSrc.route : firstSrc) : null;
        const planLegs = plan?.legs;
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
      return;
    }

    const opt = routeOptions?.[idx];
    if (!opt || !opt.sources) {
      setSelectedRouteIdx(idx);
      return;
    }

    const firstSrc = Object.values(opt.sources)[0];
    const plan = firstSrc ? (firstSrc.route ? firstSrc.route : firstSrc) : null;
    const planLegs = plan?.legs;

    // If no legs available, just select without geometry
    if (!planLegs || !Array.isArray(planLegs) || planLegs.length === 0) {
      setSelectedRouteIdx(idx);
      return;
    }

    // If geometry was already prefetched, just select
    if (opt.routeGeometries && opt.routeGeometries.length > 0) {
      setSelectedRouteIdx(idx);
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
    <Paper elevation={1} sx={{ p: { xs: 2.5, md: 3 }, position: 'relative', zIndex: 1050, height: '100%', display: 'flex', flexDirection: 'column', border: '1px solid', borderColor: '#00bcd4' }}>
      <Stack spacing={2} sx={{ flex: '0 0 auto' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', position: 'relative', zIndex: 1051 }}>
          <Typography variant="h6" fontWeight={700}>
            Suggested routes
          </Typography>
          <Button
            size="small"
            variant="contained"
            onClick={() => setShowSuggested(false)}
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
      <Box sx={{ flex: '1 1 auto', overflowY: 'auto', pr: 1, mt: 1 }}>
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
                <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.75, flexWrap: 'wrap' }}>
                  <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', alignItems: 'center' }}>
                    {(() => {
                      // enforce requested display order and colours
                      const rawLabels = Array.isArray(opt.labels) ? opt.labels : [opt.label];
                      const ORDER = ['E·A', 'E·D', 'FASTEST', 'ECO', 'GREEDY', 'COSY', 'LAZY'];
                      const orderKey = (s) => String(s || '').toUpperCase();
                      const ORDER_UP = ORDER.map((o) => o.toUpperCase());
                      const colorMap = {
                        'E·A': '#FFF8E1', // light ivory
                        'E·D': '#E0BBE4', // lilac
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
                          <Box key={lab} sx={{ display: 'inline-block' }}>
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
                                fontSize: '0.56rem',
                                paddingLeft: 0.25,
                                paddingRight: 0.25,
                                minWidth: '0px',
                                lineHeight: '16px',
                                cursor: 'pointer',
                                px: 0.4,
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

                <Stack
                  direction={{ xs: "column", md: "row" }}
                  spacing={2}
                  alignItems={{ md: "flex-start" }}
                >
                {/* Leftmost: quick 'use my location' for the From field - always visible */}
                <IconButton
                  aria-label="Use my location"
                  onClick={handleUseMyLocation}
                  size="large"
                  sx={{ alignSelf: 'center' }}
                >
                    <Crosshair size={18} />
                </IconButton>

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
                    if (value && typeof value === "object") setFromLocation(value.display_name || value.name || "");
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
                            <Typography variant="body2" fontWeight={600}>
                              {label}
                            </Typography>
                            {optionType === "location" && (
                              <Typography variant="caption" sx={{ color: (theme) => theme.palette.mode === 'light' ? '#8B5E3C' : undefined }}>
                                Location
                              </Typography>
                            )}
                          </Box>
                          <Chip label={optionType === "location" ? "Location" : "Stop"} size="small" variant="outlined" />
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
                          <InputAdornment position="start">
                            <MapPin size={18} />
                          </InputAdornment>
                        ),
                        endAdornment: fromLoading ? (
                          <CircularProgress color="inherit" size={20} />
                        ) : (
                          params.InputProps.endAdornment
                        ),
                      }}
                      sx={{
                        "& .MuiOutlinedInput-root": {
                          "& fieldset": { borderColor: '#00BCD4' },
                          "&:hover fieldset": { borderColor: '#00BCD4' },
                          "&.Mui-focused fieldset": { borderColor: '#00BCD4' },
                          boxShadow: 'none',
                        },
                        "& .MuiInputBase-input": {
                          color: (theme) => theme.palette.mode === "dark" ? "#fff" : undefined,
                          textOverflow: "ellipsis",
                        },
                        "& .MuiInputLabel-root": { color: (theme) => theme.palette.mode === "dark" ? "rgba(255,255,255,0.7)" : undefined },
                      }}
                    />
                  )}
                />

                {/* Swap button: exchange From and To values */}
                <IconButton
                  aria-label="Swap start and destination"
                  onClick={() => {
                    // swap both the selected stop objects and the input strings
                    setSelectedFromStop((prevFrom) => {
                      // use functional updates to ensure sync
                      const oldFrom = prevFrom;
                      setSelectedToStop(oldFrom);
                      return selectedToStop;
                    });
                    setSelectedToStop((prev) => prev); // no-op to satisfy eslint-like rules
                    // swap input text values
                    setFromLocation((prevFromLoc) => {
                      const oldFromLoc = prevFromLoc;
                      setToLocation(oldFromLoc);
                      return toLocation;
                    });
                  }}
                  size="large"
                  sx={{
                    alignSelf: 'center',
                    width: 56,
                    height: 56,
                    p: 0,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    borderRadius: '12px',
                    // Ensure keyboard focus is visible
                    '&:focus-visible': { outline: '2px solid', outlineOffset: 2 }
                  }}
                >
                  <span style={{ fontSize: 22, lineHeight: 1 }}>⇄</span>
                </IconButton>

                <Autocomplete
                  fullWidth
                  freeSolo
                  filterOptions={(x) => x}
                  options={allStops.to}
                  ListboxProps={{ sx: { maxHeight: '510px' } }}
                  getOptionLabel={(option) => (typeof option === "string" ? option : (option.display_name || option.name || ""))}
                  value={selectedToStop}
                  onChange={(e, value) => {
                    if (typeof value === "string") {
                      setSelectedToStop(null);
                      setToLocation(value);
                      return;
                    }
                    setSelectedToStop(value);
                    if (value && typeof value === "object") setToLocation(value.display_name || value.name || "");
                  }}
                  inputValue={toLocation}
                  onInputChange={(e, value) => setToLocation(value)}
                  loading={toLoading}
                  renderOption={(props, option) => {
                    const label = typeof option === "string" ? option : (option.display_name || option.name);
                    const optionType = typeof option === "string" ? "stop" : option.type || "stop";
                    return (
                      <Box component="li" {...props} sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                          <Box sx={{ color: (theme) => theme.palette.mode === 'light' && optionType === 'location' ? '#8B5E3C' : undefined }}>
                            {optionType === "location" ? <MapPin size={16} /> : <Bus size={16} />}
                          </Box>
                          <Box sx={{ flexGrow: 1 }}>
                            <Typography variant="body2" fontWeight={600}>
                              {label}
                            </Typography>
                            {optionType === "location" && (
                              <Typography variant="caption" sx={{ color: (theme) => theme.palette.mode === 'light' ? '#8B5E3C' : undefined }}>
                                Location
                              </Typography>
                            )}
                          </Box>
                          <Chip label={optionType === "location" ? "Location" : "Stop"} size="small" variant="outlined" />
                        </Box>
                    );
                  }}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      label="To"
                      InputProps={{
                        ...params.InputProps,
                        startAdornment: (
                            <InputAdornment position="start">
                            <NavIcon size={18} />
                          </InputAdornment>
                        ),
                        endAdornment: toLoading ? (
                          <CircularProgress color="inherit" size={20} />
                        ) : (
                          params.InputProps.endAdornment
                        ),
                      }}
                      sx={{
                        "& .MuiOutlinedInput-root": {
                          "& fieldset": { borderColor: '#00BCD4' },
                          "&:hover fieldset": { borderColor: '#00BCD4' },
                          "&.Mui-focused fieldset": { borderColor: '#00BCD4' },
                          boxShadow: 'none',
                        },
                        "& .MuiInputBase-input": {
                          color: (theme) => theme.palette.mode === "dark" ? "#fff" : undefined,
                          textOverflow: "ellipsis",
                        },
                        "& .MuiInputLabel-root": { color: (theme) => theme.palette.mode === "dark" ? "rgba(255,255,255,0.7)" : undefined },
                      }}
                    />
                  )}
                />

            <Button
              variant="contained"
              size="large"
              sx={{
                minWidth: { xs: "100%", md: 180 },
                height: 56,
                flexShrink: 0,
              }}
              onClick={handleSearch}
              disabled={!fromCoords || !toCoords || isSearching}
            >
              {isSearching ? <CircularProgress size={24} color="inherit" /> : "Search routes"}
            </Button>
          </Stack>

          {/* Date/time/transfers controls moved here (below search inputs) */}
          <Box sx={{ mt: 2, display: 'flex', gap: 1, alignItems: 'center', justifyContent: 'center', flexWrap: 'wrap' }}>
            <TextField
              label="Date"
              type="date"
              size="small"
              value={departureDate}
              onChange={(e) => setDepartureDate(e.target.value)}
              InputLabelProps={{ shrink: true }}
              sx={{ minWidth: 140, "& .MuiOutlinedInput-root": { "& fieldset": { borderColor: '#00BCD4' }, "&:hover fieldset": { borderColor: '#00BCD4' }, "&.Mui-focused fieldset": { borderColor: '#00BCD4' }, }, }}
            />
            <TextField
              label="Time"
              type="time"
              size="small"
              value={departureClock}
              onChange={(e) => setDepartureClock(e.target.value)}
              InputLabelProps={{ shrink: true }}
              sx={{ minWidth: 110, "& .MuiOutlinedInput-root": { "& fieldset": { borderColor: '#00BCD4' }, "&:hover fieldset": { borderColor: '#00BCD4' }, "&.Mui-focused fieldset": { borderColor: '#00BCD4' }, }, }}
            />
            <TextField
              label="Transfers"
              select
              size="small"
              value={maxTransfers}
              onChange={(e) => {
                const v = Number(e.target.value);
                // clamp to 0..5 defensively
                setMaxTransfers(Number.isFinite(v) ? Math.max(0, Math.min(5, v)) : 0);
              }}
              sx={{ width: 110, "& .MuiOutlinedInput-root": { "& fieldset": { borderColor: '#00BCD4' }, "&:hover fieldset": { borderColor: '#00BCD4' }, "&.Mui-focused fieldset": { borderColor: '#00BCD4' }, }, }}
            >
              {[0,1,2,3,4,5].map((n) => (
                <MenuItem key={n} value={n}>{n}</MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              value={transportMode}
              onChange={(e) => {
                const val = e.target.value;
                if (val !== null) setTransportMode(val);
              }}
              sx={{ ml: 1, minWidth: 120, maxWidth: 180, "& .MuiOutlinedInput-root": { "& fieldset": { borderColor: '#00BCD4' }, "&:hover fieldset": { borderColor: '#00BCD4' }, "&.Mui-focused fieldset": { borderColor: '#00BCD4' }, }, }}
              SelectProps={{
                renderValue: (selected) => {
                  if (!selected) return '';
                  return selected === 'all' ? 'All' : selected.charAt(0).toUpperCase() + selected.slice(1);
                },
              }}
            >
              <MenuItem value="all">All</MenuItem>
              <MenuItem value="bus">Bus</MenuItem>
              <MenuItem value="train">Train</MenuItem>
            </TextField>
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

        <Stack direction="row" spacing={1.5} mb={2} flexWrap="wrap" alignItems="center">
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
            onClosePopup={() => setOpenPopupId(null)}
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
