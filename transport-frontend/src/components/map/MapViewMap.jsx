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

	// Normalize coords to [[lat, lon], ...] numeric arrays and produce a stable key
	const normalizeSegments = (inSegments) => {
		if (!Array.isArray(inSegments)) return [];
		const out = [];
		for (const s of inSegments) {
			if (!s || !Array.isArray(s.coords)) continue;
			const coords = [];
			for (const pt of s.coords) {
				if (!Array.isArray(pt) || pt.length < 2) continue;
				const a = Number(pt[0]);
				const b = Number(pt[1]);
				if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
				// Detect lon/lat vs lat/lon by latitude range
				if (a < -90 || a > 90) {
					coords.push([b, a]);
				} else {
					coords.push([a, b]);
				}
			}
			if (coords.length < 2) continue;

			// If the segment includes explicit leg endpoints (_from/_to) try to clip
			// the geometry to only the portion between those endpoints so we don't
			// draw the entire vehicle route (common when stored tracks are full-route).
			const clipToLeg = (pts, fromPt, toPt) => {
				if (!Array.isArray(pts) || pts.length < 2 || !fromPt || !toPt) return pts;
				// nearest index by simple lat/lon distance (fast approximation)
				const sqDist = (a, b) => {
					const dlat = a[0] - b[0];
					const dlon = a[1] - b[1];
					return dlat * dlat + dlon * dlon;
				};
				let idxFrom = null;
				let idxTo = null;
				let bestFrom = Infinity;
				let bestTo = Infinity;
				for (let i = 0; i < pts.length; i++) {
					const p = pts[i];
					const dF = sqDist(p, fromPt);
					const dT = sqDist(p, toPt);
					if (dF < bestFrom) { bestFrom = dF; idxFrom = i; }
					if (dT < bestTo) { bestTo = dT; idxTo = i; }
				}
				if (idxFrom == null || idxTo == null) return pts;
				if (idxFrom <= idxTo) return pts.slice(idxFrom, idxTo + 1);
				return pts.slice(idxTo, idxFrom + 1);
			};

			// If provided, _from/_to are in [lat, lon] format
			if (s._from && s._to) {
				const clipped = clipToLeg(coords, s._from, s._to);
				if (Array.isArray(clipped) && clipped.length >= 2) {
					// replace coords with clipped segment
					coords.length = 0;
					coords.push(...clipped);
				}
			}
			const key = `${s.id || 'seg'}-${coords.length}`;
			out.push({ ...s, coords, _normKey: key });
		}
		return out;
	};

	const normSegments = normalizeSegments(segments);

	// Densify sparse coords to make polylines appear smoother when OSRM smoothing
	// isn't available. Only apply to non-walk segments to keep walk legs dashed
	// and visually lighter.
	const densifyCoords = (coords, pointsPerPair = 2) => {
		if (!Array.isArray(coords) || coords.length < 2) return coords;
		const out = [];
		for (let i = 0; i < coords.length - 1; i++) {
			const [lat1, lon1] = coords[i];
			const [lat2, lon2] = coords[i + 1];
			out.push([lat1, lon1]);
			for (let k = 1; k <= pointsPerPair; k++) {
				const t = k / (pointsPerPair + 1);
				out.push([lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t]);
			}
		}
		// push last
		out.push(coords[coords.length - 1]);
		return out;
	};

	// For very sparse segments (2 points) create a gentle curved arc so the
	// visual overlay looks like a route instead of a straight line.
	const makeCurvedSegment = (a, b, numPoints = 20, curvature = 0.06) => {
		// a, b are [lat, lon]
		const [lat1, lon1] = a;
		const [lat2, lon2] = b;
		// Convert to radians for distance calc
		const toRad = (d) => (d * Math.PI) / 180;
		const R = 6371000; // meters
		const dLat = toRad(lat2 - lat1);
		const dLon = toRad(lon2 - lon1);
		const phi1 = toRad(lat1);
		const phi2 = toRad(lat2);
		const hav = Math.sin(dLat/2)**2 + Math.cos(phi1)*Math.cos(phi2)*Math.sin(dLon/2)**2;
		const dist = 2 * R * Math.atan2(Math.sqrt(hav), Math.sqrt(1-hav));
		// Midpoint
		const midLat = (lat1 + lat2) / 2;
		const midLon = (lon1 + lon2) / 2;
		// Perpendicular vector in degrees (approx)
		const dx = lon2 - lon1;
		const dy = lat2 - lat1;
		// normalize perp
		let px = -dy;
		let py = dx;
		const plen = Math.sqrt(px*px + py*py) || 1;
		px /= plen; py /= plen;
		// offset magnitude (degrees) — convert meters -> degrees roughly by 111320 m per degree latitude
		const offsetMeters = Math.max(30, Math.min(500, dist * curvature));
		const offsetDeg = offsetMeters / 111320;
		const centerLat = midLat + py * offsetDeg;
		const centerLon = midLon + px * offsetDeg;
		// Build quadratic Bezier from a -> center -> b
		const pts = [];
		for (let i = 0; i <= numPoints; i++) {
			const t = i / numPoints;
			// Quadratic Bezier: (1-t)^2 * a + 2(1-t)t*c + t^2 * b
			const lat = (1-t)*(1-t)*lat1 + 2*(1-t)*t*centerLat + t*t*lat2;
			const lon = (1-t)*(1-t)*lon1 + 2*(1-t)*t*centerLon + t*t*lon2;
			pts.push([lat, lon]);
		}
		return pts;
	};

	// Chaikin smoothing (corner-cutting) to make polylines visually smoother.
	const chaikinSmooth = (coords, iterations = 2) => {
		if (!Array.isArray(coords) || coords.length < 3) return coords;
		let pts = coords.slice();
		for (let it = 0; it < iterations; it++) {
			const next = [];
			next.push(pts[0]); // keep first
			for (let i = 0; i < pts.length - 1; i++) {
				const [x0, y0] = pts[i];
				const [x1, y1] = pts[i + 1];
				const Q = [0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1];
				const R = [0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1];
				next.push(Q);
				next.push(R);
			}
			next.push(pts[pts.length - 1]);
			pts = next;
		}
		return pts;
	};

	const displaySegments = normSegments.map((s) => {
		// Consider both backend 'mode' and frontend-normalised 'type' so
		// walking legs are detected regardless of earlier normalisation.
		const isWalk = s.mode === 'walking' || s.type === 'walk' || s.color === '#888888' || (s.id && s.id.startsWith('walk'));
		// If already fairly dense, leave as-is. Otherwise densify non-walk legs and apply Chaikin smoothing.
		if (!isWalk) {
			let coords = s.coords;
			if (coords.length === 2) {
				// produce a curved arc between the two endpoints
				coords = makeCurvedSegment(coords[0], coords[1], 24, 0.06);
				return { ...s, coords };
			}
			if (coords.length < 40) coords = densifyCoords(coords, 3);
			// apply smoothing to make the visual line more continuous
			coords = chaikinSmooth(coords, 2);
			return { ...s, coords };
		}
		return s;
	});

	useEffect(() => {
		// DEV-LOG: inspect the incoming segments prop to verify coords shape (after normalization/densify)
		// eslint-disable-next-line no-console
		console.debug('[DEBUG] JourneyRouteLayer display segments:', Array.isArray(displaySegments) ? displaySegments.map(s => ({ id: s.id, coordsLen: (s.coords || []).length, key: s._normKey, mode: s.mode })) : displaySegments);

		if (!displaySegments || displaySegments.length === 0) return;
		const allCoords = displaySegments.flatMap((s) => s.coords || []);
		if (allCoords.length < 2) return;
		try {
			// Bias the fitted bounds so the route appears on the left ~2/3 of the map
			// Compute a right-side padding equal to ~1/3 of the map width so the
			// route is shifted left when fitted. Use paddingTopLeft for the normal
			// small inset and paddingBottomRight to reserve space on the right.
			const size = map.getSize();
			const rightPad = Math.round((size && size.x) ? size.x * 0.33 : 0);
			map.fitBounds(allCoords, { paddingTopLeft: [40, 40], paddingBottomRight: [rightPad, 40], maxZoom: 15 });
		} catch (e) {
			// ignore if map not ready
		}
		}, [map, JSON.stringify(displaySegments.map(s => s._normKey))]);

	// Ensure walking segments are rendered after vehicle segments so they are visually on top
	// and use a heavier dashed style so they are not visually covered by bus tracks.
	const renderSegments = (Array.isArray(displaySegments) ? displaySegments.slice() : []).sort((a, b) => {
		const isWalkA = (a && (a.mode === 'walking' || a.type === 'walk' || a.color === '#888888' || (a.id && String(a.id).startsWith('walk'))));
		const isWalkB = (b && (b.mode === 'walking' || b.type === 'walk' || b.color === '#888888' || (b.id && String(b.id).startsWith('walk'))));
		if (isWalkA === isWalkB) return 0;
		return isWalkA ? 1 : -1;
	});

		if (!displaySegments || displaySegments.length === 0) return null;

		return (
			<>
				{renderSegments.map((seg) => {
					if (!seg.coords || seg.coords.length < 2) return null;
					const isWalk = seg.mode === 'walking' || seg.color === '#888888' || (seg.id && String(seg.id).startsWith('walk'));
					// Use normalized key to force Leaflet to replace the polyline when coords change
					// For walking segments, use a heavier weight and a bold dashed pattern so they remain visible on top of vehicle tracks.
						return (
							<>
								{/* Cyan underlay/frame so route lines (vehicle and walking) have a cyan outline */}
								<Polyline
									key={`${seg._normKey}-frame`}
									positions={seg.coords}
									pathOptions={{
										color: '#00ffff',
										weight: isWalk ? 5 : 6,
										opacity: 0.95,
										lineCap: 'round',
										lineJoin: 'round',
										dashArray: isWalk ? '10 6' : undefined,
									}}
								/>
								{/* Main overlay line */}
								<Polyline
									key={seg._normKey}
									positions={seg.coords}
									pathOptions={{
										// Walking legs: deep grey, dashed so they remain distinct from vehicle tracks
										color: isWalk ? 'hsla(307, 53%, 67%, 1.00)' : (seg.color || '#1a73e8'),
										weight: isWalk ? 4 : 4,
										opacity: isWalk ? 1 : 1,
										lineCap: 'round',
										lineJoin: 'round',
										dashArray: isWalk ? '10 6' : undefined,
									}}
								/>
								{isWalk && seg.coords && seg.coords.length >= 2 && (
									<>
										<CircleMarker
											center={seg.coords[0]}
											radius={4}
											pathOptions={{ color: '#1f2937', weight: 1, fillColor: '#1f2937', fillOpacity: 1 }}
										/>
										<CircleMarker
											center={seg.coords[seg.coords.length - 1]}
											radius={4}
											pathOptions={{ color: '#1f2937', weight: 1, fillColor: '#1f2937', fillOpacity: 1 }}
										/>
									</>
								)}
							</>
						);
				})}
				{/* Origin dot */}
				{displaySegments[0]?.coords?.[0] && (
					<CircleMarker
						center={displaySegments[0].coords[0]}
						radius={7}
						pathOptions={{ color: '#fff', weight: 2, fillColor: '#10B981', fillOpacity: 1 }}
					/>
				)}
				{/* Destination dot */}
				{(() => {
					const last = displaySegments[displaySegments.length - 1];
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
						// Enable scroll-wheel / trackpad zoom and keep pinch-to-zoom on touch devices
						// (scrollWheelZoom handles mouse wheel and trackpad two-finger scroll; touchZoom enables pinch)
							scrollWheelZoom={true}
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
				{/* Developer debug overlay removed - rely on JourneyRouteLayer smoothing and styling */}
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
