import React, { useMemo, useState, useEffect, useCallback, lazy, Suspense } from "react";
import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
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

    return {
      type,
      route: leg.line_name || "",
      duration: durMin >= 60
        ? `${Math.floor(durMin / 60)}h ${durMin % 60} mins`
        : `${durMin} mins`,
      from: fromName,
      to: toName,
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
  if (meta.total_duration) {
    duration = meta.total_duration;
  } else {
    const totalSec = legs.reduce((s, l) => s + (l.duration_seconds ?? 0), 0);
    const totalMin = Math.round(totalSec / 60);
    duration = totalMin >= 60
      ? `${Math.floor(totalMin / 60)}h ${totalMin % 60} mins`
      : `${totalMin} mins`;
  }

  return {
    id: 1,
    duration,
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

export default function HomePage() {
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
  const { results: fromStopResults, loading: fromLoading } = useStopSearch(fromLocation, 300);
  const { results: toStopResults, loading: toLoading } = useStopSearch(toLocation, 300);

  const { alerts: serviceAlerts, loading: alertsLoading } = useServiceAlerts();
  const { data: liveAlertUpdate, isConnected: alertsConnected } = useLiveUpdates("alerts");
  const [liveAlerts, setLiveAlerts] = useState([]);
  // Array of { card, routeGeometries } — one entry per alternative route
  const [routeOptions, setRouteOptions] = useState([]);
  // Index of the card the user has clicked / selected (controls map geometry)
  const [selectedRouteIdx, setSelectedRouteIdx] = useState(0);
  const [showSuggested, setShowSuggested] = useState(false);
  // Geometry drawn on the map: derived from the selected option
  const journeyRoute = routeOptions[selectedRouteIdx]?.routeGeometries ?? null;

  // State for showing router/label details when a label chip is clicked
  const [labelDetail, setLabelDetail] = useState({ open: false, label: '', content: null });

  // ---- Map + live-bus state (merged from map-view-page) ----
  const [markers, setMarkers] = useState(MOCK_MARKERS);
  const [filters, setFilters] = useState({ showBuses: true, showTrains: true });
  const [openPopupId, setOpenPopupId] = useState(null);
  const [mapInstance, setMapInstance] = useState(null);
  const [mapCenter, setMapCenter] = useState(DEFAULT_CENTER);
  const [geoError, setGeoError] = useState(null);
  const [userLocation, setUserLocation] = useState(null);
  const [locationStatus, setLocationStatus] = useState('idle');
  const [locationError, setLocationError] = useState(null);

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
  const { data: trainDepartures, loading: trainLoading, error: trainError } = useLiveDepartures("LAN", 30000);
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
    setSelectedRouteIdx(0);
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
        { key: 'lazy', label: 'Lazy' },
        { key: 'greedy', label: 'Greedy' },
      ];

      // Build options but merge identical journeys (same steps) and collect their labels.
      const optionsMap = new Map(); // signature -> option

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
                routeGeometries: Array.isArray(journey.routeGeometries) ? journey.routeGeometries : [],
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
            setRouteOptions([{
              id: 1,
              card: { ...mainCard, id: 1 },
              routeGeometries: Array.isArray(mainJourney.routeGeometries) ? mainJourney.routeGeometries : [],
              label: 'E·A',
            }]);
            setSelectedRouteIdx(0);
            setIsSearching(false);
            return;
          }
        } catch (e) {
          // ignore and fall back to showing placeholders below
        }
      }

      setRouteOptions(options);
      setSelectedRouteIdx(0);
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

  const handleSelectRoute = (idx) => {
    setSelectedRouteIdx(idx);
  };

  const handleOpenLabelDetail = (opt, label) => {
    const content = opt?.sources?.[label] ?? null;
    setLabelDetail({ open: true, label, content });
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
          <IconButton size="small" onClick={() => setShowSuggested(false)} aria-label="Close suggested routes">
            <X size={14} />
          </IconButton>
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
                    {(Array.isArray(opt.labels) ? opt.labels : [opt.label]).map((lab) => (
                      <Chip
                        key={lab}
                        size="small"
                        label={String(lab).toUpperCase()}
                        onClick={(e) => { e.stopPropagation(); handleOpenLabelDetail(opt, lab); }}
                        sx={{
                          fontWeight: 700,
                          borderRadius: 1,
                          backgroundColor: idx === selectedRouteIdx ? '#00bcd4' : 'grey.300',
                          color: idx === selectedRouteIdx ? 'white' : 'text.primary',
                          height: 22,
                          fontSize: '0.68rem',
                          paddingLeft: 1,
                          paddingRight: 1,
                          cursor: 'pointer',
                        }}
                      />
                    ))}
                  </Stack>
                  <Typography variant="caption" sx={{ fontWeight: 700, color: idx === selectedRouteIdx ? '#00bcd4' : 'white' }}>
                    {idx === selectedRouteIdx ? ' — shown on map' : ''}
                  </Typography>
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
    <Stack spacing={{ xs: 1.5, md: 2 }}>
      {/* Dialog to show per-router/label details when a label chip is clicked */}
      <Dialog open={labelDetail.open} onClose={() => setLabelDetail({ ...labelDetail, open: false })} maxWidth="md" fullWidth>
        <DialogTitle>{labelDetail.label ? String(labelDetail.label).toUpperCase() : 'Details'}</DialogTitle>
        <DialogContent dividers>
          {labelDetail.content ? (
            <Box component="pre" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: '0.8rem', m: 0 }}>
              {JSON.stringify(labelDetail.content, null, 2)}
            </Box>
          ) : (
            <Typography>No details available for this label.</Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setLabelDetail({ ...labelDetail, open: false })}>Close</Button>
        </DialogActions>
      </Dialog>
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
                          "& fieldset": { borderColor: (theme) => theme.palette.mode === "dark" ? "rgba(255,255,255,0.7)" : undefined },
                          "&:hover fieldset": { borderColor: (theme) => theme.palette.mode === "dark" ? "#fff" : undefined },
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
                          "& fieldset": { borderColor: (theme) => theme.palette.mode === "dark" ? "rgba(255,255,255,0.7)" : undefined },
                          "&:hover fieldset": { borderColor: (theme) => theme.palette.mode === "dark" ? "#fff" : undefined },
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
              sx={{ minWidth: 140 }}
            />
            <TextField
              label="Time"
              type="time"
              size="small"
              value={departureClock}
              onChange={(e) => setDepartureClock(e.target.value)}
              InputLabelProps={{ shrink: true }}
              sx={{ minWidth: 110 }}
            />
            <TextField
              label="Transfers"
              type="number"
              size="small"
              value={maxTransfers}
              onChange={(e) => setMaxTransfers(Math.max(0, Math.min(10, Number(e.target.value) || 0)))}
              InputProps={{ inputProps: { min: 0, max: 10 } }}
              sx={{ width: 110 }}
            />
            <TextField
              select
              size="small"
              value={transportMode}
              onChange={(e) => {
                const val = e.target.value;
                if (val !== null) setTransportMode(val);
              }}
              sx={{ ml: 1, minWidth: 120, maxWidth: 180 }}
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
                {alert.message}
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
  );
}
