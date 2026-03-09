import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import { MapContainer, Marker, Popup, Polyline, CircleMarker, TileLayer, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import icon from 'leaflet/dist/images/marker-icon.png';
import iconShadow from 'leaflet/dist/images/marker-shadow.png';
import "leaflet/dist/leaflet.css";
import { useEffect } from "react";
// Allow a sideContent prop to be injected by the parent (e.g. Suggested routes)
import BusStopLayer from "./BusStopLayer";
import RouteLineLayer from "./RouteLineLayer";
import { useRouteLine } from "../../hooks/useRouteLine";
import Grid from "@mui/material/Grid";
import { useAccessibility } from "../../contexts/AccessibilityContext";

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
	const size = 28;
	const c = size / 2; // 14
	const r = c - 1.5; // 12.5

	let innerSvg;
	let bgFill, strokeColor, strokeWidth;

	if (type === 'bus' && label) {
		// Solid coloured badge with white route number — easy to read at a glance
		bgFill = color;
		strokeColor = 'white';
		strokeWidth = 1.5;
		const text = String(label).substring(0, 4);
		const fontSize = text.length >= 4 ? 7 : text.length === 3 ? 8.5 : 10;
		innerSvg = `<text x="${c}" y="${c}" text-anchor="middle" dominant-baseline="middle" font-family="Arial,sans-serif" font-size="${fontSize}" font-weight="bold" fill="white">${text}</text>`;
	} else if (type === 'bus') {
		bgFill = 'white';
		strokeColor = color;
		strokeWidth = 2;
		innerSvg = `<path d="M8 10h12v5H8z" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round"/>` +
			`<circle cx="10.5" cy="17" r="1.5" fill="${color}"/>` +
			`<circle cx="17.5" cy="17" r="1.5" fill="${color}"/>`;
	} else {
		bgFill = 'white';
		strokeColor = color;
		strokeWidth = 2;
		innerSvg = `<path d="M14 8l-5 3v7h10v-7z" fill="none" stroke="${color}" stroke-width="1.5"/>` +
			`<line x1="9" y1="18" x2="19" y2="18" stroke="${color}" stroke-width="1.5"/>`;
	}

	const svg = `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg">
		<circle cx="${c}" cy="${c}" r="${r}" fill="${bgFill}" stroke="${strokeColor}" stroke-width="${strokeWidth}"/>
		${innerSvg}
	</svg>`;

	return L.divIcon({
		html: svg,
		className: 'custom-marker-icon',
		iconSize: [size, size],
		iconAnchor: [c, c],
		popupAnchor: [0, -c]
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
	// Treat negative delays (early) visually the same as on-time.
	if (delayMinutes == null) return '#1976d2';      // unknown → blue
	if (delayMinutes >= 10) return '#d32f2f';        // very late → red
	if (delayMinutes >= 2) return '#f57c00';         // delayed → orange
	return '#1976d2';                                // on time / early → blue
};

const TRAIN_ICON = createCustomIcon('train', '#2e7d32');
const USER_ICON = createUserIcon();

/**
 * Internal map controller that fires onReady and onMoveEnd callbacks.
 * onMoveEnd is called with { lat, lon } whenever the user finishes
 * panning/zooming (Leaflet "moveend" event).
 */
/** Clears all active route overlays when the user clicks the map background. */
const MapClickClearHandler = ({ onClear }) => {
	useMapEvents({ click: () => onClear() });
	return null;
};

/**
 * Renders journey-plan route geometry on the map and fits bounds to show
 * the full journey.  Each segment in `segments` has:
 *   { id, name, coords: [[lat,lon],...], color }
 */
const JourneyRouteLayer = ({ segments }) => {
	const map = useMap();

	useEffect(() => {
		if (!segments || segments.length === 0) return;
		const allCoords = segments.flatMap((s) => s.coords || []);
		if (allCoords.length < 2) return;
		try {
			map.fitBounds(allCoords, { padding: [40, 40], maxZoom: 15 });
		} catch (e) {
			// ignore if map not ready
		}
	}, [map, segments]);

	if (!segments || segments.length === 0) return null;

	return (
		<>
			{segments.map((seg) => {
				if (!seg.coords || seg.coords.length < 2) return null;
				const isWalk = seg.color === '#888888' || (seg.id && seg.id.startsWith('walk'));
				return (
					<Polyline
						key={seg.id}
						positions={seg.coords}
						pathOptions={{
							color: seg.color || '#1a73e8',
							weight: isWalk ? 3 : 5,
							opacity: isWalk ? 0.6 : 0.85,
							dashArray: isWalk ? '6 8' : undefined,
						}}
					/>
				);
			})}
			{/* Origin dot */}
			{segments[0]?.coords?.[0] && (
				<CircleMarker
					center={segments[0].coords[0]}
					radius={7}
					pathOptions={{ color: '#fff', weight: 2, fillColor: '#10B981', fillOpacity: 1 }}
				/>
			)}
			{/* Destination dot */}
			{(() => {
				const last = segments[segments.length - 1];
				const pt = last?.coords?.[last.coords.length - 1];
				if (!pt) return null;
				return (
					<CircleMarker
						center={pt}
						radius={7}
						pathOptions={{ color: '#fff', weight: 2, fillColor: '#d32f2f', fillOpacity: 1 }}
					/>
				);
			})()}
		</>
	);
};

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

// Build the contents of the train station markers on the map
const makeTrainMarker = marker => {
	return <>{marker.services.map(service => {
		const isOnTime = service.status === 'On time';
		const dm = service.delayMins ?? 10;
		const bgColor = isOnTime ? '#e8f5e9' : (dm >= 10 ? '#ffebee' : '#fff3e0');
		const txtColor = isOnTime ? '#2e7d32' : (dm >= 10 ? '#c62828' : '#e65100');
		const icon = isOnTime ? '\u2713' : '\u26a0';

		let status = service.status;
		if(status.startsWith("Delayed ")) {
			status = status.replace("Delayed ", "");
		}

		return <Grid container justifyContent="space-between">
			{service.destination}
			<Box sx={{
				display:'inline-block',
				padding: '4px 12px',
				borderRadius: '12px',
				backgroundColor: bgColor,
				color: txtColor,
				fontSize: '12px',
				fontWeight: '600',
				marginBottom: '8px'
			}}>
				{icon} {status}
			</Box>
		</Grid>
	})}</>
}

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
 		onMoveEnd,
 		sideContent,
 		showSideOverlay = true,
	/** Route geometry from journey planner. Array of {id, name, coords, color} */
	journeyRoute = null,
}) {
	const countdownTotal = Math.max(1, Math.round(busRefreshInterval / 1000));
	const ringValue = Math.round((busCountdown / countdownTotal) * 100);

	const { activeRoutes, toggleRoute, isActive, clearRoutes } = useRouteLine();
	const { highContrast } = useAccessibility();

	// High-contrast mode → CartoDB Positron (clean, light, high-legibility labels)
	// Normal mode        → standard OpenStreetMap
	const tileUrl = highContrast
		? 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png'
		: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
	const tileAttribution = highContrast
		? '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
		: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

	return (
	<Stack direction={{ xs: "column", md: "row" }} spacing={3} sx={{ height: { xs: 'auto', md: 700 } }}>
			<Box sx={{
				flex: 2,
				position: 'relative',
				borderRadius: '12px',
				overflow: 'hidden',
				boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
				border: '1px solid',
				borderColor: 'divider',
				height: { xs: 320, sm: 420, md: '100%' }
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
						// Disable scroll-wheel / trackpad two-finger slide zoom but allow pinch-to-zoom on touch devices
						// (scrollWheelZoom handles mouse wheel and trackpad two-finger scroll; touchZoom enables pinch)
							scrollWheelZoom={false}
							touchZoom={true}
						style={{ height: "100%", width: "100%" }}
						className="leaflet-container-custom"
					>
					<MapController onReady={onMapReady} onMoveEnd={onMoveEnd} />				<MapClickClearHandler onClear={clearRoutes} />					<TileLayer
						attribution={tileAttribution}
						url={tileUrl}
					/>				{/* Clear-routes button — floated bottom-left, only when routes are active */}
				{activeRoutes.size > 0 && (
					<Box
						component="button"
						onClick={(e) => { e.stopPropagation(); clearRoutes(); }}
						className="leaflet-control"
						style={{
							position: 'absolute',
							bottom: 28,
							left: 12,
							zIndex: 1001,
							display: 'flex',
							alignItems: 'center',
							gap: '6px',
							padding: '6px 14px',
							borderRadius: '20px',
							border: '1.5px solid #d32f2f',
							backgroundColor: 'white',
							color: '#d32f2f',
							fontSize: '13px',
							fontWeight: 700,
							cursor: 'pointer',
							boxShadow: '0 2px 6px rgba(0,0,0,0.15)',
						}}
					>
						✕ Clear routes ({activeRoutes.size})
					</Box>
				)}					{/* Bus stop markers — small circles visible at zoom ≥ 13 */}
					<BusStopLayer onToggleRoute={toggleRoute} isRouteActive={isActive} />
					<RouteLineLayer activeRoutes={activeRoutes} />
				{/* Journey-plan route overlay */}
				<JourneyRouteLayer segments={journeyRoute} />
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
											{marker.type === 'bus' ? '\u{01f68c} Bus' : '\u{01f682} Train'}
										</Typography>
										{marker.type === 'train' ? makeTrainMarker(marker) : <></>}
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
												Line {String(marker.routeNumber)}
											</Box>
										)}
										{marker.type !== 'train' ? (() => {
											const dm = marker.delayMinutes;
											// Treat early (dm < 0) the same as on-time for visuals
											const isOnTime = dm == null || dm < 2;
											const isDelayed = dm != null && dm >= 2;
											const bgColor = isOnTime ? '#e8f5e9' : (dm >= 10 ? '#ffebee' : '#fff3e0');
											const txtColor = isOnTime ? '#2e7d32' : (dm >= 10 ? '#c62828' : '#e65100');
											const icon = isOnTime ? '\u2713' : '\u26a0';

											// Avoid repeating numeric delay in the pill when
											// we already display the precise value below.
											// If the backend returned an early status, normalize
											// the displayed status to 'On time' so early buses
											// look identical to on-time ones.
											let statusText = marker.status || '';
											if (isOnTime) {
												statusText = 'On time';
											} else if (marker.delayMinutes != null) {
												// Show the delay with rounded minutes after the base status.
												// If the backend provided a status like 'Delayed 5 min',
												// strip any existing numeric suffix and append our rounded value.
												const base = (statusText || 'Delayed').replace(/\s*\d+(?:\.\d+)?\s*min?s?/i, '').trim() || 'Delayed';
												const mins = Math.round(Math.abs(marker.delayMinutes));
												statusText = `${base} ${mins} min${mins !== 1 ? 's' : ''}`;
											}

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
													{icon} {statusText}
												</Box>
											);
										})() : <></>}
										{/* Numeric delay removed — status pill conveys categorical state */}
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

				{/* Render sideContent as an overlay on top of the map on md+ screens */}
				{showSideOverlay && sideContent && (
					<Box sx={{
						position: 'absolute',
						top: 16,
						right: 16,
						bottom: 16,
						// Keep the suggested routes under the AppBar header
						zIndex: 1050,
						display: { xs: 'none', md: 'block' },
						minWidth: 320,
					}}>
						{sideContent}
					</Box>
				)}
			</Box>

			{/* On small screens, render the sideContent below the map (full width) */}
			{showSideOverlay && sideContent && (
				<Box sx={{ display: { xs: 'block', md: 'none' }, width: '100%' }}>
					{sideContent}
				</Box>
			)}
		</Stack>
	);
}
