import Box from "@mui/material/Box";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Skeleton from "@mui/material/Skeleton";
import Autocomplete from "@mui/material/Autocomplete";
import TextField from "@mui/material/TextField";
import InputAdornment from "@mui/material/InputAdornment";
import { MapPin, Bus, Train, Search } from "lucide-react";
import { lazy, Suspense, useMemo, useState, useEffect, useCallback } from "react";
import { useLiveBusLocations, useLiveDepartures, useLiveUpdates, useStopSearch } from "../hooks/useTransportData";
import { useBusStops } from "../hooks/useBusStops";

const MapViewMap = lazy(() => import("../components/map/MapViewMap"));


// Static mock stops / trains shown before API data loads
const MOCK_MARKERS = [
  { id: 't1', position: [54.048889, -2.802500], name: "Lancaster Train Station", type: "train", status: "On time" },
  { id: 't2', position: [53.995000, -2.700000], name: "Morecambe Station",        type: "train", status: "On time" },
  { id: 't3', position: [54.080000, -2.700000], name: "Carnforth Station",         type: "train", status: "On time" },
];

// Animated mock buses — each follows a short looping route around Lancaster.
// `waypoints` is a closed loop of [lat, lon] the bus cycles through.
const MOCK_BUS_ROUTES = [
  {
    id: 'mock-bus-1', routeNumber: '1', operator: 'Demo Buses', delayMinutes: 0,
    waypoints: [
      [54.0480, -2.8010], [54.0510, -2.7980], [54.0540, -2.8020],
      [54.0520, -2.8060], [54.0490, -2.8050], [54.0480, -2.8010],
    ],
  },
  {
    id: 'mock-bus-2', routeNumber: '2', operator: 'Demo Buses', delayMinutes: 4,
    waypoints: [
      [54.0460, -2.7990], [54.0440, -2.8040], [54.0430, -2.8090],
      [54.0460, -2.8120], [54.0490, -2.8080], [54.0460, -2.7990],
    ],
  },
  {
    id: 'mock-bus-3', routeNumber: 'X4', operator: 'Demo Buses', delayMinutes: 12,
    waypoints: [
      [54.0530, -2.7950], [54.0560, -2.7900], [54.0580, -2.7960],
      [54.0550, -2.8010], [54.0530, -2.7980], [54.0530, -2.7950],
    ],
  },
  {
    id: 'mock-bus-4', routeNumber: '41', operator: 'Demo Buses', delayMinutes: 0,
    waypoints: [
      [54.0420, -2.8000], [54.0400, -2.7950], [54.0380, -2.8000],
      [54.0400, -2.8050], [54.0420, -2.8000],
    ],
  },
];

/**
 * Compute bearing in degrees (0 = north, clockwise) from point A to point B.
 */
function calcBearing([lat1, lon1], [lat2, lon2]) {
  const toRad = (d) => (d * Math.PI) / 180;
  const dLon = toRad(lon2 - lon1);
  const φ1 = toRad(lat1);
  const φ2 = toRad(lat2);
  const y = Math.sin(dLon) * Math.cos(φ2);
  const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(dLon);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/**
 * Interpolate between two waypoints [lat,lon] by fraction t ∈ [0,1].
 */
function lerpWaypoint([lat1, lon1], [lat2, lon2], t) {
  return [lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t];
}

// Each bus travels along its route at a fixed speed; the phase encodes
// how far along the full route (0–1) the bus has progressed.
// Different initial phases so buses don't all start at waypoint 0.
const INITIAL_PHASES = { 'mock-bus-1': 0.0, 'mock-bus-2': 0.35, 'mock-bus-3': 0.6, 'mock-bus-4': 0.15 };
// Speed: fraction of full route per second
const BUS_SPEED = 0.012;

// Default map center (Lancaster)
const DEFAULT_CENTER = { lat: 54.050556, lon: -2.800556 };

export default function MapViewPage() {
const [markers, setMarkers] = useState(MOCK_MARKERS); // Start with mock data for instant display
const [filters, setFilters] = useState({
showBuses: true,
showTrains: true
});
const [openPopupId, setOpenPopupId] = useState(null);
const [apiError, setApiError] = useState(null);
const [userLocation, setUserLocation] = useState(null);
const [locationStatus, setLocationStatus] = useState('idle');
const [locationError, setLocationError] = useState(null);
const [mapInstance, setMapInstance] = useState(null);

// Track current map center for dynamic bus-live queries
const [mapCenter, setMapCenter] = useState(DEFAULT_CENTER);

// Called by MapViewMap whenever the map finishes panning/zooming
const handleMoveEnd = useCallback(({ lat, lon }) => {
  setMapCenter({ lat, lon });
}, []);

// Search bar state
const [searchQuery, setSearchQuery] = useState('');
const [searchValue, setSearchValue] = useState(null);
const { results: searchResults, loading: searchLoading } = useStopSearch(searchQuery, 800, mapCenter);

const handleSearchSelect = useCallback((option) => {
  if (!option || !mapInstance) return;
  const lat = option.lat ?? option.latitude;
  const lon = option.lon ?? option.longitude;
  if (typeof lat === 'number' && typeof lon === 'number') {
    mapInstance.flyTo([lat, lon], Math.max(mapInstance.getZoom(), 15), { duration: 1.2 });
  }
  setSearchValue(null);
  setSearchQuery('');
}, [mapInstance]);

// Fetch real data from API using the current map center (debounced inside the hook)
const { data: busLocations, loading: busLoading, error: busError } = useLiveBusLocations('SCCU', {
lat: mapCenter.lat,
lon: mapCenter.lon,
refreshInterval: 30000,
debounceMs: 800,
});
const { data: trainDepartures, loading: trainLoading, error: trainError } = useLiveDepartures('LAN', 180000);
const { data: liveBusUpdate, isConnected: busLiveConnected } = useLiveUpdates('bus');
const { data: liveTrainUpdate, isConnected: trainLiveConnected } = useLiveUpdates('train');

// Track whether real API data has ever arrived so we know when to stop the mock animation.
const [hasRealData, setHasRealData] = useState(false);

// Animated mock buses — runs until the real API returns data.
useEffect(() => {
  if (hasRealData) return; // stop as soon as real data takes over
  const phases = { ...INITIAL_PHASES };
  const TICK_MS = 100; // update every 100 ms → smooth movement

  const intervalId = setInterval(() => {
    const mockBusMarkers = MOCK_BUS_ROUTES.map((route) => {
      // Advance phase
      phases[route.id] = (phases[route.id] + BUS_SPEED * (TICK_MS / 1000)) % 1;
      const phase = phases[route.id];

      // Which segment are we on?
      const wps = route.waypoints;
      const segCount = wps.length - 1;
      const globalT = phase * segCount;
      const segIdx = Math.min(Math.floor(globalT), segCount - 1);
      const t = globalT - segIdx;

      const from = wps[segIdx];
      const to = wps[segIdx + 1];
      const position = lerpWaypoint(from, to, t);
      const bearing = calcBearing(from, to);

      const dm = route.delayMinutes;
      const status = dm >= 10 ? `Delayed ${dm} mins` : dm >= 2 ? `Delayed ${dm} mins` : 'On time';

      return {
        id: route.id,
        type: 'bus',
        name: `Bus ${route.routeNumber}`,
        routeNumber: route.routeNumber,
        operator: route.operator,
        delayMinutes: dm,
        status,
        bearing,
        position,
      };
    });

    setMarkers([...MOCK_MARKERS, ...mockBusMarkers]);
  }, TICK_MS);

  return () => clearInterval(intervalId);
}, [hasRealData]);

// Update markers when real API data arrives; stops the mock animation once data is available.
useEffect(() => {
// Only update if we have real data from the API
if ((Array.isArray(busLocations) && busLocations.length > 0) || 
    (Array.isArray(trainDepartures) && trainDepartures.length > 0)) {

const newMarkers = [];
let id = 1;

// Add bus locations
if (Array.isArray(busLocations) && busLocations.length > 0) {
busLocations.forEach(bus => {
newMarkers.push({
id: id++,
position: [bus.latitude || bus.lat, bus.longitude || bus.lon],
name: bus.name || `Bus ${bus.id}`,
type: 'bus',
status: bus.status || 'On time',
routeNumber: bus.routeNumber || bus.route,
delayMinutes: bus.delay_minutes ?? bus.delayMinutes ?? null,
operator: bus.operator || bus.operator_name || null,
bearing: bus.bearing ?? bus.Bearing ?? bus.bearing_degrees ?? bus.heading ?? bus.course ?? null,
meta: bus.meta ?? null,
});
});
}

// Add train departures
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
setHasRealData(true);
setApiError(null);
} else if (busError || trainError) {
// Show error message but keep mock data
setApiError('Using demo data - API temporarily unavailable');
}
}, [busLocations, trainDepartures, busError, trainError]);

const filteredMarkers = useMemo(() => (
markers.filter(m => 
(m.type === 'bus' && filters.showBuses) || 
(m.type === 'train' && filters.showTrains)
)
), [markers, filters.showBuses, filters.showTrains]);

const distanceMeters = useMemo(() => {
const toRadians = (deg) => (deg * Math.PI) / 180;
return (a, b) => {
const R = 6371000;
const dLat = toRadians(b[0] - a[0]);
const dLon = toRadians(b[1] - a[1]);
const lat1 = toRadians(a[0]);
const lat2 = toRadians(b[0]);
const x = Math.sin(dLat / 2) ** 2 + Math.sin(dLon / 2) ** 2 * Math.cos(lat1) * Math.cos(lat2);
const c = 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
return R * c;
};
}, []);

const { stops: busStops, loading: stopsLoading } = useBusStops({ enabled: true });

const nearestStop = useMemo(() => {
  if (!userLocation) return null;
  // Prefer backend busStops when available; fallback to markers.
  const source = (Array.isArray(busStops) && busStops.length > 0) ? busStops : markers;
  if (!source || source.length === 0) return null;
  let nearest = null;
  for (const s of source) {
    const pos = s.position || (s.lat != null && s.lon != null ? [s.lat, s.lon] : null);
    if (!pos) continue;
    const distance = distanceMeters(userLocation, pos);
      if (!nearest || distance < nearest.distance) {
      // determine kind: prefer explicit type, treat 'rail' classification as train,
      // otherwise assume bus (backend bus stops include `lines`/`classification`)
      const kind = s.type ?? (s.classification === 'rail' ? 'train' : 'bus');
      // normalize name for display
      const name = s.name || s.display_name || (kind === 'bus' ? (s.routeNumber ? `Bus ${s.routeNumber}` : 'Bus') : kind || 'Stop');
      nearest = { ...s, position: pos, distance, name, type: kind };
    }
  }
  return nearest;
}, [userLocation, busStops, markers, distanceMeters]);

// Prefer real bus-stop data from the backend for the "closest stops" UI.
// Fallback to computing nearest markers when no bus-stop data is available.
const closestStops = useMemo(() => {
  if (!userLocation) return [];
  const source = (Array.isArray(busStops) && busStops.length > 0) ? busStops : markers;
  const withDistance = source.map((s) => {
    const pos = s.position || (s.lat != null && s.lon != null ? [s.lat, s.lon] : null);
    if (!pos) return null;
    const kind = s.type ?? (s.classification === 'rail' ? 'train' : 'bus');
    return { ...s, distance: distanceMeters(userLocation, pos), type: kind };
  }).filter(Boolean);
  return withDistance.sort((a, b) => a.distance - b.distance).slice(0, 3);
}, [userLocation, busStops, markers, distanceMeters]);

const formatWalkTime = (distance) => {
const minutes = Math.max(1, Math.round(distance / 84)); // ~1.4 m/s walking speed
return `${minutes} min walk`;
};

const normalizeLiveMarker = (item, type) => {
const lat = item?.latitude ?? item?.lat;
const lon = item?.longitude ?? item?.lon;
if (typeof lat !== 'number' || typeof lon !== 'number') return null;
return {
id: item?.vehicleId || item?.id || `${type}-${lat}-${lon}`,
position: [lat, lon],
name: item?.name || item?.label || (type === 'bus' ? `Bus ${item?.route || item?.routeNumber || ''}`.trim() : item?.station || 'Train'),
type,
status: item?.status || (item?.delayMinutes ? `Delayed ${item.delayMinutes} mins` : 'On time'),
routeNumber: item?.routeNumber || item?.route,
destination: item?.destination,
departureTime: item?.departureTime || item?.scheduledTime
  ,
  delayMinutes: item?.delay_minutes ?? item?.delayMinutes ?? null,
  operator: item?.operator || item?.operator_name || null,
  bearing: item?.bearing ?? item?.Bearing ?? item?.bearing_degrees ?? item?.heading ?? item?.course ?? null,
};
};

useEffect(() => {
const updates = Array.isArray(liveBusUpdate) ? liveBusUpdate : (liveBusUpdate ? [liveBusUpdate] : []);
const normalized = updates.map((item) => normalizeLiveMarker(item, 'bus')).filter(Boolean);
if (normalized.length === 0) return;

setMarkers((prev) => {
const next = new Map(prev.map((m) => [m.id, m]));
for (const item of normalized) {
next.set(item.id, { ...next.get(item.id), ...item });
}
return Array.from(next.values());
});
}, [liveBusUpdate]);

useEffect(() => {
const updates = Array.isArray(liveTrainUpdate) ? liveTrainUpdate : (liveTrainUpdate ? [liveTrainUpdate] : []);
const normalized = updates.map((item) => normalizeLiveMarker(item, 'train')).filter(Boolean);
if (normalized.length === 0) return;

setMarkers((prev) => {
const next = new Map(prev.map((m) => [m.id, m]));
for (const item of normalized) {
next.set(item.id, { ...next.get(item.id), ...item });
}
return Array.from(next.values());
});
}, [liveTrainUpdate]);

const requestLocation = () => {
if (!navigator.geolocation) {
setLocationStatus('error');
setLocationError('Geolocation is not supported by this browser.');
return;
}

setLocationStatus('loading');
setLocationError(null);
navigator.geolocation.getCurrentPosition(
(position) => {
const coords = [position.coords.latitude, position.coords.longitude];
setUserLocation(coords);
setLocationStatus('granted');
if (mapInstance) {
mapInstance.flyTo(coords, Math.max(mapInstance.getZoom(), 12), { duration: 1.2 });
}
},
(error) => {
setLocationStatus('error');
setLocationError(error?.message || 'Location permission denied.');
},
{ enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
);
};

const handleCenterOnUser = () => {
if (userLocation && mapInstance) {
mapInstance.flyTo(userLocation, Math.max(mapInstance.getZoom(), 12), { duration: 1.2 });
}
};

const MapFallback = () => (
<Stack direction={{ xs: "column", md: "row" }} spacing={3} sx={{ height: { xs: 'auto', md: 750 } }}>
<Skeleton variant="rounded" sx={{ flex: 1, height: { xs: 420, sm: 520, md: 750 } }} />
<Skeleton variant="rounded" sx={{ minWidth: { xs: '100%', md: 320 }, height: { xs: 220, md: 320 } }} />
</Stack>
);

return (
<Stack spacing={2} sx={{ height: '100%', mb: 2 }}>
{apiError && (
<Alert severity="warning" sx={{ borderRadius: '12px' }}>
<Typography variant="body2">
Note: Using mock data as fallback. To see real data, ensure the API at <code>https://transport.scc.lancs.ac.uk</code> is accessible. Error: {apiError}
</Typography>
</Alert>
)}

<Paper elevation={0} sx={{ 
p: { xs: 2, md: 3 }, 
borderRadius: '16px',
border: '1px solid',
borderColor: 'divider'
}}>
<Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} mb={2} flexWrap="wrap" alignItems={{ xs: "stretch", sm: "center" }}>
{/* Location search bar */}
<Autocomplete
  sx={{ flex: 1, minWidth: { xs: 0, sm: 220 } }}
  freeSolo
  filterOptions={(x) => x}
  options={searchResults || []}
  getOptionLabel={(option) => typeof option === 'string' ? option : (option.display_name || option.name || '')}
  value={searchValue}
  inputValue={searchQuery}
  onInputChange={(_, val) => setSearchQuery(val)}
  onChange={(_, option) => {
    if (option && typeof option === 'object') {
      setSearchValue(option);
      handleSearchSelect(option);
    }
  }}
  loading={searchLoading}
  renderOption={(props, option) => (
    <Box component="li" {...props} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
      <MapPin size={14} />
      <Typography variant="body2">{option.display_name || option.name}</Typography>
    </Box>
  )}
  renderInput={(params) => (
    <TextField
      {...params}
      size="small"
      placeholder="Search stops or places…"
      InputProps={{
        ...params.InputProps,
        startAdornment: (
          <InputAdornment position="start">
            <Search size={16} />
          </InputAdornment>
        ),
        endAdornment: searchLoading
          ? <CircularProgress size={16} />
          : params.InputProps.endAdornment,
      }}
      sx={{ '& .MuiOutlinedInput-root': { borderRadius: '10px' } }}
    />
  )}
/>
<Box
onClick={() => setFilters(f => ({ ...f, showBuses: !f.showBuses }))}
sx={{
padding: '12px 20px',
border: `2px solid ${filters.showBuses ? '#6366F1' : '#E2E8F0'}`,
borderRadius: '10px',
display: 'flex',
alignItems: 'center',
justifyContent: 'center',
width: { xs: '100%', sm: 'auto' },
gap: 1,
cursor: 'pointer',
backgroundColor: filters.showBuses ? '#6366F1' : 'transparent',
color: filters.showBuses ? 'white' : 'inherit',
fontWeight: 600,
transition: 'all 0.3s ease',
'&:hover': {
boxShadow: '0 4px 12px rgba(99,102,241,0.25)',
transform: 'translateY(-2px)',
borderColor: '#6366F1',
backgroundColor: filters.showBuses ? '#4F46E5' : 'rgba(99,102,241,0.08)'
},
'&:active': {
transform: 'translateY(0px)'
}
}}
>
<Bus size={20} />
Buses {filteredMarkers.filter(m => m.type === 'bus').length}
</Box>
<Box
onClick={() => setFilters(f => ({ ...f, showTrains: !f.showTrains }))}
sx={{
padding: '12px 20px',
border: `2px solid ${filters.showTrains ? '#10B981' : '#E2E8F0'}`,
borderRadius: '10px',
display: 'flex',
alignItems: 'center',
justifyContent: 'center',
width: { xs: '100%', sm: 'auto' },
gap: 1,
cursor: 'pointer',
backgroundColor: filters.showTrains ? '#10B981' : 'transparent',
color: filters.showTrains ? 'white' : 'inherit',
fontWeight: 600,
transition: 'all 0.3s ease',
'&:hover': {
boxShadow: '0 4px 12px rgba(16,185,129,0.25)',
transform: 'translateY(-2px)',
borderColor: '#10B981',
backgroundColor: filters.showTrains ? '#059669' : 'rgba(16,185,129,0.08)'
},
'&:active': {
transform: 'translateY(0px)'
}
}}
>
<Train size={20} />
Trains {filteredMarkers.filter(m => m.type === 'train').length}
</Box>

<Box sx={{ flex: 1, display: { xs: 'none', sm: 'block' } }} />
{!userLocation && (
    <Button
        variant="outlined"
        size="small"
        onClick={requestLocation}
        disabled={locationStatus === 'loading'}
        sx={{ borderRadius: '10px', textTransform: 'none', width: { xs: '100%', sm: 'auto' } }}
    >
        {locationStatus === 'loading' ? (
            <Stack direction="row" spacing={1} alignItems="center">
                <CircularProgress size={16} />
                <Typography variant="caption">Locating</Typography>
            </Stack>
        ) : (
            'Use my location'
        )}
    </Button>
)}
{userLocation && (
<Button
variant="contained"
size="small"
onClick={handleCenterOnUser}
sx={{ borderRadius: '10px', textTransform: 'none', width: { xs: '100%', sm: 'auto' }, backgroundColor: '#D97974', color: '#ffffff', '&:hover': { backgroundColor: '#c86b66' } }}
>
Center on me
</Button>
)}
</Stack>

{locationError && (
<Alert severity="warning" sx={{ borderRadius: '10px', mb: 2 }}>
<Typography variant="body2">{locationError}</Typography>
</Alert>
)}

{userLocation && nearestStop && (
<Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ xs: "stretch", sm: "center" }} mb={2} flexWrap="wrap">
<Chip
label={`Nearest: ${nearestStop.name}`}
variant="outlined"
sx={{ borderRadius: '10px' }}
/
>
<Chip
label={`${(nearestStop.distance / 1000).toFixed(1)} km · ${formatWalkTime(nearestStop.distance)}`}
color="warning"
variant="outlined"
sx={{ borderRadius: '10px' }}
/
>
{locationStatus === 'watching' && (
<Chip
label="Following your location"
color="success"
variant="outlined"
sx={{ borderRadius: '10px' }}
/>
)}
{(busLiveConnected || trainLiveConnected) && (
<Chip
label="Live updates connected"
color="primary"
variant="outlined"
sx={{ borderRadius: '10px' }}
/>
)}
</Stack>
)}

{userLocation && closestStops.length > 0 && (
<Paper elevation={0} sx={{
p: 2,
mb: 2,
borderRadius: '12px',
border: '1px solid',
borderColor: 'divider',
backgroundColor: 'rgba(99,102,241,0.04)'
}}>
<Stack spacing={1}>
<Typography variant="subtitle2" fontWeight={700}>
Closest stops
</Typography>
{closestStops.map((stop) => (
<Stack key={stop.id} direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ xs: 'flex-start', sm: 'center' }} justifyContent="space-between">
<Stack direction="row" spacing={1} alignItems="center">
<Chip
label={stop.type === 'bus' ? 'Bus' : 'Train'}
size="small"
color={stop.type === 'bus' ? 'primary' : 'success'}
variant="outlined"
sx={{ borderRadius: '10px' }}
/>
<Typography variant="body2" fontWeight={600}>
{stop.name}
</Typography>
</Stack>
<Typography variant="caption" color="text.secondary">
{(stop.distance / 1000).toFixed(1)} km  {formatWalkTime(stop.distance)}
</Typography>
</Stack>
))}
</Stack>
</Paper>
)}

<Suspense fallback={<MapFallback />}>
<MapViewMap
filteredMarkers={filteredMarkers}
openPopupId={openPopupId}
onOpenPopup={setOpenPopupId}
onClosePopup={() => setOpenPopupId(null)}
userLocation={userLocation}
nearestStop={nearestStop}
busLoading={busLoading}
trainLoading={trainLoading}
onMapReady={setMapInstance}
onMoveEnd={handleMoveEnd}
/>
</Suspense>
</Paper>
</Stack>
)
}
