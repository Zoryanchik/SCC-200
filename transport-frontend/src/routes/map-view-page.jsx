import Box from "@mui/material/Box";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Skeleton from "@mui/material/Skeleton";
import { MapPin, Bus, Train } from "lucide-react";
import { lazy, Suspense, useMemo, useState, useEffect, useCallback } from "react";
import { useLiveBusLocations, useLiveDepartures, useLiveUpdates } from "../hooks/useTransportData";

const MapViewMap = lazy(() => import("../components/map/MapViewMap"));


// Mock data for instant display
const MOCK_MARKERS = [
{ id: 1, position: [54.050556, -2.800556], name: "Lancaster Bus Station", type: "bus", status: "On time" },
{ id: 2, position: [54.048889, -2.802500], name: "Lancaster Train Station", type: "train", status: "On time" },
{ id: 3, position: [54.064560, -2.798890], name: "Lancaster City Center Stop", type: "bus", status: "On time" },
{ id: 4, position: [54.045000, -2.810000], name: "Greyhound Bus Park", type: "bus", status: "Delayed 2 mins" },
{ id: 5, position: [53.995000, -2.700000], name: "Morecambe Station", type: "train", status: "On time" },
{ id: 6, position: [53.990000, -2.750000], name: "Morecambe Bus Station", type: "bus", status: "On time" },
{ id: 7, position: [53.760000, -2.700000], name: "Preston Bus Station", type: "bus", status: "On time" },
{ id: 8, position: [53.750000, -2.680000], name: "Preston Train Station", type: "train", status: "Delayed 5 mins" },
{ id: 9, position: [54.080000, -2.700000], name: "Carnforth Station", type: "train", status: "On time" },
{ id: 10, position: [54.120000, -2.650000], name: "Kendal Bus Station", type: "bus", status: "On time" },
];

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

// Fetch real data from API using the current map center (debounced inside the hook)
const { data: busLocations, loading: busLoading, error: busError } = useLiveBusLocations('SCCU', {
lat: mapCenter.lat,
lon: mapCenter.lon,
refreshInterval: 30000,
debounceMs: 800,
});
const { data: trainDepartures, loading: trainLoading, error: trainError } = useLiveDepartures('LAN', 30000);
const { data: liveBusUpdate, isConnected: busLiveConnected } = useLiveUpdates('bus');
const { data: liveTrainUpdate, isConnected: trainLiveConnected } = useLiveUpdates('train');

// Update markers when real API data arrives
useEffect(() => {
console.log('Bus API Response:', { busLocations, busError });
console.log('Train API Response:', { trainDepartures, trainError });

// Only update if we have real data from the API
if ((Array.isArray(busLocations) && busLocations.length > 0) || 
    (Array.isArray(trainDepartures) && trainDepartures.length > 0)) {

console.log('Received real data from API, updating markers...');
const newMarkers = [];
let id = 1;

// Add bus locations
if (Array.isArray(busLocations) && busLocations.length > 0) {
busLocations.forEach(bus => {
newMarkers.push({
id: bus.vehicle_ref || id++,
position: [bus.latitude || bus.lat, bus.longitude || bus.lon],
name: bus.name || `Bus ${bus.line || bus.id || ''}`.trim(),
type: 'bus',
status: bus.status || 'On time',
routeNumber: bus.line || bus.routeNumber || bus.route,
destination: bus.destination,
operator: bus.operator,
delayMinutes: bus.delay_minutes,
bearing: bus.bearing,
direction: bus.direction,
originName: bus.origin_name,
journeyRef: bus.journey_ref,
aimedDepartureTime: bus.aimed_departure_time,
vehicleRef: bus.vehicle_ref,
});
});
}

// Add train departures
if (Array.isArray(trainDepartures) && trainDepartures.length > 0) {
trainDepartures.forEach(train => {
newMarkers.push({
id: id++,
position: [train.latitude || train.lat, train.longitude || train.lon],
name: train.station || train.name || 'Train Station',
type: 'train',
status: train.status || train.delayMinutes ? `Delayed ${train.delayMinutes} mins` : 'On time',
destination: train.destination,
departureTime: train.departureTime || train.scheduledTime
});
});
}

setMarkers(newMarkers);
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

const nearestStop = useMemo(() => {
if (!userLocation || !markers.length) return null;
let nearest = null;
for (const marker of markers) {
const distance = distanceMeters(userLocation, marker.position);
if (!nearest || distance < nearest.distance) {
nearest = { ...marker, distance };
}
}
return nearest;
}, [userLocation, markers, distanceMeters]);

const closestStops = useMemo(() => {
if (!userLocation || !markers.length) return [];
const withDistance = markers.map((marker) => ({
...marker,
distance: distanceMeters(userLocation, marker.position)
}));
return withDistance.sort((a, b) => a.distance - b.distance).slice(0, 3);
}, [userLocation, markers, distanceMeters]);

const formatWalkTime = (distance) => {
const minutes = Math.max(1, Math.round(distance / 84)); // ~1.4 m/s walking speed
return `${minutes} min walk`;
};

const normalizeLiveMarker = (item, type) => {
const lat = item?.latitude ?? item?.lat;
const lon = item?.longitude ?? item?.lon;
if (typeof lat !== 'number' || typeof lon !== 'number') return null;
return {
id: item?.vehicle_ref || item?.vehicleId || item?.id || `${type}-${lat}-${lon}`,
position: [lat, lon],
name: item?.name || item?.label || (type === 'bus' ? `Bus ${item?.line || item?.route || item?.routeNumber || ''}`.trim() : item?.station || 'Train'),
type,
status: item?.status || (item?.delayMinutes ? `Delayed ${item.delayMinutes} mins` : 'On time'),
routeNumber: item?.line || item?.routeNumber || item?.route,
destination: item?.destination,
departureTime: item?.departureTime || item?.scheduledTime,
        // Keep human-friendly operator name for display, but also capture
        // the SIRI operator code when available so we can disambiguate
        // route lookups later (operator_ref is added by the backend).
        operator: item?.operator,
        operatorCode: item?.operator_ref ?? null,
delayMinutes: item?.delay_minutes ?? item?.delayMinutes,
bearing: item?.bearing,
direction: item?.direction,
originName: item?.origin_name ?? item?.originName,
journeyRef: item?.journey_ref ?? item?.journeyRef,
aimedDepartureTime: item?.aimed_departure_time ?? item?.aimedDepartureTime,
vehicleRef: item?.vehicle_ref ?? item?.vehicleRef,
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
<Paper elevation={0} sx={{ 
p: 3.5, 
background: 'linear-gradient(135deg, #6366F1 0%, #EC4899 100%)',
color: 'white',
borderRadius: '16px'
}}>
    <Stack direction="row" spacing={1.5} alignItems="center">
    {/* Map header - icon removed */}
    </Stack>
<Typography variant="body2" sx={{ color: 'rgba(255,255,255,0.95)', mt: 1.5, fontWeight: 500 }}>
Real-time bus and train locations across Lancashire
</Typography>
</Paper>

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
<Box
onClick={() => setFilters(f => ({ ...f, showBuses: !f.showBuses }))}
sx={{
padding: '12px 20px',
border: `2px solid ${filters.showBuses ? '#6366F1' : '#E2E8F0'}`,
borderRadius: '10px',
display: 'flex',
alignItems: 'center',
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
<Stack direction="row" spacing={1.5} alignItems="center" mb={2} flexWrap="wrap">
<Chip
label={`Nearest: ${nearestStop.name}`}
variant="outlined"
sx={{ borderRadius: '10px' }}
/
>
<Chip
label={`${(nearestStop.distance / 1000).toFixed(1)} km away`}
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
    liveBusOperator={"SCCU"}
/>
</Suspense>
</Paper>
</Stack>
)
}
