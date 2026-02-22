import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import { MapContainer, Marker, Popup, TileLayer, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import icon from 'leaflet/dist/images/marker-icon.png';
import iconShadow from 'leaflet/dist/images/marker-shadow.png';
import "leaflet/dist/leaflet.css";
import { useEffect } from "react";
import WeatherWidget from "../common/WeatherWidget";

// Fix Leaflet marker icons issue with Vite
let DefaultIcon = L.icon({
iconUrl: icon,
shadowUrl: iconShadow,
iconSize: [25, 41],
iconAnchor: [12, 41],
shadowSize: [41, 41],
shadowAnchor: [12, 41]
});

L.Marker.prototype.options.icon = DefaultIcon;

const createCustomIcon = (type, color) => {
const svg = `<svg width="40" height="40" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
<circle cx="20" cy="20" r="18" fill="white" stroke="${color}" stroke-width="3"/>
${type === 'bus' 
? '<path d="M12 14h16v8H12z" fill="none" stroke="' + color + '" stroke-width="2" stroke-linecap="round"/><circle cx="16" cy="24" r="2" fill="' + color + '"/><circle cx="24" cy="24" r="2" fill="' + color + '"/>' 
: '<path d="M20 12l-6 4v8h12v-8z" fill="none" stroke="' + color + '" stroke-width="2"/><line x1="14" y1="24" x2="26" y2="24" stroke="' + color + '" stroke-width="2"/>'}
</svg>`;

return L.divIcon({
html: svg,
className: 'custom-marker-icon',
iconSize: [40, 40],
iconAnchor: [20, 20],
popupAnchor: [0, -20]
});
};

const createUserIcon = () => {
const svg = `<svg width="36" height="36" viewBox="0 0 36 36" xmlns="http://www.w3.org/2000/svg">
<circle cx="18" cy="18" r="16" fill="white" stroke="#F59E0B" stroke-width="3"/>
<circle cx="18" cy="18" r="6" fill="#F59E0B"/>
</svg>`;

return L.divIcon({
html: svg,
className: 'custom-user-marker-icon',
iconSize: [36, 36],
iconAnchor: [18, 18],
popupAnchor: [0, -18]
});
};

const BUS_ICON = createCustomIcon('bus', '#1976d2');
const TRAIN_ICON = createCustomIcon('train', '#2e7d32');
const USER_ICON = createUserIcon();

/**
 * Internal map controller that fires onReady and onMoveEnd callbacks.
 * onMoveEnd is called with { lat, lon } whenever the user finishes
 * panning/zooming (Leaflet "moveend" event).
 */
const MapController = ({ onReady, onMoveEnd }) => {
const map = useMap();

useMapEvents({
moveend: () => {
if (onMoveEnd) {
const center = map.getCenter();
onMoveEnd({ lat: center.lat, lon: center.lng });
}
},
});

useEffect(() => {
onReady(map);
// Fire initial center on mount so the hook receives coordinates immediately
if (onMoveEnd) {
const center = map.getCenter();
onMoveEnd({ lat: center.lat, lon: center.lng });
}
}, [map, onReady, onMoveEnd]);

return null;
};

export default function MapViewMap({
filteredMarkers,
openPopupId,
onOpenPopup,
onClosePopup,
userLocation,
nearestStop,
busLoading,
trainLoading,
onMapReady,
onMoveEnd
}) {
return (
<Stack direction={{ xs: "column", md: "row" }} spacing={3} sx={{ height: { xs: 'auto', md: 750 } }}>
<Box sx={{ 
flex: 1,
position: 'relative',
borderRadius: '12px',
overflow: 'hidden',
boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
border: '1px solid',
borderColor: 'divider',
height: { xs: 420, sm: 520, md: '100%' }
}}>
{(busLoading || trainLoading) && (
<Box sx={{
position: 'absolute',
inset: 0,
zIndex: 1000,
backgroundColor: 'rgba(255,255,255,0.7)',
display: 'flex',
alignItems: 'center',
justifyContent: 'center'
}}>
<Stack spacing={1} alignItems="center">
<CircularProgress size={28} />
<Typography variant="caption" fontWeight={600}>Loading live locations</Typography>
</Stack>
</Box>
)}
{!busLoading && !trainLoading && filteredMarkers.length === 0 && (
<Box sx={{
position: 'absolute',
inset: 0,
zIndex: 1000,
backgroundColor: 'rgba(255,255,255,0.7)',
display: 'flex',
alignItems: 'center',
justifyContent: 'center'
}}>
<Typography variant="body2" fontWeight={600} color="text.secondary">
No vehicles found with current filters.
</Typography>
</Box>
)}
<MapContainer 
center={[54.050556, -2.800556]} 
zoom={10} 
scrollWheelZoom 
style={{ height: "100%", width: "100%" }}
className="leaflet-container-custom"
>
<MapController onReady={onMapReady} onMoveEnd={onMoveEnd} />
<TileLayer
attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
/>
{filteredMarkers.map((marker) => (
<Marker 
key={marker.id} 
position={marker.position}
icon={marker.type === 'bus' ? BUS_ICON : TRAIN_ICON}
eventHandlers={{
click: () => onOpenPopup(marker.id)
}}
>
{openPopupId === marker.id && (
<Popup 
onClose={onClosePopup}
autoClose={false}
>
<Box sx={{ minWidth: '200px', pb: 1 }}>
<Typography variant="subtitle2" fontWeight={700} sx={{ mb: 0.5 }}>
{marker.name}
</Typography>
<Typography variant="caption" display="block" color="text.secondary" sx={{ mb: 1 }}>
{marker.type === 'bus' ? '\U0001f68c Bus Station' : '\U0001f682 Train Station'}
</Typography>
<Box sx={{
display: 'inline-block',
padding: '4px 12px',
borderRadius: '12px',
backgroundColor: marker.status === 'On time' ? '#e8f5e9' : '#ffebee',
color: marker.status === 'On time' ? '#2e7d32' : '#c62828',
fontSize: '12px',
fontWeight: '600',
marginBottom: '8px'
}}>
{marker.status === 'On time' ? '\u2713' : '\u26a0'} {marker.status}
</Box>
</Box>
</Popup>
)}
</Marker>
))}

{userLocation && (
<Marker
position={userLocation}
icon={USER_ICON}
>
<Popup>
<Typography variant="subtitle2" fontWeight={700}>Your location</Typography>
{nearestStop && (
<Typography variant="caption" display="block" color="text.secondary">
Nearest stop: {nearestStop.name} ({(nearestStop.distance / 1000).toFixed(1)} km)
</Typography>
)}
</Popup>
</Marker>
)}
</MapContainer>
</Box>

<Box sx={{ minWidth: { xs: '100%', md: 320 } }}>
<WeatherWidget />
</Box>
</Stack>
);
}
