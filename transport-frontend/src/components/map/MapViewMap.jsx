import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import { MapContainer, Marker, Popup, TileLayer, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import icon from 'leaflet/dist/images/marker-icon.png';
import iconShadow from 'leaflet/dist/images/marker-shadow.png';
import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useCallback } from "react";
// Allow a sideContent prop to be injected by the parent (e.g. Suggested routes)
import BusStopLayer from "./BusStopLayer";
import RouteLineLayer from "./RouteLineLayer";
import { useRouteLine } from "../../hooks/useRouteLine";
import { resolveOperatorCode } from '../../services/operatorMap';
import { stopsToLatLngs } from '../../services/routeLineApi';

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
		 onMoveEnd,
		 sideContent,
		 showSideOverlay = true,
		// The operator code used to fetch live bus locations (e.g. 'SCCU').
		// Used as a fallback when a marker lacks an explicit operator field.
		liveBusOperator = null,
}) {
	const countdownTotal = Math.max(1, Math.round(busRefreshInterval / 1000));
	const ringValue = Math.round((busCountdown / countdownTotal) * 100);

	const { activeRoutes, toggleRoute, isActive } = useRouteLine();
	// Keep a reference to the Leaflet map instance so we can call fitBounds
	const mapRef = useRef(null);

	// Wrap any external onMapReady passed in so we capture the map instance
	const _onMapReady = useCallback((map) => {
		mapRef.current = map;
		if (typeof onMapReady === 'function') onMapReady(map);
	}, [onMapReady]);

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
					<MapController onReady={_onMapReady} onMoveEnd={onMoveEnd} />
					<TileLayer
						attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
						url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
					/>
					{/* Bus stop markers — small circles visible at zoom ≥ 13 */}
					<BusStopLayer onToggleRoute={toggleRoute} isRouteActive={isActive} />
					<RouteLineLayer activeRoutes={activeRoutes} />
					{filteredMarkers.map((marker) => (
						<Marker
							key={marker.id}
							position={marker.position}
							icon={marker.type === 'bus'
							? createCustomIcon('bus', busIconColor(marker.delayMinutes), marker.routeNumber != null ? String(marker.routeNumber) : null)
								: TRAIN_ICON}
							eventHandlers={{
								click: async () => {
									// Toggle route display when clicking a bus marker
									if (marker.type === 'bus' && marker.routeNumber != null) {
										try {
											// Prefer the operator code when available; otherwise attempt to
											// resolve a human-friendly operator name to a SIRI code.  As a
											// last resort fall back to the page-level liveBusOperator.
											let usedOp = marker.operatorCode || null;
											if (!usedOp && marker.operator) {
												usedOp = resolveOperatorCode(marker.operator) || null;
											}
											if (!usedOp) usedOp = liveBusOperator || null;
											// Debug: record which operator we use when toggling a route
											try { console.info(`[MapViewMap] toggleRoute line=${String(marker.routeNumber)} operator=${usedOp} markerId=${marker.id}`); } catch (e) { /* ignore */ }

											// Determine a preferred last-stop/destination from the
											// live marker (if present) so we can prefer variants
											// that terminate where the vehicle is heading.
											// Prefer a destination ATCO/code if present in the live
											// marker (e.g. destination_ref). Fall back to human
											// readable destination name if necessary.
											const preferredAtco = marker.destination_ref || marker.destinationRef || marker.meta?.destination_ref || marker.meta?.destinationRef || marker.meta?.destination_atco || null;
											const preferredName = marker.destination || marker.destination_name || marker.meta?.destination_name || marker.meta?.destination || null;

											const preferredDest = preferredAtco || preferredName || null;

											// If the backend included a server-side matched
											// route identifier, prefer it — passing it into
											// toggleRoute causes the hook to request the
											// exact variant (backend treats full ids as
											// exact lookups).  We still pass preferredLastStop
											// for additional filtering when useful.
											const matchedRouteId = marker.meta?.matched_route_id || marker.matched_route_id || null;

											let routeData = null;
											try {
												routeData = await toggleRoute(String(marker.routeNumber), usedOp, { preferredLastStop: preferredDest, matchedRouteId });
											} catch (e) {
												// swallow - toggleRoute already logs failures
											}

											// If we received route data (i.e. toggled ON), compute bounds and fit map
											if (routeData && mapRef.current) {
												try {
													const allPositions = [];
													if (Array.isArray(routeData.variants)) {
														for (const variant of routeData.variants) {
															const pos = variant.geometry ? variant.geometry : stopsToLatLngs(variant.stops);
															if (Array.isArray(pos) && pos.length > 0) {
																// geometry may be [[lat, lon], ...] or stopsToLatLngs returns same
																for (const p of pos) {
																	// ensure [lat, lon]
																	if (Array.isArray(p) && p.length >= 2) allPositions.push([p[0], p[1]]);
																}
															}
														}
													}
													if (allPositions.length > 0) {
														const bounds = L.latLngBounds(allPositions);
														// Use a small padding so the route is comfortably visible
														mapRef.current.fitBounds(bounds, { padding: [64, 64] });
													}
												} catch (e) {
													// ignore fitBounds failures
												}
											}
										} catch (e) {
											// ignore
										}
									}
									onOpenPopup(marker.id);
								}
							}}
						>
							{openPopupId === marker.id && (
								<Popup
									onClose={() => {
										// Close popup as before
										onClosePopup();
										// When closing a bus popup, hide the route if it's active
										if (marker.type === 'bus' && marker.routeNumber != null) {
											try {
												// Only toggle off if currently active
												if (isActive(String(marker.routeNumber))) toggleRoute(String(marker.routeNumber), marker.operatorCode || marker.operator || liveBusOperator || null);
											} catch (e) {
												// ignore
											}
										}
									}}
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
												Line {String(marker.routeNumber)}
											</Box>
										)}
										{(() => {
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
										})()}
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
															// Exclude coordinate, operator and common live-feed fields
															// including recent additions like vehicle_ref, delay_seconds,
															// bearing, direction, origin/destination refs/names,
															// journey_ref and aimed_departure_time.
															return ![
																'lat', 'lon', 'latitude', 'longitude',
																'operator', 'operator_name', 'operatorref', 'operator_ref', 'operatorname',
																'status',
																// delay variants
																'delay_minutes', 'delayminutes', 'delay_seconds', 'delayseconds',
																// vehicle / telemetry
																'vehicle_ref', 'vehicleref', 'bearing', 'direction',
																// origin / destination identifiers and names
																'origin_ref', 'originref', 'origin_name', 'originname',
																'destination_ref', 'destinationref', 'destination_name', 'destinationname',
																// journey / timing
																'journey_ref', 'journeyref', 'aimed_departure_time', 'aimed_departure'
															].includes(kk);
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
