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
import BusStopLayer from "./BusStopLayer";

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

/**
 * Creates a custom Leaflet divIcon for a bus or train marker.
 * @param {'bus'|'train'} type
 * @param {string} color  - hex/named CSS colour
 * @param {string|null} [label] - for bus markers, the route/line number to display on the icon
 */
const createCustomIcon = (type, color, label = null) => {
	let innerSvg;
	if (type === 'bus' && label) {
		// Show route number prominently inside the circle
		const text = String(label).substring(0, 4); // cap at 4 chars
		const fontSize = text.length >= 4 ? 9 : text.length === 3 ? 11 : 13;
		innerSvg = `<text x="20" y="25" text-anchor="middle" font-family="Arial,sans-serif" font-size="${fontSize}" font-weight="bold" fill="${color}">${text}</text>`;
	} else if (type === 'bus') {
		innerSvg = '<path d="M12 14h16v8H12z" fill="none" stroke="' + color + '" stroke-width="2" stroke-linecap="round"/><circle cx="16" cy="24" r="2" fill="' + color + '"/><circle cx="24" cy="24" r="2" fill="' + color + '"/>';
	} else {
		innerSvg = '<path d="M20 12l-6 4v8h12v-8z" fill="none" stroke="' + color + '" stroke-width="2"/><line x1="14" y1="24" x2="26" y2="24" stroke="' + color + '" stroke-width="2"/>';
	}

	const svg = `<svg width="40" height="40" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
		<circle cx="20" cy="20" r="18" fill="white" stroke="${color}" stroke-width="3"/>
		${innerSvg}
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

/**
 * Returns the colour to use for a bus marker icon based on delay.
 * @param {number|null} delayMinutes
 */
const busIconColor = (delayMinutes) => {
	if (delayMinutes == null) return '#1976d2';      // unknown → blue
	if (delayMinutes >= 10) return '#d32f2f';        // very late → red
	if (delayMinutes >= 2) return '#f57c00';         // delayed → orange
	if (delayMinutes <= -1) return '#7b1fa2';        // early → purple
	return '#1976d2';                                // on time → blue
};

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
	/** seconds until next bus data refresh */
	busCountdown = 30,
	/** total refresh period in ms — used to compute ring percentage */
	busRefreshInterval = 30000,
	/** true while a background re-fetch is in-flight */
	busRefreshing = false,
	onMapReady,
	onMoveEnd
}) {
	const countdownTotal = Math.max(1, Math.round(busRefreshInterval / 1000));
	const ringValue = Math.round((busCountdown / countdownTotal) * 100);

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
				{/* Full overlay only on the very first load — not on every 30-second refresh */}
				{(busLoading || trainLoading) && (
					<Box sx={{
						position: 'absolute',
						inset: 0,
						zIndex: 1000,
						backgroundColor: 'rgba(255,255,255,0.72)',
						display: 'flex',
						alignItems: 'center',
						justifyContent: 'center'
					}}>
						<Stack spacing={1} alignItems="center">
							<CircularProgress size={32} />
							<Typography variant="caption" fontWeight={600}>Loading live locations…</Typography>
						</Stack>
					</Box>
				)}

				{/* Countdown ring — always visible after initial load to show next refresh */}
				{!busLoading && (
					<Box sx={{
						position: 'absolute',
						top: 12,
						right: 12,
						zIndex: 1001,
						display: 'flex',
						flexDirection: 'column',
						alignItems: 'center',
						gap: '4px',
					}}>
						{/* Circular progress ring */}
						<Box sx={{ position: 'relative', display: 'inline-flex' }}>
							{/* Grey background track */}
							<CircularProgress
								variant="determinate"
								value={100}
								size={52}
								thickness={3.5}
								sx={{ color: 'rgba(0,0,0,0.1)', position: 'absolute', top: 0, left: 0 }}
							/>
							{/* Countdown ring — spins when refreshing, counts down otherwise */}
							<CircularProgress
								variant={busRefreshing ? 'indeterminate' : 'determinate'}
								value={busRefreshing ? undefined : ringValue}
								size={52}
								thickness={3.5}
								sx={{ color: busRefreshing ? '#6366F1' : '#10B981', transition: 'color 0.3s' }}
							/>
							{/* Centre label */}
							<Box sx={{
								position: 'absolute', inset: 0,
								display: 'flex', alignItems: 'center', justifyContent: 'center',
								backgroundColor: 'white', borderRadius: '50%',
								margin: '4px',
							}}>
								<Typography
									variant="caption"
									fontWeight={700}
									sx={{ fontSize: '11px', lineHeight: 1, color: busRefreshing ? '#6366F1' : '#374151' }}
								>
									{busRefreshing ? '↻' : `${busCountdown}s`}
								</Typography>
							</Box>
						</Box>
						{/* Label pill below the ring */}
						<Box sx={{
							backgroundColor: 'rgba(255,255,255,0.92)',
							borderRadius: '8px',
							px: 0.8, py: 0.3,
							boxShadow: '0 1px 4px rgba(0,0,0,0.12)',
						}}>
							<Typography
								variant="caption"
								fontWeight={600}
								sx={{ fontSize: '10px', color: busRefreshing ? '#6366F1' : '#374151', whiteSpace: 'nowrap' }}
							>
								{busRefreshing ? 'Updating…' : 'Next update'}
							</Typography>
						</Box>
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
					{/* Bus stop markers — small circles visible at zoom ≥ 13 */}
					<BusStopLayer />
					{filteredMarkers.map((marker) => (
						<Marker
							key={marker.id}
							position={marker.position}
							icon={marker.type === 'bus'
							? createCustomIcon('bus', busIconColor(marker.delayMinutes), marker.routeNumber != null ? String(marker.routeNumber) : null)
								: TRAIN_ICON}
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
										<Typography variant="caption" display="block" color="text.secondary" sx={{ mb: 0.5 }}>
											{marker.type === 'bus' ? '\U0001f68c Bus' : '\U0001f682 Train'}
										</Typography>
										{marker.type === 'bus' && marker.routeNumber != null && (
											<Box sx={{
												display: 'inline-flex',
												alignItems: 'center',
												gap: '4px',
												padding: '2px 10px',
												borderRadius: '10px',
												backgroundColor: busIconColor(marker.delayMinutes),
												color: 'white',
												fontSize: '13px',
												fontWeight: '700',
												mb: 1,
											}}>
												Route {String(marker.routeNumber)}
											</Box>
										)}
										{(() => {
											const dm = marker.delayMinutes;
											const isOnTime = dm == null || (dm > -1 && dm < 2);
											const isEarly = dm != null && dm <= -1;
											const bgColor = isOnTime ? '#e8f5e9' : isEarly ? '#f3e5f5' : (dm >= 10 ? '#ffebee' : '#fff3e0');
											const txtColor = isOnTime ? '#2e7d32' : isEarly ? '#6a1b9a' : (dm >= 10 ? '#c62828' : '#e65100');
											const icon = isOnTime ? '\u2713' : isEarly ? '\u23eb' : '\u26a0';
											return (
												<Box sx={{
													display: 'inline-block',
													padding: '4px 12px',
													borderRadius: '12px',
													backgroundColor: bgColor,
													color: txtColor,
													fontSize: '12px',
													fontWeight: '600',
													marginBottom: '8px'
												}}>
													{icon} {marker.status}
												</Box>
											);
										})()}
										{/* Render operator prominently (if available) and then backend-provided meta fields (exclude coords and operator keys) */}
										{marker.operator && (
											// force a full-width break before operator so it is always on its own line
											<Box sx={{ width: '100%', mt: 1 }}>
												<Typography variant="body2" sx={{ display: 'block', mb: 0.5 }}>
													<strong>Operator</strong>: {String(marker.operator)}
												</Typography>
											</Box>
										)}
										{marker.meta && Object.keys(marker.meta).length > 0 && (
											<Box sx={{ mt: 1 }}>
												{Object.entries(marker.meta)
													.filter(([k]) => {
														const kk = String(k).toLowerCase();
														return !['lat', 'lon', 'latitude', 'longitude', 'operator', 'operator_name', 'operatorref', 'operator_ref', 'operatorname', 'delay_minutes', 'delayminutes', 'status'].includes(kk);
													})
													.map(([key, value]) => (
														<Typography key={key} variant="body2" sx={{ mb: 0.5 }}>
															<strong>{key.replace(/_/g, ' ')}:</strong> {String(value)}
														</Typography>
													))}
											</Box>
										)}
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
