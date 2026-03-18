import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import { MapContainer, Marker, Popup, Tooltip, Polyline, CircleMarker, TileLayer, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import icon from 'leaflet/dist/images/marker-icon.png';
import iconShadow from 'leaflet/dist/images/marker-shadow.png';
import "leaflet/dist/leaflet.css";
import React, { useEffect } from "react";
// Allow a sideContent prop to be injected by the parent (e.g. Suggested routes)
import BusStopLayer from "./BusStopLayer";
import RouteLineLayer from "./RouteLineLayer";
import { useRouteLine } from "../../hooks/useRouteLine";
import { fetchRouteLineWithFallback, fetchRouteLineNoFallback, fetchRouteLabel, stopsToLatLngs } from '../../services/routeLineApi';
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
const createCustomIcon = (type, color, label = null, bearing = null) => {
	// --- Layout constants ---
	// The arrow tip extends `arrowH` px beyond the circle rim.
	// The canvas is a square padded so the tip never clips at ANY rotation angle,
	// because the tip traces a circle of radius (r + arrowH) around the center.
	const r = 12;        // badge circle radius
	const arrowH = 10;   // how far the arrow tip protrudes past the circle rim
	const arrowW = 8;    // arrowhead half-width at its base (wider = more visible)
	const PAD = 4;       // minimum gap between any element and the SVG edge

	// tipDist = max distance from center to any element (the arrow tip)
	const tipDist = r + arrowH;
	const cx = tipDist + PAD;   // canvas center = circle center (24)
	const S = cx * 2;           // total SVG canvas size (48) — equal on all sides
	// Leaflet uses `iconSize` as the interactive hitbox for divIcons.
	// Keep the hitbox ~equal to the visible circle badge (2r) rather than
	// the padded SVG canvas (S), so hover/click triggers at the icon edge.
	const hit = r * 2;

	const rot = (bearing != null && !Number.isNaN(Number(bearing))) ? Number(bearing) : null;
	const hasBearing = rot !== null;

	let bgFill, strokeColor, strokeWidth;
	// staticContent: rendered OUTSIDE the rotating group → always upright (labels, icons)
	let staticContent = '';
	// arrowSvg: rendered INSIDE the rotating group → rotates with bearing
	let arrowSvg = '';

	if (type === 'bus') {
		if (label) {
			bgFill = color;
			strokeColor = 'white';
			strokeWidth = 2;
			const text = String(label).substring(0, 4);
			const fontSize = text.length >= 4 ? 8 : text.length === 3 ? 9.5 : 11;
			staticContent = `<text x="${cx}" y="${cx + 0.5}" text-anchor="middle" dominant-baseline="middle" font-family="Arial,Helvetica,sans-serif" font-size="${fontSize}" font-weight="bold" fill="white">${text}</text>`;
		} else {
			bgFill = 'white';
			strokeColor = color;
			strokeWidth = 2;
			// Small bus silhouette (upright, not rotated)
			staticContent = `<rect x="${cx - 5}" y="${cx - 4}" width="10" height="6" rx="1.5" fill="none" stroke="${color}" stroke-width="1.5"/>` +
				`<line x1="${cx - 5}" y1="${cx - 1}" x2="${cx + 5}" y2="${cx - 1}" stroke="${color}" stroke-width="0.8"/>` +
				`<circle cx="${cx - 2.5}" cy="${cx + 4}" r="1.5" fill="${color}"/>` +
				`<circle cx="${cx + 2.5}" cy="${cx + 4}" r="1.5" fill="${color}"/>`;
		}

		if (hasBearing) {
			// Chevron arrow pointing UP (before rotation).
			// Base corners are buried inside the circle fill to hide the join.
			// A concave notch at the midpoint of the base gives it a proper
			// arrowhead / chevron shape rather than a plain triangle.
			const tipY    = cx - r - arrowH;       // sharp tip above circle
			const baseY   = cx - r + 3;             // base buried inside circle
			const notchY  = baseY - arrowH * 0.42;  // concave notch pulls base inward
			const arrowColor  = label ? 'white' : color;
			const arrowStroke = label ? color  : 'white';
			// Points: tip → right-base → notch-center → left-base → back to tip
			const pts = `${cx},${tipY} ${cx + arrowW},${baseY} ${cx},${notchY} ${cx - arrowW},${baseY}`;
			arrowSvg = `<polygon points="${pts}" fill="${arrowColor}" stroke="${arrowStroke}" stroke-width="1.2" stroke-linejoin="round"/>`;
		}
	} else {
		// Train marker
		bgFill = 'white';
		strokeColor = color;
		strokeWidth = 2;
		staticContent = `<path d="M${cx} ${cx - 5.5}l-4 2.5v5.5h8v-5.5z" fill="none" stroke="${color}" stroke-width="1.5"/>` +
			`<line x1="${cx - 4}" y1="${cx + 2}" x2="${cx + 4}" y2="${cx + 2}" stroke="${color}" stroke-width="1.5"/>`;
	}

	// Circle element (shared: sits on top of arrow base when bearing present)
	const circleEl = `<circle cx="${cx}" cy="${cx}" r="${r}" fill="${bgFill}" stroke="${strokeColor}" stroke-width="${strokeWidth}"/>`;

	// Assemble SVG:
	//  - rotating group (arrow + circle) → direction tracks bearing
	//  - static group (text/icon) → always upright, never counter-rotated via brittle string hacks
	const rotDeg = hasBearing ? rot : 0;
	const rotatingGroup = hasBearing
		? `<g transform="rotate(${rotDeg} ${cx} ${cx})">${arrowSvg}${circleEl}</g>`
		: circleEl;
	const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${S}" height="${S}" viewBox="0 0 ${S} ${S}">${rotatingGroup}${staticContent}</svg>`;

	return L.divIcon({
		className: '',
		html: svg,
		iconSize: [hit, hit],
		iconAnchor: [hit / 2, hit / 2],
		popupAnchor: [0, -hit / 2]
	});

};

/**
 * Small, high-contrast icon for the user's location.
 */
const createUserIcon = () => {
	const size = 18;
	const r = 6;
	const cx = size / 2;
	const cy = size / 2;
	const html = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">`
		+ `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#1d4ed8" stroke="white" stroke-width="2"/>`
		+ `</svg>`;
	return L.divIcon({
		className: '',
		html,
		iconSize: [size, size],
		iconAnchor: [cx, cy],
		popupAnchor: [0, -cy]
	});
};

/**
 * Create a simple circular endpoint marker icon with an optional letter.
 */
const createEndpointIcon = (color, letter) => {
	const size = 28;
	const cx = size / 2;
	const cy = size / 2;
	const r = 11;
	const text = (letter == null) ? '' : String(letter).substring(0, 1);
	const html = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">`
		+ `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${color}" stroke="white" stroke-width="3"/>`
		+ (text
			? `<text x="${cx}" y="${cy + 0.5}" text-anchor="middle" dominant-baseline="middle" font-family="Arial,Helvetica,sans-serif" font-size="12" font-weight="bold" fill="white">${text}</text>`
			: '')
		+ `</svg>`;
	return L.divIcon({
		className: '',
		html,
		iconSize: [size, size],
		iconAnchor: [cx, cy],
		popupAnchor: [0, -cy]
	});
};

/**
 * Programmatically add prominent Start/Destination markers to a Leaflet map.
 * Returns an array of created Leaflet marker instances (so callers can remove them).
 * @param {L.Map} map
 * @param {[number,number]|null} start [lat,lon]
 * @param {[number,number]|null} end [lat,lon]
 */
export const highlightEndpoints = (map, start, end) => {
	if (!map) return [];
	const created = [];
	try {
                // Ensure arrays have at least 2 valid numbers
		if (start && Array.isArray(start) && start.length >= 2 && Number.isFinite(start[0])) {
			const m = L.marker(start, { icon: createEndpointIcon('#10B981', 'S'), pane: 'endpointPane' }).addTo(map);
			created.push(m);
		}
		if (end && Array.isArray(end) && end.length >= 2 && Number.isFinite(end[0])) {
			const m = L.marker(end, { icon: createEndpointIcon('#d32f2f', 'D'), pane: 'endpointPane' }).addTo(map);
			created.push(m);
		}
	} catch (e) {
		// ignore failures — function is best-effort
	}
	return created;
};

/**
 * Returns the colour to use for a bus marker icon based on delay.
 * @param {number|null} delayMinutes
 */
const busIconColor = (delayMinutes, isMapped = true) => {
        if (!isMapped) return '#9e9e9e';         // unmapped → grey
        // Treat negative delays (early) visually the same as on-time.
        if (delayMinutes == null) return '#1976d2';      // unknown → blue
        if (delayMinutes >= 10) return '#d32f2f';        // very late → red
        if (delayMinutes >= 2) return '#f57c00';         // delayed → orange
        return '#1976d2';                                // on time / early → blue
};

const isBusMappedLocal = (m) => {
  if (!m || m.type !== 'bus') return false;
  if (m.mock === true) return true;
  if (m.id && String(m.id).toLowerCase().startsWith('mock')) return true;
  const meta = m.meta || {};
	// Canonical mapping marker is now route_int; if we have it, the backend matched
	// this vehicle to a timetable journey for this refresh.
	const top = m.logged_journey_id || m.journey_id || m.route_id || m.route_int || null;
  if (top) return true;
  const mr = (m.match_reason ?? (meta && meta.match_reason) ?? null);
  if (mr != null) return String(mr).toLowerCase() === 'matched';
  const keys = Object.keys(meta).map(k => String(k).toLowerCase());
	const want = ['logged_journey_id', 'loggedjourneyid', 'journey_id', 'journeyid', 'route_id', 'routeid', 'route_int', 'routeint'];
  for (const w of want) {
    if (keys.includes(w)) return true;
    if (meta[w] || meta[w.replace(/_/g, '')]) return true;
  }
  return false;
};

const TRAIN_ICON = createCustomIcon('train', '#2e7d32', null, null);
const USER_ICON = createUserIcon();

// --- OSRM snapping (best-effort) -----------------------------------------
// Input/Output coords are always [lat, lon].
// Uses OSRM /route as a lightweight road-following smoother. If OSRM isn't
// reachable or returns an error, we fall back to the original coords.
const OSRM_BASE = (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_OSRM_BASE)
	? String(import.meta.env.VITE_OSRM_BASE)
	: 'http://127.0.0.1:5012';

const osrmRouteCoordsLatLon = async (coordsLatLon, { profile = 'driving', timeoutMs = 2500, maxWaypoints = 90 } = {}) => {
	try {
		if (!Array.isArray(coordsLatLon) || coordsLatLon.length < 2) return coordsLatLon;
		// Normalize to numbers and drop junk.
		const norm = [];
		for (const pt of coordsLatLon) {
			if (!Array.isArray(pt) || pt.length < 2) continue;
			const lat = Number(pt[0]);
			const lon = Number(pt[1]);
			if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
			norm.push([lat, lon]);
		}
		if (norm.length < 2) return coordsLatLon;

		// Downsample to keep URL size and OSRM compute reasonable.
		let sampled = norm;
		if (norm.length > maxWaypoints) {
			const step = (norm.length - 1) / (maxWaypoints - 1);
			sampled = [];
			for (let i = 0; i < maxWaypoints; i++) {
				const idx = Math.round(i * step);
				sampled.push(norm[Math.min(norm.length - 1, Math.max(0, idx))]);
			}
			// De-dupe consecutive identical points.
			const dedup = [];
			for (const p of sampled) {
				const last = dedup[dedup.length - 1];
				if (!last || last[0] !== p[0] || last[1] !== p[1]) dedup.push(p);
			}
			sampled = dedup;
			if (sampled.length < 2) return coordsLatLon;
		}

		const coordStr = sampled.map(([lat, lon]) => `${lon},${lat}`).join(';');
		const url = `${OSRM_BASE.replace(/\/$/, '')}/route/v1/${encodeURIComponent(profile)}/${coordStr}?overview=full&geometries=geojson`;

		const controller = new AbortController();
		const t = setTimeout(() => controller.abort(), timeoutMs);
		let resp;
		try {
			resp = await fetch(url, { signal: controller.signal });
		} finally {
			clearTimeout(t);
		}
		if (!resp || !resp.ok) return coordsLatLon;
		const data = await resp.json();
		const osrmCoords = data && data.routes && data.routes[0] && data.routes[0].geometry && data.routes[0].geometry.coordinates;
		if (!Array.isArray(osrmCoords) || osrmCoords.length < 2) return coordsLatLon;
		// OSRM returns [lon, lat]
		const out = [];
		for (const pt of osrmCoords) {
			if (!Array.isArray(pt) || pt.length < 2) continue;
			const lon = Number(pt[0]);
			const lat = Number(pt[1]);
			if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
			out.push([lat, lon]);
		}
		return out.length >= 2 ? out : coordsLatLon;
	} catch (e) {
		return coordsLatLon;
	}
};

// Utility: project point P onto segment AB and return nearest point on segment
const _projectPointOntoSegment = (px, py, ax, ay, bx, by) => {
	const vx = bx - ax;
	const vy = by - ay;
	const wx = px - ax;
	const wy = py - ay;
	const vlen2 = vx * vx + vy * vy;
	if (vlen2 === 0) return { x: ax, y: ay };
	const t = Math.max(0, Math.min(1, (wx * vx + wy * vy) / vlen2));
	return { x: ax + t * vx, y: ay + t * vy, t };
};

// Utility: find nearest point on a polyline (array of [lat, lon]) to a given point [lat, lon]
const _nearestPointOnPolyline = (poly, pt) => {
	if (!Array.isArray(poly) || poly.length === 0) return null;
	let best = null;
	const px = Number(pt[0]);
	const py = Number(pt[1]);
	for (let i = 0; i < poly.length - 1; i++) {
		const a = poly[i];
		const b = poly[i + 1];
		const ax = Number(a[0]);
		const ay = Number(a[1]);
		const bx = Number(b[0]);
		const by = Number(b[1]);
		const proj = _projectPointOntoSegment(px, py, ax, ay, bx, by);
		const dx = proj.x - px;
		const dy = proj.y - py;
		const d2 = dx * dx + dy * dy;
		if (best == null || d2 < best.d2) {
			best = { x: proj.x, y: proj.y, d2, segIndex: i, t: proj.t };
		}
	}
	if (!best) return null;
	return [best.x, best.y];
};

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
	const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:5050').replace(/\/$/, '');
	const DEBUG_JOURNEY_ROUTE = String(import.meta.env.VITE_DEBUG_JOURNEY_ROUTE || '').toLowerCase() === '1' || String(import.meta.env.VITE_DEBUG_JOURNEY_ROUTE || '').toLowerCase() === 'true';
	// Debug overlay toggle for hover-intent investigation.
	// NOTE: this component lives outside MapViewMap's scope, so it must compute its
	// own flag (it can't reference MapViewMap-local variables).
	const showHoverDebugUi = React.useMemo(() => {
		try {
			if (typeof window === 'undefined') return false;
			const qsEnabled = new URLSearchParams(window.location.search || '').get('hoverDebug') === '1';
			const lsEnabled = !!(window.localStorage && window.localStorage.getItem('HOVER_INTENT_DEBUG_UI') === '1');
			return qsEnabled || lsEnabled;
		} catch (e) {
			return false;
		}
	}, []);

	// Local cache of fetched OSRM geometries for segments which lack a usable
	// polyline. Keyed by segment _normKey so refreshes replace entries.
	const [fetchedCoordsMap, setFetchedCoordsMap] = React.useState({});
	// Keep track of any programmatically-added endpoint markers so we can
	// remove them when the route changes or the component unmounts.
	const endpointMarkersRef = React.useRef([]);

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
			if (coords.length === 0) continue;

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
	// Preserve any explicit endpoints attached to the input array (Home page uses
	// segments._start/_end) since normalizeSegments returns a new Array.
	try {
		if (segments && segments._start && !normSegments._start) normSegments._start = segments._start;
		if (segments && segments._end && !normSegments._end) normSegments._end = segments._end;
	} catch (e) {
		// ignore
	}

	// Helper to normalise coords returned from the backend/OSRM to [[lat,lon],...]
	const normalizeCoords = (raw) => {
		if (!Array.isArray(raw)) return [];
		const out = [];
		let dropped = 0;

		const inLatRange = (x) => Number.isFinite(x) && x >= -90 && x <= 90;
		const inLonRange = (x) => Number.isFinite(x) && x >= -180 && x <= 180;

		for (const pt of raw) {
			if (!Array.isArray(pt) || pt.length < 2) {
				dropped++;
				continue;
			}
			let a = Number(pt[0]);
			let b = Number(pt[1]);
			if (!Number.isFinite(a) || !Number.isFinite(b)) {
				dropped++;
				continue;
			}

			// Decide whether the point is [lat, lon] or [lon, lat].
			// Only swap when it *clearly* looks swapped: a can't be lat but b can.
			let lat = a;
			let lon = b;
			const aLat = inLatRange(a);
			const bLat = inLatRange(b);
			const aLon = inLonRange(a);
			const bLon = inLonRange(b);

			if (!aLat && bLat && aLon && bLon) {
				// likely [lon, lat]
				lat = b;
				lon = a;
			}

			if (!inLatRange(lat) || !inLonRange(lon)) {
				dropped++;
				continue;
			}
			out.push([lat, lon]);
		}

		try {
			if (dropped > 0 && raw.length >= 10) {
				console.debug('[map] normalizeCoords', { inLen: raw.length, outLen: out.length, dropped });
			}
		} catch (e) {
			// ignore
		}

		return out;
	};

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
	// Preserve explicit endpoints through the map() which created a new array.
	try {
		if (normSegments._start && !displaySegments._start) displaySegments._start = normSegments._start;
		if (normSegments._end && !displaySegments._end) displaySegments._end = normSegments._end;
	} catch (e) {
		// ignore
	}

	useEffect(() => {
		if (!DEBUG_JOURNEY_ROUTE) return;
		try {
			const segs = Array.isArray(displaySegments) ? displaySegments : [];
			const walkCount = segs.filter((s) => s && (s.mode === 'walking' || s.type === 'walk' || s.color === '#888888' || (s.id && String(s.id).startsWith('walk')))).length;
			const stopPts = segs.reduce((n, s) => n + ((s && s._from) ? 1 : 0) + ((s && s._to) ? 1 : 0), 0);
			console.debug('[JourneyRouteLayer] render', {
				segCount: segs.length,
				walkCount,
				stopPts,
				start: displaySegments && displaySegments._start,
				end: displaySegments && displaySegments._end,
				firstSeg: segs[0] ? { id: segs[0].id, mode: segs[0].mode, color: segs[0].color, dash: (segs[0].mode === 'walking') } : null,
			});
		} catch (e) {
			// ignore
		}
	}, [DEBUG_JOURNEY_ROUTE, JSON.stringify(displaySegments.map((s) => s._normKey))]);

	useEffect(() => {
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

	// Explicitly highlight start and end points for the current route.
	useEffect(() => {
		try {
			// remove any previous markers
			if (Array.isArray(endpointMarkersRef.current) && endpointMarkersRef.current.length > 0) {
				endpointMarkersRef.current.forEach((m) => {
					try { map.removeLayer(m); } catch (e) { /* ignore */ }
				});
				endpointMarkersRef.current = [];
			}

			if (!displaySegments || displaySegments.length === 0) return;

			// Prefer explicit endpoints when provided by the caller (Home page attaches
			// these from journey.meta.start_point/destination).
			let start = null;
			let end = null;
			try {
				if (displaySegments._start && Array.isArray(displaySegments._start) && displaySegments._start.length >= 2) {
					start = displaySegments._start;
				}
				if (displaySegments._end && Array.isArray(displaySegments._end) && displaySegments._end.length >= 2) {
					end = displaySegments._end;
				}
			} catch (e) {
				start = null;
				end = null;
			}

			// Fallback: derive endpoints from polyline coords.
			if (!start) {
				for (const seg of displaySegments) {
					if (seg?.coords?.length > 0) {
						start = seg.coords[0];
						break;
					}
				}
			}
			if (!end) {
				for (let i = displaySegments.length - 1; i >= 0; i--) {
					const seg = displaySegments[i];
					if (seg?.coords?.length > 0) {
						end = seg.coords[seg.coords.length - 1];
						break;
					}
				}
			}
			if (map && start && end) {
				const created = highlightEndpoints(map, start, end);
				endpointMarkersRef.current = created;
			}
		} catch (e) {
			// best-effort only
		}
		return () => {
			if (Array.isArray(endpointMarkersRef.current)) {
				endpointMarkersRef.current.forEach((m) => {
					try { map.removeLayer(m); } catch (e) { /* ignore */ }
				});
				endpointMarkersRef.current = [];
			}
		};
	}, [map, JSON.stringify(displaySegments.map(s => s._normKey))]);

	// For any non-walking segment with fewer than 2 coords, attempt a best-effort
	// OSRM fetch to build a polyline between the previous segment's end and
	// the next segment's start. This helps when the journey-plan returned only
	// a single-point placeholder for a vehicle leg.
	useEffect(() => {
		let mounted = true;
		(async () => {
			if (!displaySegments || displaySegments.length === 0) return;
			const updates = {};
			for (let i = 0; i < displaySegments.length; i++) {
				const seg = displaySegments[i];
				const isWalk = seg.mode === 'walking' || seg.type === 'walk' || seg.color === '#888888' || (seg.id && String(seg.id).startsWith('walk'));
				if (isWalk) continue;
				if (!seg || !Array.isArray(seg.coords) || seg.coords.length >= 2) continue;
				// Skip if we already fetched coords for this segment
				if (seg._normKey && fetchedCoordsMap[seg._normKey]) continue;
				// If the segment knows a canonical timetable route_int, pass it through so the backend
				// can return clean stop-stitched geometry (and avoid messy raw route_tracks).
				const segRouteInt = (
					seg?.route_int ??
					seg?.routeInt ??
					seg?.meta?.route_int ??
					seg?.meta?.routeInt ??
					null
				);
				// Determine from/to points from neighbouring segments
				let from = null;
				let to = null;
				const prev = displaySegments[i - 1];
				const next = displaySegments[i + 1];
				if (prev) {
					const pk = prev._normKey;
					const pa = (pk && fetchedCoordsMap[pk]) ? fetchedCoordsMap[pk] : prev.coords;
					if (pa && pa.length) from = pa[pa.length - 1];
				}
				if (!from && seg.coords && seg.coords.length) from = seg.coords[0];
				if (next) {
					const nk = next._normKey;
					const nb = (nk && fetchedCoordsMap[nk]) ? fetchedCoordsMap[nk] : next.coords;
					if (nb && nb.length) to = nb[0];
				}
				if (!to && seg.coords && seg.coords.length) to = seg.coords[0];
				if (!from || !to) continue;
				try {
					let url = `${API_BASE}/route/leg-geometry?from_lat=${encodeURIComponent(from[0])}&from_lon=${encodeURIComponent(from[1])}&to_lat=${encodeURIComponent(to[0])}&to_lon=${encodeURIComponent(to[1])}&mode=driving`;
					if (segRouteInt != null && String(segRouteInt) !== '') {
						url += `&route_int=${encodeURIComponent(String(segRouteInt))}`;
					}
					const resp = await fetch(url);
					if (!resp.ok) continue;
					const data = await resp.json();
					const norm = normalizeCoords(data.coords);
					if (norm && norm.length >= 2) {
						if (mounted) updates[seg._normKey] = norm;
					}
				} catch (e) {
					// ignore
				}
			}
			if (mounted && Object.keys(updates).length) {
				setFetchedCoordsMap((p) => ({ ...p, ...updates }));
			}
		})();
		return () => { mounted = false; };
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
			{renderSegments.map((seg, segIdx) => {
					// Prefer any fetched OSRM geometry for segments that lacked a usable polyline
					const actualCoords = (seg && seg._normKey && fetchedCoordsMap[seg._normKey]) ? fetchedCoordsMap[seg._normKey] : seg.coords;
					if (!actualCoords || actualCoords.length < 2) return null;
					const isWalk = seg.mode === 'walking' || seg.color === '#888888' || (seg.id && String(seg.id).startsWith('walk'));
					// Use normalized key to force Leaflet to replace the polyline when coords change
					// For walking segments, use a heavier weight and a bold dashed pattern so they remain visible on top of vehicle tracks.
					return (
						<>
								{/* Cyan underlay/frame so route lines (vehicle and walking) have a cyan outline */}
								<Polyline
									pane="routePane"
									key={`${seg._normKey}-frame`}
									positions={actualCoords}
									pathOptions={{
										color: '#00ffff',
										weight: isWalk ? 6 : 6,
										opacity: isWalk ? 0.55 : 0.95,
										lineCap: 'round',
										lineJoin: 'round',
										dashArray: isWalk ? '10 6' : undefined,
									}}
								/>
								{/* Main overlay line */}
								<Polyline
									pane="routePane"
									key={seg._normKey}
									positions={actualCoords}
									pathOptions={{
										// Walking legs: neutral grey + dashed so they read differently from transit
										color: isWalk ? '#6b7280' : (seg.color || '#1a73e8'),
										weight: isWalk ? 4 : 4,
										opacity: isWalk ? 0.95 : 1,
										lineCap: 'round',
										lineJoin: 'round',
										dashArray: isWalk ? '10 6' : undefined,
									}}
								/>
								{isWalk && actualCoords && actualCoords.length >= 2 && (
									<>
										<CircleMarker
										pane="transferPane"
											center={actualCoords[0]}
											radius={4}
											pathOptions={{ color: '#1f2937', weight: 1, fillColor: '#1f2937', fillOpacity: 1 }}
										/>
										<CircleMarker
										pane="transferPane"
											center={actualCoords[actualCoords.length - 1]}
											radius={4}
											pathOptions={{ color: '#1f2937', weight: 1, fillColor: '#1f2937', fillOpacity: 1 }}
										/>
									</>
								)}
						</>
					);
				})}

							{/* Transfer stop markers: render a marker at the boundary between consecutive segments */}
							{(() => {
								const pts = [];
								for (let i = 0; i < displaySegments.length - 1; i++) {
									const aKey = displaySegments[i]._normKey;
									const bKey = displaySegments[i + 1]._normKey;
									const a = (aKey && fetchedCoordsMap[aKey]) ? fetchedCoordsMap[aKey] : displaySegments[i].coords;
									const b = (bKey && fetchedCoordsMap[bKey]) ? fetchedCoordsMap[bKey] : displaySegments[i + 1].coords;
									let pt = null;
									if (a && a.length) pt = a[a.length - 1];
									if ((!pt || pt.length === 0) && b && b.length) pt = b[0];
									if (pt && pt.length === 2) {
										// Skip origin/destination (first/last)
										if (i !== 0 || (displaySegments[0] && displaySegments[0].coords && displaySegments[0].coords.length > 0)) pts.push(pt);
									}
								}
								return pts.map((p, idx) => (
									<CircleMarker
										pane="transferPane"
										key={`transfer-${idx}`}
										center={p}
										radius={6}
										pathOptions={{ color: '#fff', weight: 2, fillColor: '#F59E0B', fillOpacity: 1 }}
									/>
								));
							})()}

							{/* Stop markers: render from/to points when segments provide them (Home page attaches these per leg). */}
							{(() => {
								const pts = [];
								const pushUnique = (p) => {
									if (!p || !Array.isArray(p) || p.length < 2) return;
									const lat = Number(p[0]);
									const lon = Number(p[1]);
									if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
									const key = `${lat.toFixed(6)},${lon.toFixed(6)}`;
									if (pts.some((x) => x._k === key)) return;
									pts.push({ _k: key, p: [lat, lon] });
								};
								for (const seg of displaySegments) {
									if (seg && seg._from) pushUnique(seg._from);
									if (seg && seg._to) pushUnique(seg._to);
								}
								return pts.map((x, idx) => (
									<CircleMarker
										pane="transferPane"
										key={`stoppt-${x._k}-${idx}`}
										center={x.p}
										radius={5}
										pathOptions={{ color: '#ffffff', weight: 2, fillColor: '#111827', fillOpacity: 1 }}
									/>
								));
							})()}
			</>
		);
	};


const MapController = ({ onReady, onMoveEnd, selectedVehicleTrack, onClearSelectedVehicleTrack }) => {
	const map = useMap();
	const vehicleLayerRef = React.useRef(null);
	const vehicleCoreLayerRef = React.useRef(null);
	const vehicleStartRef = React.useRef(null);
	const vehicleEndRef = React.useRef(null);

	useMapEvents({
		moveend: () => {
			if (onMoveEnd) {
				const center = map.getCenter();
				let bounds = null;
				try {
					bounds = map.getBounds();
				} catch (e) {
					bounds = null;
				}
				onMoveEnd({ lat: center.lat, lon: center.lng, bounds });
			}
		},
	});

	useEffect(() => {
		// Create dedicated panes so we can control z-order between
		// route polylines (routePane) and transfer/endpoint markers
		// (transferPane). This ensures transfer stop markers render
		// above route tracks regardless of render order.
		try {
			// Dedicated tooltip pane for vehicle hover labels.
			//
			// Why: In this app we create several custom panes (tracks, transfers, endpoints).
			// In some Leaflet/react-leaflet setups the default tooltipPane can end up behind
			// other overlays or be affected by unexpected CSS stacking contexts.
			//
			// Fix: Put vehicle tooltips in their own pane with a very high z-index.
			if (!map.getPane('vehicleTooltipPane')) {
				map.createPane('vehicleTooltipPane');
				map.getPane('vehicleTooltipPane').style.zIndex = 2000;
			}
			if (!map.getPane('routePane')) {
				map.createPane('routePane');
				map.getPane('routePane').style.zIndex = 400;
			}
			// Vehicle tracks should always be visible above base tiles and other overlays.
			// Use a dedicated pane with a very high z-index so the selected vehicle's
			// polyline can't be hidden behind other layers.
			if (!map.getPane('vehicleTrackPane')) {
				map.createPane('vehicleTrackPane');
				// Leaflet defaults: overlayPane=400, markerPane=600, tooltipPane=650, popupPane=700.
				// Keep the track above routes/overlays, but BELOW vehicle markers.
				// (markerPane=600) so bus icons stay on top.
				map.getPane('vehicleTrackPane').style.zIndex = 590;
				// Allow clicks on the polyline without letting the map background click-clear
				// handler treat it as a background click.
			}
			if (!map.getPane('transferPane')) {
				map.createPane('transferPane');
				map.getPane('transferPane').style.zIndex = 650;
			}
			if (!map.getPane('endpointPane')) {
				map.createPane('endpointPane');
				map.getPane('endpointPane').style.zIndex = 700;
			}
		} catch (e) {
			// ignore if map not ready
		}
		if (onReady) onReady(map);
		// Fire initial center on mount so the hook receives coordinates immediately
		if (onMoveEnd) {
			const center = map.getCenter();
			let bounds = null;
			try {
				bounds = map.getBounds();
			} catch (e) {
				bounds = null;
			}
			onMoveEnd({ lat: center.lat, lon: center.lng, bounds });
		}
	}, [map, onReady, onMoveEnd]);

	// Imperative selected-vehicle overlay. This bypasses react-leaflet's rendering
	// lifecycle issues and guarantees the polyline is added to the Leaflet map.
	useEffect(() => {
		if (!map) return;
		// Clear existing layers
		try {
			if (vehicleLayerRef.current) {
				vehicleLayerRef.current.remove();
				vehicleLayerRef.current = null;
			}
			if (vehicleCoreLayerRef.current) {
				vehicleCoreLayerRef.current.remove();
				vehicleCoreLayerRef.current = null;
			}
			if (vehicleStartRef.current) {
				vehicleStartRef.current.remove();
				vehicleStartRef.current = null;
			}
			if (vehicleEndRef.current) {
				vehicleEndRef.current.remove();
				vehicleEndRef.current = null;
			}
		} catch (e) {
			// ignore
		}

		const coords = selectedVehicleTrack && Array.isArray(selectedVehicleTrack.coords) ? selectedVehicleTrack.coords : null;
		if (!coords || coords.length < 2) return;

		try {
			// Ensure pane exists even if the controller's mount effect hasn't run yet.
			if (!map.getPane('vehicleTrackPane')) {
				map.createPane('vehicleTrackPane');
				map.getPane('vehicleTrackPane').style.zIndex = 590;
			}
			const paneName = map.getPane('vehicleTrackPane') ? 'vehicleTrackPane' : undefined;
			const latLngs = coords.map((p) => L.latLng(Number(p[0]), Number(p[1])));
			try {
				console.debug('[map] imperative vehicle track add', {
					id: selectedVehicleTrack && selectedVehicleTrack.id,
					len: latLngs.length,
					first: [latLngs[0].lat, latLngs[0].lng],
					last: [latLngs[latLngs.length - 1].lat, latLngs[latLngs.length - 1].lng],
					pane: paneName,
				});
			} catch (e) {
				// ignore
			}
			// Cyan casing/frame layer for the vehicle track
			vehicleLayerRef.current = L.polyline(latLngs, {
				pane: paneName,
				color: '#00ffff',
				weight: 8,
				opacity: 0.95,
				lineCap: 'round',
				lineJoin: 'round',
			}).addTo(map);

			// Single colored line (same color as the icon).
			vehicleCoreLayerRef.current = L.polyline(latLngs, {
				pane: paneName,
				color: (selectedVehicleTrack && selectedVehicleTrack.color) ? selectedVehicleTrack.color : '#1a73e8',
				weight: 5,
				opacity: 1,
				lineCap: 'round',
				lineJoin: 'round',
			}).addTo(map);

			const handleTrackClick = (e) => {
				try {
					if (e && e.originalEvent) {
						e.originalEvent.preventDefault();
						e.originalEvent.stopPropagation();
					}
					// Leaflet provides a helper for this exact purpose.
					try { L.DomEvent && typeof L.DomEvent.stopPropagation === 'function' && L.DomEvent.stopPropagation(e); } catch (ee) { /* ignore */ }
					if (e && typeof e.stopPropagation === 'function') e.stopPropagation();
					if (onClearSelectedVehicleTrack) onClearSelectedVehicleTrack();
				} catch (err) {
					// ignore
				}
			};

			vehicleLayerRef.current.on('click', handleTrackClick);
			vehicleCoreLayerRef.current.on('click', handleTrackClick);
			// Bring the casing to back so it sits behind the core layer
			vehicleLayerRef.current.bringToBack();

			vehicleStartRef.current = L.circleMarker(latLngs[0], {
				pane: paneName,
				radius: 8,
				color: '#ffffff',
				weight: 2,
				fillColor: (selectedVehicleTrack && selectedVehicleTrack.color) ? selectedVehicleTrack.color : '#1a73e8',
				fillOpacity: 1,
			}).addTo(map);
			vehicleStartRef.current.on('click', (e) => {
				try {
					if (e && e.originalEvent) {
						e.originalEvent.preventDefault();
						e.originalEvent.stopPropagation();
					}
					try { L.DomEvent && typeof L.DomEvent.stopPropagation === 'function' && L.DomEvent.stopPropagation(e); } catch (ee) { /* ignore */ }
					if (e && typeof e.stopPropagation === 'function') e.stopPropagation();
					if (onClearSelectedVehicleTrack) onClearSelectedVehicleTrack();
				} catch (err) {
					// ignore
				}
			});
			vehicleStartRef.current.bringToFront();

			vehicleEndRef.current = L.circleMarker(latLngs[latLngs.length - 1], {
				pane: paneName,
				radius: 8,
				color: '#ffffff',
				weight: 2,
				fillColor: (selectedVehicleTrack && selectedVehicleTrack.color) ? selectedVehicleTrack.color : '#1a73e8',
				fillOpacity: 1,
			}).addTo(map);
			vehicleEndRef.current.on('click', (e) => {
				try {
					if (e && e.originalEvent) {
						e.originalEvent.preventDefault();
						e.originalEvent.stopPropagation();
					}
					try { L.DomEvent && typeof L.DomEvent.stopPropagation === 'function' && L.DomEvent.stopPropagation(e); } catch (ee) { /* ignore */ }
					if (e && typeof e.stopPropagation === 'function') e.stopPropagation();
					if (onClearSelectedVehicleTrack) onClearSelectedVehicleTrack();
				} catch (err) {
					// ignore
				}
			});
			vehicleEndRef.current.bringToFront();

			// NOTE: we intentionally do NOT auto-fit bounds here. Auto-fitting every
					// time the track is added makes it feel like the track can’t be “hidden”
					// because the map keeps jumping back to it.
				} catch (e) {
					// ignore
				}

				return () => {
					try {
						if (vehicleLayerRef.current) vehicleLayerRef.current.remove();
						if (vehicleCoreLayerRef.current) vehicleCoreLayerRef.current.remove();
						if (vehicleStartRef.current) vehicleStartRef.current.remove();
						if (vehicleEndRef.current) vehicleEndRef.current.remove();
					} catch (e) { /* ignore */ }
				};
	}, [map, selectedVehicleTrack, onClearSelectedVehicleTrack]);	return null;
};

// Fit the map to a polyline when it changes (best-effort, guarded).
const MapFitToPolyline = ({ coords, padding = [30, 30] }) => {
	const map = useMap();
	const lastHashRef = React.useRef(null);

	useEffect(() => {
		if (!map) return;
		if (!Array.isArray(coords) || coords.length < 2) return;
		// Hash a few points so we don't constantly refit on minor re-renders.
		let h;
		try {
			const a = coords[0];
			const b = coords[Math.floor(coords.length / 2)];
			const c = coords[coords.length - 1];
			h = JSON.stringify([coords.length, a, b, c]);
		} catch (e) {
			h = null;
		}
		if (h && lastHashRef.current === h) return;
		lastHashRef.current = h;
		try {
			const bounds = L.latLngBounds(coords.map((p) => [Number(p[0]), Number(p[1])]));
			if (bounds.isValid()) {
				map.fitBounds(bounds, { paddingTopLeft: padding, paddingBottomRight: padding, maxZoom: 16 });
			}
		} catch (e) {
			// ignore
		}
	}, [map, coords, padding]);

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

// Adaptive popup that places the popup to left/right/top depending on
// marker screen position so it remains visible without forcing a map pan/zoom.
function AdaptivePopup({ marker, onClose, children }) {
	const map = useMap();
	const [opts, setOpts] = React.useState({
		offset: [0, -12],
		autoPan: true,
		keepInView: true,
		autoPanPadding: [28, 28],
		maxWidth: 360,
	});

	React.useEffect(() => {
		try {
			if (!map || !marker || !marker.position) return;
			const size = map.getSize();
			const pt = map.latLngToContainerPoint(marker.position);
			const edgeMargin = 120; // px from edge where we consider side placement
			// Default: above marker with small pan
			let next = { offset: [0, -12], autoPan: true, keepInView: true, autoPanPadding: [28, 28], maxWidth: 360 };
			if (pt.x < edgeMargin) {
				// Marker near left edge → show popup to the right of marker, avoid panning
				next = { offset: [140, 0], autoPan: false, keepInView: false, maxWidth: 320 };
			} else if (pt.x > (size.x - edgeMargin)) {
				// Marker near right edge → show popup to the left
				next = { offset: [-140, 0], autoPan: false, keepInView: false, maxWidth: 320 };
			} else if (pt.y < edgeMargin) {
				// Marker near top edge → show below marker
				next = { offset: [0, 20], autoPan: false, keepInView: false, maxWidth: 320 };
			}
			setOpts(next);
		} catch (e) {
			// ignore
		}
	}, [map, marker && marker.position]);

	return (
		<Popup onClose={onClose} autoClose={false} closeButton={false} {...opts}>
			<Box onClick={() => { try { onClose && onClose(); } catch (e) { /* ignore */ } }} sx={{ cursor: 'pointer' }}>
				{children}
			</Box>
		</Popup>
	);
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
		/** show or hide currently-active route overlays (from useRouteLine) */
		showRouteLines = true,
	/** Route geometry from journey planner. Array of {id, name, coords, color} */
	journeyRoute = null,
	// Optional companion callback: called with a stable signature string when
// a popup is opened. Parent may use this to verify the popup still refers
// to the same logical vehicle after data refreshes.
	onOpenPopupSignature = null,
}) {

	// ── Hover-intent (0.7s) for bus icons ───────────────────────────────────
	// We only kick off any hover-card request once the cursor has remained on
	// the SAME bus marker for >= 700ms. This prevents expensive calls when the
	// user is just moving across a cluster.
	//
	// Contract:
	// - `hoveredMarkerId` tracks what the cursor is currently over.
	// - `activeHoverId` is the marker that "won" the 0.7s dwell and is allowed to
	//   trigger requests / show richer UI.
	const debugHoverIntent = (typeof window !== 'undefined' && window.localStorage && window.localStorage.getItem('HOVER_INTENT_DEBUG') === '1');
	const debugHoverFlow = (() => {
		try {
			if (typeof window === 'undefined') return false;
			const qs = new URLSearchParams(window.location.search || '');
			return qs.get('hoverFlow') === '1' || (window.localStorage && window.localStorage.getItem('HOVER_FLOW_DEBUG') === '1');
		} catch (e) {
			return false;
		}
	})();
	const [hoveredMarkerId, setHoveredMarkerId] = React.useState(null);
	const hoveredMarkerIdRef = React.useRef(null);
	const hoveredMarkerSigRef = React.useRef(null);
	const hoverTimerRef = React.useRef(null);
	const [activeHoverId, setActiveHoverId] = React.useState(null);
	const activeHoverReqTokenRef = React.useRef(0);
	const activeHoverIdRef = React.useRef(null);
	// Hover tooltips are driven by map-level picking (see <HoverWinnerController />), not by
	// marker mouseover events (which can be swallowed by overlapping DOM/SVG layers).
	// Leaflet Tooltip triggering can vary (hover vs click) depending on how the layer is created.
	// We keep refs so we can imperatively open/close tooltips on hover.
	const markerRefsById = React.useRef(new Map());
	const _leafletFromRef = (ref) => {
		try {
			if (!ref) return null;
			// react-leaflet v4 often provides the Leaflet instance directly.
			// Some wrappers expose it as `.instance` or `.leafletElement`.
			return ref.getElement?.() || ref.instance || ref.leafletElement || ref;
		} catch (e) {
			return null;
		}
	};

	React.useEffect(() => {
		hoveredMarkerIdRef.current = hoveredMarkerId;
	}, [hoveredMarkerId]);

	React.useEffect(() => {
		activeHoverIdRef.current = activeHoverId;
	}, [activeHoverId]);

	// Leaflet's default tooltip triggering can vary by device/browser and can fall back
	// to click-only in some cases. We drive tooltip visibility from our map-level
	// hover picking state by imperatively opening/closing tooltips.
	React.useEffect(() => {
		try {
			const hoveredId = hoveredMarkerId;
			const activeId = activeHoverId;
			const shouldBeOpen = new Set([hoveredId, activeId].filter(Boolean));
			for (const [id, el] of markerRefsById.current.entries()) {
				if (!el) continue;
				try {
					if (shouldBeOpen.has(id)) {
						if (debugHoverFlow) console.debug('[hoverFlow] tooltip open', { id, reason: id === hoveredId ? 'hovered' : 'active' });
						el.openTooltip && el.openTooltip();
					} else {
						if (debugHoverFlow) console.debug('[hoverFlow] tooltip close', { id });
						el.closeTooltip && el.closeTooltip();
					}
				} catch (e2) {
					// ignore
				}
			}
		} catch (e) {
			// ignore
		}
	}, [hoveredMarkerId, activeHoverId]);

	React.useEffect(() => {
		try {
			if (!debugHoverFlow) return;
			console.info('[hoverFlow] enabled');
		} catch (e) {
			// ignore
		}
	}, [debugHoverFlow]);

	React.useEffect(() => {
		try {
			if (!debugHoverFlow) return;
			console.debug('[hoverFlow] state', { hoveredMarkerId, activeHoverId, markerRefCount: markerRefsById.current.size });
		} catch (e) {
			// ignore
		}
	}, [debugHoverFlow, hoveredMarkerId, activeHoverId]);
	// Dev-only “is hover state changing?” overlay.
	// Enable either by:
	// - localStorage.setItem('HOVER_INTENT_DEBUG_UI', '1')
	// - OR adding ?hoverDebug=1 to the URL
	const showHoverDebugUi = (() => {
		try {
			if (typeof window === 'undefined') return false;
			const qsEnabled = new URLSearchParams(window.location.search || '').get('hoverDebug') === '1';
			const lsEnabled = !!(window.localStorage && window.localStorage.getItem('HOVER_INTENT_DEBUG_UI') === '1');
			return qsEnabled || lsEnabled;
		} catch (e) {
			return false;
		}
	})();

	React.useEffect(() => {
		try {
			if (!showHoverDebugUi) return;
			console.info('[MapViewMap] hover debug UI enabled');
		} catch (e) {
			// ignore
		}
	}, [showHoverDebugUi]);

	// Debugging aid: verify that the SOURCE OF TRUTH state actually clears when the UI
	// looks “sticky”. Enable by setting localStorage.HOVER_INTENT_DEBUG = '1'.
	React.useEffect(() => {
		try {
			if (!debugHoverIntent) return;
			console.debug('[hoverIntent] state', { hoveredMarkerId, activeHoverId });
		} catch (e) {
			// ignore
		}
	}, [debugHoverIntent, hoveredMarkerId, activeHoverId]);

	React.useEffect(() => {
		return () => {
			try {
				if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
			} catch (e) {
				// ignore
			}
		};
	}, []);

	const beginHoverIntent = React.useCallback((marker) => {
		try {
			if (!marker || marker.type !== 'bus') return;
			// Allow hover for unmatched/grey buses too.
			// (They may still have useful labels even if they aren't matched to a timetable.)
			// If we already have an active hover on a different marker, clear it now.
			// This prevents “sticky” hover cards when sliding across dense clusters
			// where mouseout sometimes doesn’t fire for the old marker.
			setActiveHoverId((prev) => (prev && prev !== marker.id ? null : prev));
			try {
				if (debugHoverIntent) console.debug('[hoverIntent] begin', { id: marker.id, prevActive: activeHoverIdRef.current });
			} catch (e) {
				// ignore
			}
			const sig = makeMarkerSignature(marker);
			setHoveredMarkerId(marker.id);
			hoveredMarkerSigRef.current = sig;
			// Cancel any prior intent timer.
			if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
			const tokenAtStart = ++activeHoverReqTokenRef.current;
			hoverTimerRef.current = setTimeout(() => {
				// Only activate if we're still over the same logical vehicle.
				if (activeHoverReqTokenRef.current !== tokenAtStart) return;
				if (hoveredMarkerIdRef.current !== marker.id) return;
				if (hoveredMarkerSigRef.current !== sig) return;
				try {
					if (debugHoverIntent) console.debug('[hoverIntent] activate', { id: marker.id });
				} catch (e) {
					// ignore
				}
				setActiveHoverId(marker.id);
			}, 700);
		} catch (e) {
			// ignore
		}
	}, []);

	// Defensive cleanup: if we’re no longer hovering anything, ensure any
	// active hover is cleared. This catches edge cases where we miss a
	// marker mouseout event due to overlapping DOM/SVG layers.
	React.useEffect(() => {
		if (!hoveredMarkerId) {
			try {
				if (debugHoverIntent && activeHoverIdRef.current) console.debug('[hoverIntent] hovered cleared -> clear active', { prevActive: activeHoverIdRef.current });
			} catch (e) {
				// ignore
			}
			setActiveHoverId(null);
		}
	}, [hoveredMarkerId]);

	const cancelHoverIntent = React.useCallback((marker) => {
		try {
			if (!marker || marker.type !== 'bus') return;
			try {
				if (debugHoverIntent) console.debug('[hoverIntent] cancel', { id: marker.id, prevActive: activeHoverIdRef.current, prevHovered: hoveredMarkerIdRef.current });
			} catch (e) {
				// ignore
			}
			setHoveredMarkerId((prev) => (prev === marker.id ? null : prev));
			hoveredMarkerSigRef.current = null;
			try {
				if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
			} catch (e) {
				// ignore
			}
			// Invalidate any pending timer callback.
			try { activeHoverReqTokenRef.current += 1; } catch (e) { /* ignore */ }
			setActiveHoverId((prev) => (prev === marker.id ? null : prev));
		} catch (e) {
			// ignore
		}
	}, []);

	// NOTE: map-level mousemove picking must live in a descendant of <MapContainer>.
	// See <HoverWinnerController /> rendered inside the map.

// Helper: create a compact signature for a marker that changes when the
// logical vehicle changes. Prefer backend-provided identifiers (journey id,
// vehicle ref) and fall back to route number + quantized position.
const makeMarkerSignature = (m) => {
	try {
		const meta = m && m.meta ? m.meta : {};
		return meta.logged_journey_id || meta.journey_id || meta.vehicle_journey_code || meta.vehicle_ref || meta.vehicleId || m.routeNumber || `${String(m.id)}|${Math.round((m.position?.[0]||0)*1e5)}|${Math.round((m.position?.[1]||0)*1e5)}`;
	} catch (e) {
		return String(m && m.id);
	}
};

// Convert Leaflet layer points to pixel space and pick a single nearest bus marker
// within a small radius. This is used to avoid relying on marker mouseout events,
// which can be missed when sliding across dense overlapping icons.
// Vehicle markers are small and can be hard to hover precisely.
// On real maps with fast mouse movement + high DPI screens, 20px is often too
// strict, which makes hover feel like it "only works after click".
//
// Hysteresis helps when markers are close: once a marker is picked, we keep it
// until the cursor moves a little further away, reducing flapping.
const DEFAULT_HOVER_PICK_ENTER_RADIUS_PX = 32;
const DEFAULT_HOVER_PICK_LEAVE_RADIUS_PX = 44;

const _markerMouseD2 = ({ map, latlng, marker }) => {
	try {
		if (!map || typeof map.latLngToLayerPoint !== 'function') return null;
		if (!latlng || !marker) return null;
		const pos = Array.isArray(marker.position) ? marker.position : null;
		if (!pos || pos.length < 2) return null;
		const mousePt = map.latLngToLayerPoint(latlng);
		if (!mousePt) return null;
		const pt = map.latLngToLayerPoint({ lat: pos[0], lng: pos[1] });
		if (!pt) return null;
		const dx = pt.x - mousePt.x;
		const dy = pt.y - mousePt.y;
		return dx * dx + dy * dy;
	} catch (e) {
		return null;
	}
};
const pickNearestVehicleMarker = ({ markers, map, latlng, radiusPx = DEFAULT_HOVER_PICK_RADIUS_PX }) => {
	try {
		if (!map || typeof map.latLngToLayerPoint !== 'function') return null;
		if (!latlng) return null;
		if (!Array.isArray(markers) || markers.length === 0) return null;
		const mousePt = map.latLngToLayerPoint(latlng);
		if (!mousePt) return null;

		let best = null;
		let bestD2 = radiusPx * radiusPx;
		for (const m of markers) {
			if (!m) continue;
			if (m.type !== 'bus' && m.type !== 'train') continue;
			// Allow hover picking for grey/unmatched buses too.
			const pos = Array.isArray(m.position) ? m.position : null;
			if (!pos || pos.length < 2) continue;
			const pt = map.latLngToLayerPoint({ lat: pos[0], lng: pos[1] });
			if (!pt) continue;
			const dx = pt.x - mousePt.x;
			const dy = pt.y - mousePt.y;
			const d2 = dx * dx + dy * dy;
			if (d2 <= bestD2) {
				bestD2 = d2;
				best = m;
			}
		}
		return best;
	} catch (e) {
		return null;
	}
};

// Internal: hooks-based component that must be rendered under <MapContainer>.
// It implements the single-winner hover picking on map mousemove.
function HoverWinnerController({
	filteredMarkers,
	setHoveredMarkerId,
	hoveredMarkerSigRef,
	hoverTimerRef,
	activeHoverReqTokenRef,
	setActiveHoverId,
	enterRadiusPx = DEFAULT_HOVER_PICK_ENTER_RADIUS_PX,
	leaveRadiusPx = DEFAULT_HOVER_PICK_LEAVE_RADIUS_PX,
}) {
	const map = useMap();
	const debugHoverFlow = React.useMemo(() => {
		try {
			if (typeof window === 'undefined') return false;
			const qs = new URLSearchParams(window.location.search || '');
			return qs.get('hoverFlow') === '1' || (window.localStorage && window.localStorage.getItem('HOVER_FLOW_DEBUG') === '1');
		} catch (e) {
			return false;
		}
	}, []);
	const winnerIdRef = React.useRef(null);
	const candidateIdRef = React.useRef(null);
	const candidateSigRef = React.useRef(null);
	// If the pointer is briefly "over nothing" (due to overlapping layers / frame gaps),
	// don't immediately kill the dwell timer. Give it a short grace window so a user
	// who effectively stays on the icon still gets the tooltip.
	const lastPickTsRef = React.useRef(0);
	const lastPickIdRef = React.useRef(null);
	const HOVER_REACQUIRE_GRACE_MS = 500;

	const clearTimer = React.useCallback(() => {
		try {
			if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
		} catch (e) {
			// ignore
		}
	}, [hoverTimerRef]);

	const clearAll = React.useCallback(() => {
		try {
			winnerIdRef.current = null;
			candidateIdRef.current = null;
			candidateSigRef.current = null;
			setHoveredMarkerId(null);
			hoveredMarkerSigRef.current = null;
			clearTimer();
			try { activeHoverReqTokenRef.current += 1; } catch (e2) {}
			setActiveHoverId(null);
		} catch (e) {
			// ignore
		}
	}, [setHoveredMarkerId, hoveredMarkerSigRef, clearTimer, activeHoverReqTokenRef, setActiveHoverId]);

	// Clear just the UI-visible hover state, but keep refs.
	const clearUiHoverOnly = React.useCallback(() => {
		try {
			setHoveredMarkerId(null);
			hoveredMarkerSigRef.current = null;
			clearTimer();
			try { activeHoverReqTokenRef.current += 1; } catch (e2) {}
			setActiveHoverId(null);
		} catch (e) {
			// ignore
		}
	}, [setHoveredMarkerId, hoveredMarkerSigRef, clearTimer, activeHoverReqTokenRef, setActiveHoverId]);

	const hideHoverUiNoTimerCancel = React.useCallback(() => {
		// Hide UI, but intentionally DO NOT clear the timer nor bump the request token.
		// If the cursor re-acquires the same marker quickly, we want the dwell timer to
		// keep counting.
		try {
			setHoveredMarkerId(null);
			hoveredMarkerSigRef.current = null;
			setActiveHoverId(null);
		} catch (e) {
			// ignore
		}
	}, [setHoveredMarkerId, hoveredMarkerSigRef, setActiveHoverId]);

	useMapEvents({
		mousemove: (e) => {
			try {
				const nowTs = Date.now();
				const latlng = e && e.latlng ? e.latlng : null;
				const markers = Array.isArray(filteredMarkers) ? filteredMarkers : [];
				const prevWinnerId = winnerIdRef.current;
				const prevWinner = prevWinnerId ? markers.find((m) => m && m.id === prevWinnerId) : null;

				if (debugHoverFlow) {
					console.debug('[hoverFlow] mousemove', {
						mouse: latlng ? { lat: latlng.lat, lng: latlng.lng } : null,
						markers: markers.length,
						prevWinnerId,
					});
				}

				// If we have a winner and we're still within “leave”, keep it.
				if (prevWinner) {
					const prevD2 = _markerMouseD2({ map, latlng, marker: prevWinner });
					if (prevD2 != null && prevD2 <= leaveRadiusPx * leaveRadiusPx) {
						if (debugHoverFlow) console.debug('[hoverFlow] keep winner (within leave radius)', { prevWinnerId, prevD2, leaveRadiusPx });
						return;
					}
				}

				// Pick a new candidate within the tighter enter radius.
				const picked = pickNearestVehicleMarker({ markers, map, latlng, radiusPx: enterRadiusPx });
				if (debugHoverFlow) console.debug('[hoverFlow] picked', picked ? { id: picked.id, type: picked.type } : null);
				if (picked) {
					lastPickTsRef.current = nowTs;
					lastPickIdRef.current = picked.id;
				}

				// If we left the winner radius and there is no new candidate, hide.
				if (!picked && prevWinnerId) {
					if (debugHoverFlow) console.debug('[hoverFlow] left winner, no new pick -> clear winner', { prevWinnerId });
					winnerIdRef.current = null;
					candidateIdRef.current = null;
					candidateSigRef.current = null;
					setHoveredMarkerId(null);
					setActiveHoverId(null);
				}
				if (!picked) {
					// Nothing under cursor.
					// If we very recently picked the same candidate, treat this as a brief "blocked"
					// frame and keep the dwell timer running.
					const graceOk = (nowTs - (lastPickTsRef.current || 0)) <= HOVER_REACQUIRE_GRACE_MS;
					if (graceOk && candidateIdRef.current && lastPickIdRef.current === candidateIdRef.current && hoverTimerRef.current) {
						if (debugHoverFlow) console.debug('[hoverFlow] nothing picked -> grace (keep timer)', { candidateId: candidateIdRef.current });
						hideHoverUiNoTimerCancel();
						return;
					}
					// Otherwise hide and cancel intent.
					if (debugHoverFlow) console.debug('[hoverFlow] nothing picked -> clearUiHoverOnly');
					clearUiHoverOnly();
					return;
				}

				const nextId = picked.id;
				const nextSig = makeMarkerSignature(picked);
				const prevCandidateId = candidateIdRef.current;
				const prevCandidateSig = candidateSigRef.current;
				if (prevCandidateId === nextId && prevCandidateSig === nextSig) {
					// Candidate unchanged; keep waiting for timer.
					if (debugHoverFlow) console.debug('[hoverFlow] candidate unchanged', { nextId });
					return;
				}

				// New candidate: arm 0.7s timer.
				if (debugHoverFlow) console.debug('[hoverFlow] new candidate -> setHovered + arm timer', { nextId });
				candidateIdRef.current = nextId;
				candidateSigRef.current = nextSig;
				setHoveredMarkerId(nextId);
				hoveredMarkerSigRef.current = nextSig;
				clearTimer();
				const tokenAtStart = ++activeHoverReqTokenRef.current;
				hoverTimerRef.current = setTimeout(() => {
					try {
						if (activeHoverReqTokenRef.current !== tokenAtStart) return;
						if (candidateIdRef.current !== nextId) return;
						if (candidateSigRef.current !== nextSig) return;
						winnerIdRef.current = nextId;
						if (debugHoverFlow) console.debug('[hoverFlow] timer fired -> setActiveHoverId', { nextId });
						setActiveHoverId(nextId);
					} catch (e3) {
						// ignore
					}
				}, 700);
			} catch (e2) {
				// ignore
			}
		},
		mouseout: () => {
			try {
				if (debugHoverFlow) console.debug('[hoverFlow] mouseout -> clearAll');
			} catch (e) {
				// ignore
			}
			clearAll();
		},
	});

	return null;
}
	const countdownTotal = Math.max(1, Math.round(busRefreshInterval / 1000));
	const ringValue = Math.round((busCountdown / countdownTotal) * 100);

	const { activeRoutes, toggleRoute, isActive, clearRoutes } = useRouteLine();

	// Selected vehicle track overlay shown when user clicks a live vehicle marker.
	const [selectedVehicleTrack, setSelectedVehicleTrack] = React.useState(null);
	const [loadingPopupId, setLoadingPopupId] = React.useState(null);
	const [loadingError, setLoadingError] = React.useState(null);
	const [vehicleTrackRenderKey, setVehicleTrackRenderKey] = React.useState(0);
	// Used to avoid stale async responses overwriting newer selections.
	const selectedVehicleReqTokenRef = React.useRef(0);
	// Cooldown to avoid rapid repeated clicks triggering overlapping async selection flows.
	// IMPORTANT: this is per-vehicle, not global — so clicking different buses
	// after a refresh doesn't get “blocked” by a prior click.
	// Map: vehicleId -> timestamp (ms) until which clicks for that vehicle are ignored.
	const busClickCooldownByIdRef = React.useRef(new Map());

	// Debugging: log when showRouteLines changes and when we clear routes
	React.useEffect(() => {
		try {
			console.debug('[MapViewMap] showRouteLines=', showRouteLines, 'activeRoutes.size=', activeRoutes?.size);
		} catch (e) {
			// ignore
		}
		if (!showRouteLines && activeRoutes && activeRoutes.size > 0) {
			console.debug('[MapViewMap] showRouteLines false → clearing active routes (size)', activeRoutes.size);
			clearRoutes();
		}
	}, [showRouteLines, clearRoutes, activeRoutes]);

	// Single source of truth for clearing the selected vehicle track.
	// IMPORTANT: always bump the token so any in-flight async work can't re-apply.
	const clearSelectedVehicle = React.useCallback(() => {
		try { selectedVehicleReqTokenRef.current += 1; } catch (e) { /* ignore */ }
		try { setSelectedVehicleTrack(null); } catch (e) { /* ignore */ }
		try { setLoadingPopupId(null); } catch (e) { /* ignore */ }
		try { setLoadingError(null); } catch (e) { /* ignore */ }
		// We no longer use the click popup UI for vehicles; hover tooltips are sufficient.
		// Keep this as a no-op to avoid relying on parent popup state.
	}, [onClosePopup]);

	// Clearing overlays should also clear any active hover-card selection.
	React.useEffect(() => {
		// When the user clears overlays (background click, etc.) the hovered marker
		// might still be under cursor; keep it simple and reset the active hover.
		// (Pointer move will re-arm the dwell timer if needed.)
		if (!selectedVehicleTrack) return;
		// no-op: kept for future extension
	}, [selectedVehicleTrack]);

	// Note: selectedVehicleTrack is independent of route overlays.
	// Don't clear it just because activeRoutes is empty — in normal operation
	// users may never toggle route overlays at all.
	// However, a user-initiated "clear overlays" action (background click / clear
	// button) should clear BOTH route overlays and any selected vehicle track.
	const clearOverlays = React.useCallback(() => {
		try { clearRoutes(); } catch (e) { /* ignore */ }
		clearSelectedVehicle();
	}, [clearRoutes, clearSelectedVehicle]);

	// If the underlying markers change such that the currently-selected
	// vehicle no longer exists (disappeared from the feed), hide its
	// track and any open popup/loading indicator. This keeps the UI in
	// sync with live data updates.
	React.useEffect(() => {
		try {
			const ids = new Set((filteredMarkers || []).map((m) => m && m.id));
			if (selectedVehicleTrack && !ids.has(selectedVehicleTrack.id)) {
				try { console.debug('[map] selectedVehicleTrack cleared: selected vehicle disappeared from feed', { id: selectedVehicleTrack.id }); } catch (e) { /* ignore */ }
				setSelectedVehicleTrack(null);
			}
			if (openPopupId && !ids.has(openPopupId)) {
				try { onClosePopup(); } catch (e) { /* ignore */ }
			}
			if (loadingPopupId && !ids.has(loadingPopupId)) {
				setLoadingPopupId(null);
				setLoadingError(null);
			}
		} catch (e) {
			// ignore
		}
	}, [filteredMarkers, selectedVehicleTrack, openPopupId, loadingPopupId, onClosePopup]);

	// Debug: confirm when track state is populated.
	React.useEffect(() => {
		try {
			if (!selectedVehicleTrack) return;
			const n = Array.isArray(selectedVehicleTrack.coords) ? selectedVehicleTrack.coords.length : 0;
			console.debug('[map] selectedVehicleTrack updated', { id: selectedVehicleTrack.id, coordsLen: n });
			if (n >= 2) setVehicleTrackRenderKey((k) => k + 1);
		} catch (e) {
			// ignore
		}
	}, [selectedVehicleTrack]);
	const { highContrast } = useAccessibility();

	// Debug toggle mirrored from MapViewPage: when set in localStorage under
	// SHOW_ALL_BUSES_DEBUG, allow the UI to fall back to nearest-geometry variant
	// selection for debugging. Default is false in normal operation.
	const debugShowAllBuses = (typeof window !== 'undefined' && window.localStorage && window.localStorage.getItem('SHOW_ALL_BUSES_DEBUG') === '1');
	const debugTrackClickFlow = (typeof window !== 'undefined' && window.localStorage && window.localStorage.getItem('SHOW_TRACK_DEBUG') === '1');

	// Cache routeData per-line so first-click has immediate access when possible.
	const [routeDataCache, setRouteDataCache] = React.useState({});

	// API base used for backend calls from this component.
	const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:5050').replace(/\/$/, '');

	// Prefetch route data for visible markers to avoid first-click fallback to mock.
	React.useEffect(() => {
		let mounted = true;
		const lines = new Set();
		try {
			for (const m of filteredMarkers) {
				const line = m.routeNumber || m.route;
				if (line) lines.add(String(line));
			}
		} catch (e) {}
		// Fetch each line if not already cached
		// Include current map center as a geo hint so short line names like "1"
		// resolve to the local city network rather than an arbitrary global match.
		let centerLat = null;
		let centerLon = null;
		try {
			const c = mapRef && mapRef.current ? mapRef.current.getCenter() : null;
			if (c && Number.isFinite(Number(c.lat)) && Number.isFinite(Number(c.lng))) {
				centerLat = Number(c.lat);
				centerLon = Number(c.lng);
			}
		} catch (e) {}

		for (const line of lines) {
			if (routeDataCache[line]) continue;
			fetchRouteLineWithFallback(line, { lat: centerLat, lon: centerLon }).then((data) => {
				if (!mounted) return;
				setRouteDataCache((prev) => ({ ...prev, [line]: data }));
			}).catch(() => {
				// ignore individual fetch failures
			});
		}
		return () => { mounted = false; };
	}, [filteredMarkers]);

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
				height: { xs: '55dvh', sm: 420, md: '100%' },
				minHeight: { xs: 320, sm: 420, md: 0 }
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

					{/* Debug badge: confirms whether we have a selected vehicle track at render time */}
					{(function(){
						try {
							const debug = (typeof window !== 'undefined' && window.localStorage && window.localStorage.getItem('SHOW_TRACK_DEBUG') === '1');
							if (!debug) return null;
							const n = selectedVehicleTrack && Array.isArray(selectedVehicleTrack.coords) ? selectedVehicleTrack.coords.length : 0;
							const first = (n >= 1) ? selectedVehicleTrack.coords[0] : null;
							return (
								<Box sx={{
									position: 'absolute',
									top: 12,
									left: 12,
									zIndex: 1200,
									backgroundColor: 'rgba(0,0,0,0.75)',
									color: 'white',
									padding: '6px 10px',
									borderRadius: '8px',
									fontSize: '12px',
									fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace'
								}}>
									<div>track: {selectedVehicleTrack ? 'yes' : 'no'}</div>
									<div>pts: {n}</div>
									{first ? <div>first: {Number(first[0]).toFixed(5)}, {Number(first[1]).toFixed(5)}</div> : null}
								</Box>
							);
						} catch (e) {
							return null;
						}
					})()}
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
						<HoverWinnerController
							filteredMarkers={filteredMarkers}
							setHoveredMarkerId={setHoveredMarkerId}
							hoveredMarkerSigRef={hoveredMarkerSigRef}
							hoverTimerRef={hoverTimerRef}
							activeHoverReqTokenRef={activeHoverReqTokenRef}
							setActiveHoverId={setActiveHoverId}
							enterRadiusPx={DEFAULT_HOVER_PICK_ENTER_RADIUS_PX}
							leaveRadiusPx={DEFAULT_HOVER_PICK_LEAVE_RADIUS_PX}
						/>
					{(function(){
						try {
							const n = selectedVehicleTrack && Array.isArray(selectedVehicleTrack.coords) ? selectedVehicleTrack.coords.length : 0;
							if (n >= 2) console.debug('[map] MapContainer sees selectedVehicleTrack', { id: selectedVehicleTrack.id, coordsLen: n });
						} catch (e) {}
						return null;
					})()}
					<MapController
						key={`mapctl-${selectedVehicleTrack ? (selectedVehicleTrack.id + '-' + (Array.isArray(selectedVehicleTrack.coords) ? selectedVehicleTrack.coords.length : 0)) : 'none'}`}
						onReady={onMapReady}
						onMoveEnd={onMoveEnd}
						selectedVehicleTrack={selectedVehicleTrack}
						onClearSelectedVehicleTrack={() => {
							try { clearSelectedVehicle(); } catch (e) { /* ignore */ }
						}}
					/>					<MapClickClearHandler onClear={clearOverlays} />					<TileLayer
						attribution={tileAttribution}
						url={tileUrl}
					/>				{/* Clear-routes button — floated bottom-left, only when routes are active */}

					{/* While a vehicle track is selected, fit bounds once so it's not offscreen. */}
					{selectedVehicleTrack && Array.isArray(selectedVehicleTrack.coords) && selectedVehicleTrack.coords.length >= 2 && (
						<MapFitToPolyline coords={selectedVehicleTrack.coords} />
					)}

					{/* Selected vehicle track is rendered imperatively by MapController. */}
				{showRouteLines && activeRoutes.size > 0 && (
					<Box
						component="button"
						onClick={(e) => { e.stopPropagation(); clearOverlays(); }}
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
							fontWeight: '700',
							cursor: 'pointer',
							boxShadow: '0 2px 6px rgba(0,0,0,0.15)',
						}}
					>
						✕ Clear routes ({activeRoutes.size})
					</Box>
				)}					{/* Bus stop markers — small circles visible at zoom ≥ 13 */}
					<BusStopLayer onToggleRoute={toggleRoute} isRouteActive={isActive} />
					{showRouteLines && <RouteLineLayer activeRoutes={activeRoutes} onRouteClick={toggleRoute} />}
				{/* Journey-plan route overlay */}
				{(function(){
					try { console.debug('[MapViewMap] journeyRoute segments=', Array.isArray(journeyRoute) ? journeyRoute.length : journeyRoute); } catch(e) {}
					return (showRouteLines && Array.isArray(journeyRoute) && journeyRoute.length > 0) ? <JourneyRouteLayer segments={journeyRoute} /> : null;
				})()}
				{/* Developer debug overlay removed - rely on JourneyRouteLayer smoothing and styling */}
					{filteredMarkers.map((marker) => {
									try {
										let tooltipText = '';
										if (marker.type === 'bus') {
											const dm = marker.delayMinutes;
											const isOnTime = dm == null || dm < 2;
											const mins = dm != null ? Math.round(Math.abs(dm)) : 0;
											const isMapped = isBusMappedLocal(marker);

											let statusText = marker.status || '';
											if (!isMapped) {
													statusText = 'Offline';
											} else if (isOnTime) {
													statusText = 'On time';
											} else if (marker.delayMinutes != null) {
													const base = (statusText || 'Delayed').replace(/\s*\d+(?:\.\d+)?\s*min?s?/i, '').trim() || 'Delayed';
													statusText = `${base} ${mins} min${mins !== 1 ? 's' : ''}`;
											}

											const statusIcon = !isMapped ? '?' : (isOnTime ? '✓' : '⚠');
											const statusColor = !isMapped ? '#616161' : (isOnTime ? '#2e7d32' : (dm >= 10 ? '#c62828' : '#e65100'));
											const bgColor = !isMapped ? '#f5f5f5' : (isOnTime ? '#e8f5e9' : (dm >= 10 ? '#ffebee' : '#fff3e0'));

											tooltipText = `
  <div style="font-family: inherit; display: flex; flex-direction: column; width: max-content;">
    <div style="display: flex; flex-direction: row; gap: 16px; align-items: flex-start; justify-content: space-between;">
      <div style="display: flex; flex-direction: column;">
        <div style="font-weight: 700; font-size: 14px; margin-bottom: 4px; white-space: nowrap;">${marker.name || 'Bus'}</div>
        <div style="font-size: 12px; color: #666; margin-bottom: 6px; white-space: nowrap;">🚌 Bus</div>
      </div>
      <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 8px;">
        ${marker.routeNumber != null ? `<div style="background-color: ${busIconColor(marker.delayMinutes, isBusMappedLocal(marker))}; color: white; display: inline-flex; align-items: center; padding: 2px 10px; border-radius: 10px; font-size: 13px; font-weight: 700; white-space: nowrap;">Line ${marker.routeNumber}</div>` : ''}
        <div style="display: inline-block; padding: 4px 12px; border-radius: 12px; background-color: ${bgColor}; color: ${statusColor}; font-size: 12px; font-weight: 600; white-space: nowrap;">${statusIcon} ${statusText}</div>
      </div>
    </div>
    ${marker.operator ? `<div style="font-size: 12px; color: #666; margin-top: 8px; text-align: right; white-space: nowrap;"><strong>Operator:</strong> ${marker.operator}</div>` : ''}
</div>
`;
                                                                                                } else {
											let servicesHtml = '';
											if (marker.services && Array.isArray(marker.services)) {
												servicesHtml = marker.services.map(service => {
													const isOnTime = service.status === 'On time';
													const dm = service.delayMins ?? 10;
													const bgColor = isOnTime ? '#e8f5e9' : (dm >= 10 ? '#ffebee' : '#fff3e0');
													const txtColor = isOnTime ? '#2e7d32' : (dm >= 10 ? '#c62828' : '#e65100');
													const icon = isOnTime ? '✓' : '⚠';
											
													let status = service.status;
													if(status.startsWith("Delayed ")) {
														status = status.replace("Delayed ", "");
													}
											
													return `
													<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; gap: 16px;">
														<div style="font-family: inherit; font-size: 14px;">${service.destination}</div>
														<div style="display: inline-block; padding: 4px 12px; border-radius: 12px; background-color: ${bgColor}; color: ${txtColor}; font-size: 12px; font-weight: 600; white-space: nowrap;">
															${icon} ${status}
														</div>
													</div>
													`;
												}).join('');
											}

											tooltipText = `
												<div style="font-family: inherit; min-width: 200px;">
													<div style="font-weight: 700; font-size: 14px; margin-bottom: 4px;">${marker.name || 'Train'}</div>
													<div style="font-size: 12px; color: #666; margin-bottom: 8px;">🚂 Train</div>
													${servicesHtml}
												</div>
											`;
										}

										return (
											<Marker
							key={marker.id}
						ref={(ref) => {
							try {
								const k = marker && marker.id != null ? marker.id : null;
								if (!k) return;
								const el = _leafletFromRef(ref);
								if (el) markerRefsById.current.set(k, el);
								else markerRefsById.current.delete(k);
							} catch (e) { /* ignore */ }
						}}
							position={marker.position}
												riseOnHover={false}
							icon={marker.type === 'bus'
								? createCustomIcon('bus', busIconColor(marker.delayMinutes, isBusMappedLocal(marker)), marker.routeNumber != null ? String(marker.routeNumber) : null, marker.bearing != null ? Number(marker.bearing) : null)
								: TRAIN_ICON}
							eventHandlers={{
								click: async (e) => {
									// Prevent the click from bubbling to the map which
									// would trigger MapClickClearHandler (clearing routes)
									// and potential UI state changes that can make the
									// marker disappear during selection.
									try { e && e.originalEvent && e.originalEvent.stopPropagation(); } catch (err) { /* ignore */ }

									// If the same vehicle is already selected, toggle it off
									try {
										if (selectedVehicleTrack && selectedVehicleTrack.id === marker.id) {
											clearSelectedVehicle();
											return;
										}
									} catch (e) { /* ignore */ }

									if (marker.type === 'bus') {
										// Throttle clicks for THIS bus for 1 second after a handled click.
										// This prevents overlapping async selection for double-clicks,
										// without blocking clicks on other buses.
										try {
											const now = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
											const idKey = marker && marker.id != null ? String(marker.id) : null;
											if (idKey) {
												const until = busClickCooldownByIdRef.current.get(idKey) || 0;
												if (now < until) {
													if (debugTrackClickFlow) console.debug('[trackClick] dropped by cooldown', { id: idKey, remainingMs: Math.max(0, Math.round(until - now)) });
												return;
											}
												busClickCooldownByIdRef.current.set(idKey, now + 1000);
											}
										} catch (e) {
											// If anything goes wrong with timing APIs, don't block clicks.
										}
											// If the bus is unmatched (grey), do nothing on click.
											// This avoids falling back to route tracks/mock geometry for
											// vehicles that aren't mapped to a timetable journey.
											try {
												const hasRouteInt = !!(marker && (marker.route_int ?? marker.routeInt ?? marker.meta?.route_int ?? marker.meta?.routeInt));
												if (!hasRouteInt && !isBusMappedLocal(marker)) return;
											} catch (e) {
												return;
											}
										const line = marker.routeNumber || marker.route || null;
									
										if (!line) return;

											// Increment selection token; only the latest click may update selection state.
											const selectionToken = ++selectedVehicleReqTokenRef.current;

										// Prefer cached route data when available to avoid hitting the
										// potentially expensive /routes/line endpoint on first click.
										const cached = routeDataCache[String(line)];
										const timeoutMs = 10000; // increased from 5000ms to 10000ms
										const t0 = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
										// Show a small loading popup so users see feedback while the label (or route) requests are in flight.
										try { setLoadingError(null); } catch (e) { /* ignore */ }
										try { setLoadingPopupId(marker.id); } catch (e) { /* ignore */ }

										try {
											if (cached) {
												// We have the route geometry cached — use it immediately.
												const routeData = cached;
												setRouteDataCache((prev) => ({ ...prev, [String(line)]: routeData }));
												// Proceed to selection logic below using the cached routeData.
												var __routeData_local = routeData;
													// Fetch label/geometry asynchronously (do not block display of route).
													(async (tokenAtStart) => {
													try {
														const pos = marker && Array.isArray(marker.position) ? marker.position : null;
														const controllerLabel = new AbortController();
														const tlabel = setTimeout(() => controllerLabel.abort(), 5000);
														const labelData = await fetchRouteLabel(String(line), {
															signal: controllerLabel.signal,
															timeoutMs: 5000,
															lat: pos && pos.length === 2 ? pos[0] : undefined,
															lon: pos && pos.length === 2 ? pos[1] : undefined,
														});
														clearTimeout(tlabel);
															if (selectedVehicleReqTokenRef.current !== tokenAtStart) return;
																		// Best-effort: if backend provides canonical route_ints for this line,
																		// try to fetch the in-memory route_tracks geometry and render it.
																	try {
																					const routeIntDirect = marker && (marker.route_int ?? marker.routeInt ?? marker.meta?.route_int ?? marker.meta?.routeInt) ? (marker.route_int ?? marker.routeInt ?? marker.meta?.route_int ?? marker.meta?.routeInt) : null;
																					const routeIntsFromLabel = labelData && Array.isArray(labelData.route_ints) ? labelData.route_ints : [];
																					const routeInts = [routeIntDirect, ...routeIntsFromLabel].filter((v, i, a) => v != null && a.indexOf(v) === i);
																					const routeIds = labelData && Array.isArray(labelData.route_ids) ? labelData.route_ids : [];
																		const pos = marker && Array.isArray(marker.position) ? marker.position : null;
																			try { console.debug('[map] label route_ints/route_ids (cached)', { line: String(line), routeIntsLen: routeInts.length, routeInts: routeInts.slice(0, 10), routeIdsLen: routeIds.length, routeIds: routeIds.slice(0, 10) }); } catch (e) { /* ignore */ }
																			if (((routeInts && routeInts.length) || (routeIds && routeIds.length)) && pos && pos.length === 2) {
																			// Provide a tiny non-zero segment so the endpoint has from/to coords,
																				// but rely on route_int/route_id to return route_tracks.
																			const eps = 0.0001;
																					const candidates = (routeInts && routeInts.length) ? routeInts.map((v) => ({ kind: 'route_int', value: v })) : [];
																					if (!candidates.length && routeIds && routeIds.length) {
																						candidates.push(...routeIds.map((v) => ({ kind: 'route_id', value: v })));
																					}
																					for (const cand of candidates) {
																						const raw = cand && cand.value != null ? cand.value : null;
																						const val = raw != null ? String(raw) : null;
																						if (!val) continue;
																						try { console.debug('[map] trying leg-geometry candidate (cached)', { line: String(line), kind: cand.kind, val }); } catch (e) { /* ignore */ }
																					// NOTE: This leg-geometry call is for *live/cached vehicle* overlays.
																					// Do NOT pass historical `date` / `departure_time` here; live tracks are
																					// intentionally today-only.
																					let url = `${API_BASE}/route/leg-geometry?from_lat=${encodeURIComponent(pos[0])}&from_lon=${encodeURIComponent(pos[1])}`;
																					url += `&to_lat=${encodeURIComponent(pos[0] + eps)}&to_lon=${encodeURIComponent(pos[1] + eps)}`;
																					url += `&mode=driving`;
																					url += cand.kind === 'route_int' ? `&route_int=${encodeURIComponent(val)}` : `&route_id=${encodeURIComponent(val)}`;
																					try { console.debug('[map] leg-geometry request (cached)', { line: String(line), kind: cand.kind, val, url }); } catch (e) { /* ignore */ }
																				// IMPORTANT: Don't use a short AbortController timeout here.
																				// In practice, other selection interactions can abort pending
																				// requests, and the extra controller/timeout increases the chance
																				// we self-abort before the backend responds.
																				const resp = await fetch(url);
																					if (selectedVehicleReqTokenRef.current !== tokenAtStart) {
																						try { console.debug('[map] token mismatch after fetch (cached) — stopping', { line: String(line), kind: cand.kind, val }); } catch (e) { /* ignore */ }
																					break;
																				}
																					if (!resp) {
																						try { console.debug('[map] leg-geometry no response (cached)', { line: String(line), kind: cand.kind, val }); } catch (e) { /* ignore */ }
																					continue;
																				}
																					try { console.debug('[map] leg-geometry status (cached)', { line: String(line), kind: cand.kind, val, ok: resp.ok, status: resp.status }); } catch (e) { /* ignore */ }
																				if (!resp.ok) continue;
																				let data = null;
																				let norm = null;
																				try {
																					data = await resp.json();
																					// Defensive: normalizeCoords should exist in this scope, but if refactors
																					// ever move this block, fall back to a tiny local normalizer.
																					const safeNormalizeCoords = (raw) => {
																						if (typeof normalizeCoords === 'function') return normalizeCoords(raw);
																						if (!Array.isArray(raw)) return [];
																						const out = [];
																						for (const pt of raw) {
																							if (!Array.isArray(pt) || pt.length < 2) continue;
																							const a = Number(pt[0]);
																							const b = Number(pt[1]);
																							if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
																							// Assume [lat, lon]
																							out.push([a, b]);
																						}
																						return out;
																					};
																						norm = safeNormalizeCoords(data && data.coords);
																						if (!norm || norm.length < 2) {
																							try { console.debug('[map] leg-geometry returned empty coords (cached)', { line: String(line), rid }); } catch (e) { /* ignore */ }
																							// No track returned: DO NOT fall back to stops/linear geometry.
																							continue;
																						}
																						if (data && data.source === 'linear') {
																								try { console.debug('[map] leg-geometry returned linear (cached)', { line: String(line), rid, coordsLen: norm.length }); } catch (e) { /* ignore */ }
																								continue;
																						}
																						try { console.debug('[map] leg-geometry selected route_tracks (cached)', { line: String(line), rid, coordsLen: norm.length }); } catch (e) { /* ignore */ }
																						const color = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
																						// If OSRM is up, snap the provided route_tracks geometry to roads.
																						// Best-effort only — fall back immediately to the raw coords.
																						setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: norm, color } : { id: marker.id, coords: norm, color, stops: [], label: null });
																						try {
																							const snapped = await osrmRouteCoordsLatLon(norm);
																							if (selectedVehicleReqTokenRef.current === tokenAtStart && Array.isArray(snapped) && snapped.length >= 2) {
																								setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: snapped, color } : prev);
																							}
																						} catch (e) { /* ignore */ }
																						break;
																				} catch (je) {
																					let txt = null;
																					try { txt = await resp.text(); } catch (te) { /* ignore */ }
																					try {
																						console.debug('[map] leg-geometry parse/process failed (cached)', {
																							line: String(line),
																							rid,
																							error: je && je.message ? je.message : String(je),
																							contentType: resp.headers ? resp.headers.get('content-type') : null,
																							textSample: txt ? String(txt).slice(0, 300) : null,
																						});
																					} catch (e) { /* ignore */ }
																					continue;
																				}
																				if (data && data.source === 'linear') {
																					try { console.debug('[map] leg-geometry returned linear (cached)', { line: String(line), rid, coordsLen: norm ? norm.length : 0 }); } catch (e) { /* ignore */ }
																					continue;
																				}
																			}
																		}
																	} catch (ee) {
																			// ignore geom failures
																	}
																	// Attach label to the selectedVehicleTrack if still selected.
																	// (Don't overwrite coords here; only augment.)
																	if (selectedVehicleReqTokenRef.current !== tokenAtStart) return;
																	setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, label: labelData } : prev);
													} catch (err) {
														// label fetch non-fatal — log and ignore
														 
														console.warn('[map] fetchRouteLabel failed (cached) for line=', line, err && err.message ? err.message : err);
													}
														})(selectionToken);
												// Important: in cached mode we kick off the label/geometry fetch above.
												// Don't run the synchronous variant-selection logic below, because it can
												// overwrite the just-fetched in-memory track/label overlay.
												const color = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
																		// Option A: if /bus/live already included precomputed polyline coords,
																		// render immediately and skip click-time geometry fetches.
																		try {
																			const quick = marker && (marker.track_coords || marker.meta?.track_coords);
																			if (Array.isArray(quick) && quick.length >= 2) {
																				setSelectedVehicleTrack({ id: marker.id, coords: quick, color, stops: [], label: null });
																				try { if (onOpenPopupSignature) onOpenPopupSignature(makeMarkerSignature(marker)); } catch (ee) { /* ignore */ }
																				return;
																			}
																		} catch (e) { /* ignore */ }
																		// No further fallback here.
																		// If we didn't receive explicit per-vehicle track_coords from the feed,
																		// we rely solely on /route/leg-geometry (route_tracks) flow kicked off
																		// above. If that fails, we intentionally show no track.
																		setSelectedVehicleTrack({ id: marker.id, coords: null, color, stops: [], label: null });
												try {
													try { if (onOpenPopupSignature) onOpenPopupSignature(makeMarkerSignature(marker)); } catch (ee) { /* ignore */ }
																																																												// Intentionally not opening click popup (hover tooltips only).
												} catch (e) { /* ignore */ }
												return;
											} else {
												// No cache: fetch route first (strict), then request label.
												const controller = new AbortController();
												const timeout = setTimeout(() => controller.abort(), timeoutMs);
												let routeData = null;
												try {
													routeData = await fetchRouteLineNoFallback(String(line), { signal: controller.signal, timeoutMs });
													// Cache for future clicks
													setRouteDataCache((prev) => ({ ...prev, [String(line)]: routeData }));
												} finally {
													clearTimeout(timeout);
												}
												var __routeData_local = routeData;
													// Fetch label/geometry asynchronously (non-fatal)
													(async (tokenAtStart) => {
													try {
														// IMPORTANT: Don't pass an AbortSignal for cached label fetch.
														// We've seen these get aborted in practice ("Fetch is aborted"),
														// which prevents us from ever discovering route_ids => route_tracks.
														const pos = marker && Array.isArray(marker.position) ? marker.position : null;
														const labelData = await fetchRouteLabel(String(line), {
															timeoutMs: 8000,
															lat: pos && pos.length === 2 ? pos[0] : undefined,
															lon: pos && pos.length === 2 ? pos[1] : undefined,
														});
															if (selectedVehicleReqTokenRef.current !== tokenAtStart) return;
																					// Best-effort: if backend provides canonical route_ints for this line,
																					// try to fetch the in-memory route_tracks geometry and render it.
														try {
																						const routeInts = labelData && Array.isArray(labelData.route_ints) ? labelData.route_ints : [];
																						const routeIds = labelData && Array.isArray(labelData.route_ids) ? labelData.route_ids : [];
															const pos = marker && Array.isArray(marker.position) ? marker.position : null;
																					try { console.debug('[map] label route_ints/route_ids (post-route)', { line: String(line), routeIntsLen: routeInts.length, routeInts: routeInts.slice(0, 10), routeIdsLen: routeIds.length, routeIds: routeIds.slice(0, 10) }); } catch (e) { /* ignore */ }
																					if (((routeInts && routeInts.length) || (routeIds && routeIds.length)) && pos && pos.length === 2) {
																const eps = 0.0001;
																						const candidates = (routeInts && routeInts.length) ? routeInts.map((v) => ({ kind: 'route_int', value: v })) : [];
																						if (!candidates.length && routeIds && routeIds.length) {
																							candidates.push(...routeIds.map((v) => ({ kind: 'route_id', value: v })));
																						}
																						for (const cand of candidates) {
																							const raw = cand && cand.value != null ? cand.value : null;
																							const val = raw != null ? String(raw) : null;
																							if (!val) continue;
																							try { console.debug('[map] trying leg-geometry candidate (post-route)', { line: String(line), kind: cand.kind, val }); } catch (e) { /* ignore */ }
																							let url = `${API_BASE}/route/leg-geometry?from_lat=${encodeURIComponent(pos[0])}&from_lon=${encodeURIComponent(pos[1])}`;
																							url += `&to_lat=${encodeURIComponent(pos[0] + eps)}&to_lon=${encodeURIComponent(pos[1] + eps)}`;
																							url += `&mode=driving`;
																							url += cand.kind === 'route_int' ? `&route_int=${encodeURIComponent(val)}` : `&route_id=${encodeURIComponent(val)}`;
																							try { console.debug('[map] leg-geometry request (post-route)', { line: String(line), kind: cand.kind, val, url }); } catch (e) { /* ignore */ }
																	const controllerGeom = new AbortController();
																	const tgeom = setTimeout(() => controllerGeom.abort(), 5000);
																	const resp = await fetch(url, { signal: controllerGeom.signal });
																	clearTimeout(tgeom);
																	if (selectedVehicleReqTokenRef.current !== tokenAtStart) {
																		try { console.debug('[map] token mismatch after fetch (post-route) — stopping', { line: String(line), rid }); } catch (e) { /* ignore */ }
																		break;
																	}
																	if (!resp) {
																		try { console.debug('[map] leg-geometry no response (post-route)', { line: String(line), rid }); } catch (e) { /* ignore */ }
																		continue;
																	}
																	try { console.debug('[map] leg-geometry status (post-route)', { line: String(line), rid, ok: resp.ok, status: resp.status }); } catch (e) { /* ignore */ }
																	if (!resp.ok) continue;
																	let data = null;
																	let norm = null;
																	try {
																		data = await resp.json();
																		const safeNormalizeCoords = (raw) => {
																			if (typeof normalizeCoords === 'function') return normalizeCoords(raw);
																			if (!Array.isArray(raw)) return [];
																			const out = [];
																			for (const pt of raw) {
																				if (!Array.isArray(pt) || pt.length < 2) continue;
																				const a = Number(pt[0]);
																				const b = Number(pt[1]);
																				if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
																				out.push([a, b]);
																			}
																			return out;
																		};
																				norm = safeNormalizeCoords(data && data.coords);
																				if (!norm || norm.length < 2) {
																					try { console.debug('[map] leg-geometry returned empty coords (post-route)', { line: String(line), rid }); } catch (e) { /* ignore */ }
																					// No track returned: do NOT fall back to stops/linear geometry.
																					continue;
																				}
																				if (data && data.source === 'linear') {
																						try { console.debug('[map] leg-geometry returned linear (post-route)', { line: String(line), rid, coordsLen: norm.length }); } catch (e) { /* ignore */ }
																						continue;
																				}
																				try { console.debug('[map] leg-geometry selected route_tracks (post-route)', { line: String(line), rid, coordsLen: norm.length }); } catch (e) { /* ignore */ }
																				// Hacky circular-route workaround:
																				// Some circular lines (e.g. 6B) effectively have the same start/end stop.
																				// The backend may return only one variant/arc per route_id, so stitch
																				// multiple route_ids together when available.
																				let stitched = norm;
																				try {
																					if (Array.isArray(routeIds) && routeIds.length >= 2 && pos && pos.length === 2) {
																						const primaryRid = (cand.kind === 'route_id') ? val : (routeIds && routeIds.length ? String(routeIds[0]) : null);
																						if (primaryRid) {
																							// Cap extra fetches so a pathological label doesn't spam requests.
																							const maxExtras = 4;
																							const extras = routeIds
																								.map((v) => String(v))
																								.filter((v) => v && v !== primaryRid)
																								.slice(0, maxExtras);

																							const safeNormalizeCoords2 = (raw) => {
																									if (typeof normalizeCoords === 'function') return normalizeCoords(raw);
																									if (!Array.isArray(raw)) return [];
																									const out = [];
																									for (const pt of raw) {
																										if (!Array.isArray(pt) || pt.length < 2) continue;
																										const a = Number(pt[0]);
																										const b = Number(pt[1]);
																										if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
																										out.push([a, b]);
																									}
																								return out;
																							};

																							const joinIfClose = (aIn, bIn) => {
																								const a = Array.isArray(aIn) ? aIn : [];
																								const b = Array.isArray(bIn) ? bIn : [];
																								if (a.length < 2 || b.length < 2) return null;
																								const lastA = a[a.length - 1];
																								const firstB = b[0];
																								const drop = lastA && firstB && lastA[0] === firstB[0] && lastA[1] === firstB[1];
																								return drop ? a.concat(b.slice(1)) : a.concat(b);
																							};

																							for (const other of extras) {
																								let url2 = `${API_BASE}/route/leg-geometry?from_lat=${encodeURIComponent(pos[0])}&from_lon=${encodeURIComponent(pos[1])}`;
																								url2 += `&to_lat=${encodeURIComponent(pos[0] + eps)}&to_lon=${encodeURIComponent(pos[1] + eps)}`;
																								url2 += `&mode=driving&route_id=${encodeURIComponent(other)}`;
																								const resp2 = await fetch(url2);
																								if (!resp2 || !resp2.ok) continue;
																								const data2 = await resp2.json();
																								const norm2 = safeNormalizeCoords2(data2 && data2.coords);
																								if (!(data2 && data2.source === 'route_tracks' && Array.isArray(norm2) && norm2.length >= 2)) continue;

																								// Choose orientation that best connects: append norm2, or append reversed norm2.
																								const a = stitched;
																								const b = norm2;
																								const bRev = b.slice().reverse();
																								const joinedFwd = joinIfClose(a, b);
																								const joinedRev = joinIfClose(a, bRev);
																								// If both possible, prefer the one that yields a longer path (usually means better continuity).
																								if (joinedFwd && joinedRev) {
																									stitched = joinedRev.length > joinedFwd.length ? joinedRev : joinedFwd;
																								} else if (joinedFwd) {
																									stitched = joinedFwd;
																								} else if (joinedRev) {
																									stitched = joinedRev;
																								} else {
																									// Last resort: just concatenate (better than dropping a segment).
																									stitched = a.concat(b);
																								}

																								try { console.debug('[map] circular stitch applied (post-route)', { line: String(line), route_id_a: primaryRid, route_id_b: other, coordsLen: stitched.length }); } catch (e) { /* ignore */ }
																							}
																						}
																				}
																			} catch (e) {
																			/* ignore */
																		}
																				const color = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
																				setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: stitched, color } : { id: marker.id, coords: stitched, color, stops: [], label: null });
																				break;
																	} catch (je) {
																		let txt = null;
																		try { txt = await resp.text(); } catch (te) { /* ignore */ }
																		try {
																			console.debug('[map] leg-geometry parse/process failed (post-route)', {
																				line: String(line),
																				rid,
																				error: je && je.message ? je.message : String(je),
																				contentType: resp.headers ? resp.headers.get('content-type') : null,
																				textSample: txt ? String(txt).slice(0, 300) : null,
																			});
																		} catch (e) { /* ignore */ }
																		continue;
																	}
																	if (data && data.source === 'linear') {
																		try { console.debug('[map] leg-geometry returned linear (post-route)', { line: String(line), rid, coordsLen: norm ? norm.length : 0 }); } catch (e) { /* ignore */ }
																		continue;
																	}
																			if (data && data.source === 'route_tracks' && norm && norm.length >= 2) {
																				try { console.debug('[map] leg-geometry selected route_tracks (post-route)', { line: String(line), rid, coordsLen: norm.length }); } catch (e) { /* ignore */ }
																				const color = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
																				setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: norm, color } : { id: marker.id, coords: norm, color, stops: [], label: null });
																				break;
																			}
																}
															}
														} catch (ee) {
															// ignore geometry failures
														}
														if (selectedVehicleReqTokenRef.current !== tokenAtStart) return;
														setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, label: labelData } : prev);
													} catch (err) {
														// non-fatal label fetch
														 
														console.warn('[map] fetchRouteLabel failed (post-route) for line=', line, err && err.message ? err.message : err);
													}
													})(selectionToken);
											}

											// IMPORTANT: From here on, do not run any synchronous
											// variant-selection / stop-based geometry logic. Those
											// paths can overwrite a valid route_tracks polyline once
											// it arrives, or cause us to accept fallback geometry.
											// We only want the /route/leg-geometry route_id flow to
											// populate coords.
											const _vehicleTrackColor = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
											setSelectedVehicleTrack({ id: marker.id, coords: null, color: _vehicleTrackColor, stops: [], label: null });
											try {
												try { if (onOpenPopupSignature) onOpenPopupSignature(makeMarkerSignature(marker)); } catch (ee) { /* ignore */ }
																																																								// Intentionally not opening click popup (hover tooltips only).
											} catch (e) { /* ignore */ }
											return;
								
											// After either path above, selection logic will run using __routeData_local

											// Use the route data populated above (from cache or fetch)
											const routeData = __routeData_local;

												if (!routeData || !Array.isArray(routeData.variants) || routeData.variants.length === 0) {
												setSelectedVehicleTrack(null);
												return;
											}

											// Now proceed with the existing selection logic (prefer meta mapping,
											// then optionally nearest-geometry when debugShowAllBuses is enabled).
											let metaRouteId = null;
											if (marker.meta) {
												metaRouteId = marker.meta.route_id || marker.meta.routeId || marker.meta.route || marker.meta.logged_journey_id || marker.meta.journey_id || marker.meta.journeyId || null;
											}

											const pos = marker.position;

											if (metaRouteId) {
												// Production policy: frontends MUST use the backend's
												// file-prefixed canonical route_id (contains '::').
												// Only perform an exact equality match here to avoid
												// accidentally matching suffix-only or legacy ids.
												const metaStr = String(metaRouteId);
												const match = routeData.variants.find(v => {
													if (!v || v.route_id == null) return false;
													const vr = String(v.route_id);
													return vr === metaStr;
												});
												if (match) {
													let geom = Array.isArray(match.geometry) ? match.geometry : stopsToLatLngs(match.stops);
													const stops = Array.isArray(match.stops) ? stopsToLatLngs(match.stops) : [];
													if (Array.isArray(geom) && geom.length >= 2) {
														const norm = geom.map((pt) => ([Number(pt[0]), Number(pt[1])]));
														const color = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
														// Label will be attached asynchronously when/if the fetch completes.
																setSelectedVehicleTrack({ id: marker.id, coords: norm, color, stops: [], label: null });
																try {
																	const snapped = await osrmRouteCoordsLatLon(norm);
																	if (selectedVehicleReqTokenRef.current === selectionToken && Array.isArray(snapped) && snapped.length >= 2) {
																		setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: snapped } : prev);
																	}
																} catch (e) { /* ignore */ }
														try { 
															// Notify parent of the popup open and its signature (optional)
															try { if (onOpenPopupSignature) onOpenPopupSignature(makeMarkerSignature(marker)); } catch (ee) { /* ignore */ }
																																																																																																						// Intentionally not opening click popup (hover tooltips only).
														} catch (e) { /* ignore */ }
														return;
													}
													if (stops.length >= 2) {
														const norm = stops.map((pt) => ([Number(pt[0]), Number(pt[1])]));
														const color = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
																setSelectedVehicleTrack({ id: marker.id, coords: norm, color, stops: [], label: null });
																try {
																	const snapped = await osrmRouteCoordsLatLon(norm);
																	if (selectedVehicleReqTokenRef.current === selectionToken && Array.isArray(snapped) && snapped.length >= 2) {
																		setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: snapped } : prev);
																	}
																} catch (e) { /* ignore */ }
														try { 
															try { if (onOpenPopupSignature) onOpenPopupSignature(makeMarkerSignature(marker)); } catch (ee) { /* ignore */ }
																																																																																																						// Intentionally not opening click popup (hover tooltips only).
														} catch (e) { /* ignore */ }
														return;
													}
												}
											}

											let best = null;
											if (metaRouteId || debugShowAllBuses) {
												for (const variant of routeData.variants) {
													let geom = Array.isArray(variant.geometry) ? variant.geometry : stopsToLatLngs(variant.stops);
													if (!Array.isArray(geom) || geom.length === 0) continue;
													const norm = geom.map((pt) => ([Number(pt[0]), Number(pt[1])]));
													let minD = Infinity;
													for (const p of norm) {
														const dlat = p[0] - pos[0];
														const dlon = p[1] - pos[1];
														const d2 = dlat * dlat + dlon * dlon;
														if (d2 < minD) minD = d2;
													}
													if (best == null || minD < best.minD) {
														best = { variant, norm, minD };
													}
												}

												if (!best) {
													setSelectedVehicleTrack(null);
													return;
												}
											} else {
												setSelectedVehicleTrack(null);
												return;
											}

											const color = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
											const stops = Array.isArray(best.variant && best.variant.stops) ? stopsToLatLngs(best.variant.stops) : [];
															setSelectedVehicleTrack({ id: marker.id, coords: best.norm, color, stops, label: null });
															try {
																const snapped = await osrmRouteCoordsLatLon(best.norm);
																if (selectedVehicleReqTokenRef.current === selectionToken && Array.isArray(snapped) && snapped.length >= 2) {
																	setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: snapped } : prev);
																}
															} catch (e) { /* ignore */ }
											try { 
												try { if (onOpenPopupSignature) onOpenPopupSignature(makeMarkerSignature(marker)); } catch (ee) { /* ignore */ }
																																																																																																						// Intentionally not opening click popup (hover tooltips only).
											} catch (e) { /* ignore */ }
										} catch (e) {
											// If either request failed or timed out, ensure no partial UI is shown.
											setSelectedVehicleTrack(null);
											try {
												const t1 = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
												 
												console.warn(`[map] route/label fetch failed for line=${line} marker=${marker.id}:`, e, `took=${Math.round(t1-t0)}ms`);
												setLoadingError({ id: marker.id, message: (e && e.message) ? e.message : String(e) });
											} catch (ee) { /* ignore */ }
										} finally {
											try { setLoadingPopupId(null); } catch (e) { /* ignore */ }
										}
									}
								}
							}}
						>
							{/* Show popup when openPopupId matches (successful fetch) or when loadingPopupId matches (in-progress) */}
							{(openPopupId === marker.id || loadingPopupId === marker.id) && (
								<AdaptivePopup marker={marker} onClose={onClosePopup} autoPan={false}>
									<Box sx={{ minWidth: '200px', pb: 1 }}>
										<Typography variant="subtitle2" fontWeight={700} sx={{ mb: 0.5 }}>
											{marker.name}
										</Typography>
										{loadingPopupId === marker.id && (
											<Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
												<CircularProgress size={18} />
												<Typography variant="body2">Loading route…</Typography>
											</Box>
										)}
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
												backgroundColor: busIconColor(marker.delayMinutes, isBusMappedLocal(marker)),
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
										{/* Bearing value intentionally hidden from popup to avoid clutter; icon shows heading visually */}
										{marker.meta && Object.keys(marker.meta).length > 0 && (
											<Box sx={{ mt: 1 }}>
												{Object.entries(marker.meta)
													.filter(([k]) => {
														const kk = String(k).toLowerCase();
															// Exclude coordinate/operator/delay/status, identifier fields and any bearing-like fields
															return ![
																'lat', 'lon', 'latitude', 'longitude',
																'operator', 'operator_name', 'operatorname', 'operator_ref', 'operatorref', 'operatorname',
																'delay_minutes', 'delayminutes', 'status',
																'bearing', 'bearing_degrees', 'bearingdegrees', 'heading', 'course',
																// Administrative/identifier fields that should not be shown in the popup
																'logged_journey_id', 'loggedjourneyid', 'journey_id', 'journeyid',
																'framed_journey_ref', 'framedjourneyref', 'dated_journey_ref', 'datedjourneyref',
																'vehicle_journey_code', 'vehiclejourneycode', 'vehicle_ref', 'vehicleref',
																// Origin/departure and ATCO fields (noisy for popup labels)
																'origin_dep_secs', 'origindepsecs', 'origin_dep', 'origindep',
																'origin_atco', 'originatco', 'destination_atco', 'destinationatco'
															].includes(kk);
													})
													.map(([key, value]) => (
														<Typography key={key} variant="body2" sx={{ mb: 0.5 }}>
															<strong>{key.replace(/_/g, ' ')}:</strong> {String(value)}
														</Typography>
													))}

													{/* Selected vehicle track overlay (shown when user clicks a live vehicle) */}
													{selectedVehicleTrack && Array.isArray(selectedVehicleTrack.coords) && selectedVehicleTrack.coords.length >= 2 && (
														<>
															{/* Cyan underlay/frame so the vehicle track has a cyan outline */}
															<Polyline
																pane="routePane"
																positions={selectedVehicleTrack.coords}
																pathOptions={{ color: '#00ffff', weight: 6, opacity: 0.95, lineCap: 'round', lineJoin: 'round' }}
																eventHandlers={{
																	click: () => {
																		try { setSelectedVehicleTrack(null); } catch (e) { /* ignore */ }
																		try { onClosePopup(); } catch (e) { /* ignore */ }
																	}
																}}
															/>
															{/* Main coloured track */}
															<Polyline
																pane="routePane"
																positions={selectedVehicleTrack.coords}
																pathOptions={{ color: selectedVehicleTrack.color || '#1a73e8', weight: 4, opacity: 1, lineCap: 'round', lineJoin: 'round' }}
																eventHandlers={{
																	click: () => {
																		try { setSelectedVehicleTrack(null); } catch (e) { /* ignore */ }
																		try { onClosePopup(); } catch (e) { /* ignore */ }
																	}
																}}
															/>
															{/* Render stop markers snapped to the displayed polyline so they lie exactly on the track */}
															{Array.isArray(selectedVehicleTrack.stops) && selectedVehicleTrack.stops.length > 0 && selectedVehicleTrack.coords.length >= 2 && (
																selectedVehicleTrack.stops.map((s, si) => {
																	try {
																		const snapped = _nearestPointOnPolyline(selectedVehicleTrack.coords, s);
																		if (!snapped) return null;
																		return (
																			<CircleMarker
																				key={`stop-${si}`}
																				center={snapped}
																				radius={4}
																				pathOptions={{ color: '#ffffff', weight: 2, fillColor: selectedVehicleTrack.color || '#1a73e8', fillOpacity: 1 }}
																			/>
																		);
																	} catch (e) {
																		return null;
																	}
																})
															)}
														</>
													)}
											</Box>
										)}
									</Box>
								</AdaptivePopup>
							)}
													{/**
													 * Leaflet tooltips can occasionally get "stuck" on screen if their internal
													 * open/close lifecycle gets out of sync with React renders (common with dense,
													 * overlapping markers).
													 *
													 * To make this robust:
													 * - Gate tooltip visibility on `activeHoverId` (1s dwell winner).
													 * - Key the Tooltip so a winner change forces a full Leaflet unmount/remount.
													 * - Disable interactivity so it can't "capture" the pointer and prevent
													 *   synthetic leave events.
													 */}
																{(activeHoverId === marker.id || hoveredMarkerId === marker.id) && (
														<Tooltip
																		key={`veh-tt-${String(marker.id)}-${String(activeHoverId ?? hoveredMarkerId ?? '')}`}
																		pane="vehicleTooltipPane"
															direction="top"
															offset={[0, -15]}
															className="custom-vehicle-tooltip"
															interactive={false}
															permanent={false}
															sticky={true}
															opacity={1}
														>
															<div dangerouslySetInnerHTML={{ __html: tooltipText }} />
														</Tooltip>
													)}
												</Marker>
										);
									} catch (err) {
											return (
												<Marker
							key={marker.id}
							position={marker.position}
													zIndexOffset={(() => {
														// Deterministic stacking to reduce "two markers hovered" in dense areas.
														// Higher zIndexOffset wins. Buses first, then trains, then stable by id.
														try {
															const base = marker.type === 'bus' ? 2000 : 1000;
															const idStr = String(marker.id ?? '');
															let h = 0;
															for (let i = 0; i < idStr.length; i++) h = ((h << 5) - h) + idStr.charCodeAt(i);
															return base + (h % 300);
														} catch (e) {
															return marker.type === 'bus' ? 2000 : 1000;
														}
													})()}
												riseOnHover={false}
							icon={marker.type === 'bus'
							? createCustomIcon('bus', busIconColor(marker.delayMinutes, isBusMappedLocal(marker)), marker.routeNumber != null ? String(marker.routeNumber) : null, marker.bearing != null ? Number(marker.bearing) : null)
								: TRAIN_ICON} eventHandlers={{
											// Hover intent is driven at map-level by <HoverWinnerController />.
											// Marker mouseover/mouseout are unreliable in dense overlapping icons.
													click: async (e) => {
									// Prevent the click from bubbling to the map which
									// would trigger MapClickClearHandler (clearing routes)
									// and potential UI state changes that can make the
									// marker disappear during selection.
									try { e && e.originalEvent && e.originalEvent.stopPropagation(); } catch (err) { /* ignore */ }

									// If the same vehicle is already selected, toggle it off
									try {
										if (selectedVehicleTrack && selectedVehicleTrack.id === marker.id) {
											clearSelectedVehicle();
											return;
										}
									} catch (e) { /* ignore */ }

									if (marker.type === 'bus') {
										const line = marker.routeNumber || marker.route || null;
									
										if (!line) return;

											// Increment selection token; only the latest click may update selection state.
											const selectionToken = ++selectedVehicleReqTokenRef.current;

										// Prefer cached route data when available to avoid hitting the
										// potentially expensive /routes/line endpoint on first click.
										const cached = routeDataCache[String(line)];
										const timeoutMs = 10000; // increased from 5000ms to 10000ms
										const t0 = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
										// Show a small loading popup so users see feedback while the label (or route) requests are in flight.
										try { setLoadingError(null); } catch (e) { /* ignore */ }
										try { setLoadingPopupId(marker.id); } catch (e) { /* ignore */ }

										try {
											if (cached) {
												// We have the route geometry cached — use it immediately.
												const routeData = cached;
												setRouteDataCache((prev) => ({ ...prev, [String(line)]: routeData }));
												// Proceed to selection logic below using the cached routeData.
												var __routeData_local = routeData;
													// Fetch label/geometry asynchronously (do not block display of route).
													(async (tokenAtStart) => {
													try {
														const controllerLabel = new AbortController();
														const tlabel = setTimeout(() => controllerLabel.abort(), 5000);
														const labelData = await fetchRouteLabel(String(line), { signal: controllerLabel.signal, timeoutMs: 5000 });
														clearTimeout(tlabel);
															if (selectedVehicleReqTokenRef.current !== tokenAtStart) return;
																	// Best-effort: if backend provides canonical route_ints for this line,
																	// try to fetch the in-memory route_tracks geometry and render it.
																	try {
																		const routeInts = labelData && Array.isArray(labelData.route_ints) ? labelData.route_ints : [];
																		const routeIds = labelData && Array.isArray(labelData.route_ids) ? labelData.route_ids : [];
																		const pos = marker && Array.isArray(marker.position) ? marker.position : null;
																		try { console.debug('[map] label route_ints/route_ids (cached)', { line: String(line), routeIntsLen: routeInts.length, routeInts: routeInts.slice(0, 10), routeIdsLen: routeIds.length, routeIds: routeIds.slice(0, 10) }); } catch (e) { /* ignore */ }
																		const candidates = (routeInts && routeInts.length) ? routeInts.map((v) => ({ kind: 'route_int', value: v })) : [];
																		if (!candidates.length && routeIds && routeIds.length) {
																			candidates.push(...routeIds.map((v) => ({ kind: 'route_id', value: v })));
																		}
																		if (candidates.length && pos && pos.length === 2) {
																			// Provide a tiny non-zero segment so the endpoint has from/to coords,
																			// but rely on route_int/route_id to return route_tracks.
																			const eps = 0.0001;
																			for (const cand of candidates) {
																				const raw = cand && cand.value != null ? cand.value : null;
																				const val = raw != null ? String(raw) : null;
																				if (!val) continue;
																				try { console.debug('[map] trying leg-geometry candidate (cached)', { line: String(line), kind: cand.kind, val }); } catch (e) { /* ignore */ }
																				let url = `${API_BASE}/route/leg-geometry?from_lat=${encodeURIComponent(pos[0])}&from_lon=${encodeURIComponent(pos[1])}`;
																				url += `&to_lat=${encodeURIComponent(pos[0] + eps)}&to_lon=${encodeURIComponent(pos[1] + eps)}`;
																				url += `&mode=driving`;
																				url += cand.kind === 'route_int' ? `&route_int=${encodeURIComponent(val)}` : `&route_id=${encodeURIComponent(val)}`;
																				try { console.debug('[map] leg-geometry request (cached)', { line: String(line), kind: cand.kind, val, url }); } catch (e) { /* ignore */ }
																				const controllerGeom = new AbortController();
																				const tgeom = setTimeout(() => controllerGeom.abort(), 5000);
																				const resp = await fetch(url, { signal: controllerGeom.signal });
																				clearTimeout(tgeom);
																				if (selectedVehicleReqTokenRef.current !== tokenAtStart) {
																					try { console.debug('[map] token mismatch after fetch (cached) — stopping', { line: String(line), kind: cand.kind, val }); } catch (e) { /* ignore */ }
																					break;
																				}
																				if (!resp) {
																					try { console.debug('[map] leg-geometry no response (cached)', { line: String(line), kind: cand.kind, val }); } catch (e) { /* ignore */ }
																					continue;
																				}
																				try { console.debug('[map] leg-geometry status (cached)', { line: String(line), kind: cand.kind, val, ok: resp.ok, status: resp.status }); } catch (e) { /* ignore */ }
																				if (!resp.ok) continue;
																				let data = null;
																				let norm = null;
																				try {
																					data = await resp.json();
																					// Defensive: normalizeCoords should exist in this scope, but if refactors
																					// ever move this block, fall back to a tiny local normalizer.
																					const safeNormalizeCoords = (raw) => {
																						if (typeof normalizeCoords === 'function') return normalizeCoords(raw);
																						if (!Array.isArray(raw)) return [];
																						const out = [];
																						for (const pt of raw) {
																							if (!Array.isArray(pt) || pt.length < 2) continue;
																							const a = Number(pt[0]);
																							const b = Number(pt[1]);
																							if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
																							// Assume [lat, lon]
																							out.push([a, b]);
																						}
																						return out;
																					};
																					norm = safeNormalizeCoords(data && data.coords);
																					try { console.debug('[map] leg-geometry selected route_tracks (cached)', { line: String(line), kind: cand.kind, val, coordsLen: norm.length }); } catch (e) { /* ignore */ }
																					const color = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
																					setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: norm, color } : { id: marker.id, coords: norm, color, stops: [], label: null });
																					break;
																				} catch (je) {
																					let txt = null;
																					try { txt = await resp.text(); } catch (te) { /* ignore */ }
																					try {
																						console.debug('[map] leg-geometry parse/process failed (cached)', {
																							line: String(line),
																							kind: cand ? cand.kind : null,
																							val,
																							error: je && je.message ? je.message : String(je),
																							contentType: resp.headers ? resp.headers.get('content-type') : null,
																							textSample: txt ? String(txt).slice(0, 300) : null,
																						});
																					} catch (e) { /* ignore */ }
																					continue;
																				}
																				if (data && data.source === 'linear') {
																				try { console.debug('[map] leg-geometry returned linear (cached)', { line: String(line), kind: cand ? cand.kind : null, val, coordsLen: norm ? norm.length : 0 }); } catch (e) { /* ignore */ }
																					continue;
																				}
																			}
																		}
																	} catch (ee) {
																			// ignore geom failures
																	}
																	// Attach label to the selectedVehicleTrack if still selected.
																	// (Don't overwrite coords here; only augment.)
																	if (selectedVehicleReqTokenRef.current !== tokenAtStart) return;
																	setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, label: labelData } : prev);
													} catch (err) {
														// label fetch non-fatal — log and ignore
														 
														console.warn('[map] fetchRouteLabel failed (cached) for line=', line, err && err.message ? err.message : err);
													}
														})(selectionToken);
												// Important: in cached mode we kick off the label/geometry fetch above.
												// Don't run the synchronous variant-selection logic below, because it can
												// overwrite the just-fetched in-memory track/label overlay.
												const color = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
												// Don't set coords to [] here — wait for route_tracks fetch to succeed.
												setSelectedVehicleTrack({ id: marker.id, coords: null, color, stops: [], label: null });
												try {
													try { if (onOpenPopupSignature) onOpenPopupSignature(makeMarkerSignature(marker)); } catch (ee) { /* ignore */ }
													onOpenPopup(marker.id);
												} catch (e) { /* ignore */ }
												return;
											} else {
												// No cache: fetch route first (strict), then request label.
												const controller = new AbortController();
												const timeout = setTimeout(() => controller.abort(), timeoutMs);
												let routeData = null;
												try {
													routeData = await fetchRouteLineNoFallback(String(line), { signal: controller.signal, timeoutMs });
													// Cache for future clicks
													setRouteDataCache((prev) => ({ ...prev, [String(line)]: routeData }));
												} finally {
													clearTimeout(timeout);
												}
												var __routeData_local = routeData;
													// Fetch label/geometry asynchronously (non-fatal)
													(async (tokenAtStart) => {
													try {
														const controllerLabel = new AbortController();
														const tlabel = setTimeout(() => controllerLabel.abort(), 5000);
														const labelData = await fetchRouteLabel(String(line), { signal: controllerLabel.signal, timeoutMs: 5000 });
														clearTimeout(tlabel);
															if (selectedVehicleReqTokenRef.current !== tokenAtStart) return;
														// Best-effort: if backend provides canonical route_ints for this line,
														// try to fetch the in-memory route_tracks geometry and render it.
														try {
															const routeInts = labelData && Array.isArray(labelData.route_ints) ? labelData.route_ints : [];
															const routeIds = labelData && Array.isArray(labelData.route_ids) ? labelData.route_ids : [];
															const pos = marker && Array.isArray(marker.position) ? marker.position : null;
															try { console.debug('[map] label route_ints/route_ids (post-route)', { line: String(line), routeIntsLen: routeInts.length, routeInts: routeInts.slice(0, 10), routeIdsLen: routeIds.length, routeIds: routeIds.slice(0, 10) }); } catch (e) { /* ignore */ }
															const candidates = (routeInts && routeInts.length) ? routeInts.map((v) => ({ kind: 'route_int', value: v })) : [];
															if (!candidates.length && routeIds && routeIds.length) {
																candidates.push(...routeIds.map((v) => ({ kind: 'route_id', value: v })));
															}
															if (candidates.length && pos && pos.length === 2) {
																const eps = 0.0001;
																for (const cand of candidates) {
																	const raw = cand && cand.value != null ? cand.value : null;
																	const val = raw != null ? String(raw) : null;
																	if (!val) continue;
																	try { console.debug('[map] trying leg-geometry candidate (post-route)', { line: String(line), kind: cand.kind, val }); } catch (e) { /* ignore */ }
																	let url = `${API_BASE}/route/leg-geometry?from_lat=${encodeURIComponent(pos[0])}&from_lon=${encodeURIComponent(pos[1])}`;
																	url += `&to_lat=${encodeURIComponent(pos[0] + eps)}&to_lon=${encodeURIComponent(pos[1] + eps)}`;
																	url += `&mode=driving`;
																	url += cand.kind === 'route_int' ? `&route_int=${encodeURIComponent(val)}` : `&route_id=${encodeURIComponent(val)}`;
																	try { console.debug('[map] leg-geometry request (post-route)', { line: String(line), kind: cand.kind, val, url }); } catch (e) { /* ignore */ }
																	const controllerGeom = new AbortController();
																	const tgeom = setTimeout(() => controllerGeom.abort(), 5000);
																	const resp = await fetch(url, { signal: controllerGeom.signal });
																	clearTimeout(tgeom);
																	if (selectedVehicleReqTokenRef.current !== tokenAtStart) {
																		try { console.debug('[map] token mismatch after fetch (post-route) — stopping', { line: String(line), kind: cand.kind, val }); } catch (e) { /* ignore */ }
																		break;
																	}
																	if (!resp || !resp.ok) continue;
																	let data = null;
																	let norm = null;
																	try {
																		data = await resp.json();
																		norm = (typeof normalizeCoords === 'function') ? normalizeCoords(data && data.coords) : (data && data.coords);
																		if (!Array.isArray(norm) || norm.length < 2) continue;
																		if (data && data.source === 'linear') continue;
																		const color = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
																		// If OSRM is up, snap the provided route_tracks geometry to roads.
																		// Best-effort only — fall back immediately to the raw coords.
																		setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: norm, color } : { id: marker.id, coords: norm, color, stops: [], label: null });
																		try {
																			const snapped = await osrmRouteCoordsLatLon(norm);
																			if (selectedVehicleReqTokenRef.current === tokenAtStart && Array.isArray(snapped) && snapped.length >= 2) {
																				setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: snapped, color } : prev);
																			}
																		} catch (e) { /* ignore */ }
																	break;
																	} catch (_je) {
																		continue;
																	}
																}
															}
														} catch (ee) {
															// ignore geometry failures
														}
														if (selectedVehicleReqTokenRef.current !== tokenAtStart) return;
														setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, label: labelData } : prev);
													} catch (err) {
														// non-fatal label fetch
														 
														console.warn('[map] fetchRouteLabel failed (post-route) for line=', line, err && err.message ? err.message : err);
													}
													})(selectionToken);
											}
								
											// After either path above, selection logic will run using __routeData_local

											// Use the route data populated above (from cache or fetch)
											const routeData = __routeData_local;

												if (!routeData || !Array.isArray(routeData.variants) || routeData.variants.length === 0) {
												setSelectedVehicleTrack(null);
												return;
											}

											// Now proceed with the existing selection logic (prefer meta mapping,
											// then optionally nearest-geometry when debugShowAllBuses is enabled).
											let metaRouteId = null;
											if (marker.meta) {
												metaRouteId = marker.meta.route_id || marker.meta.routeId || marker.meta.route || marker.meta.logged_journey_id || marker.meta.journey_id || marker.meta.journeyId || null;
											}

											const pos = marker.position;

											if (metaRouteId) {
												// Production policy: frontends MUST use the backend's
												// file-prefixed canonical route_id (contains '::').
												// Only perform an exact equality match here to avoid
												// accidentally matching suffix-only or legacy ids.
												const metaStr = String(metaRouteId);
												const match = routeData.variants.find(v => {
													if (!v || v.route_id == null) return false;
													const vr = String(v.route_id);
													return vr === metaStr;
												});
												if (match) {
													let geom = Array.isArray(match.geometry) ? match.geometry : stopsToLatLngs(match.stops);
													const stops = Array.isArray(match.stops) ? stopsToLatLngs(match.stops) : [];
													if (Array.isArray(geom) && geom.length >= 2) {
														const norm = geom.map((pt) => ([Number(pt[0]), Number(pt[1])]));
														const color = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
														// Label will be attached asynchronously when/if the fetch completes.
														setSelectedVehicleTrack({ id: marker.id, coords: norm, color, stops: [], label: null });
														try { 
															// Notify parent of the popup open and its signature (optional)
															try { if (onOpenPopupSignature) onOpenPopupSignature(makeMarkerSignature(marker)); } catch (ee) { /* ignore */ }
															onOpenPopup(marker.id);
														} catch (e) { /* ignore */ }
														return;
													}
													if (stops.length >= 2) {
														const norm = stops.map((pt) => ([Number(pt[0]), Number(pt[1])]));
														const color = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
														setSelectedVehicleTrack({ id: marker.id, coords: norm, color, stops: [], label: null });
														try { 
															try { if (onOpenPopupSignature) onOpenPopupSignature(makeMarkerSignature(marker)); } catch (ee) { /* ignore */ }
															onOpenPopup(marker.id);
														} catch (e) { /* ignore */ }
														return;
													}
												}
											}

											let best = null;
											if (metaRouteId || debugShowAllBuses) {
												for (const variant of routeData.variants) {
													let geom = Array.isArray(variant.geometry) ? variant.geometry : stopsToLatLngs(variant.stops);
													if (!Array.isArray(geom) || geom.length === 0) continue;
													const norm = geom.map((pt) => ([Number(pt[0]), Number(pt[1])]));
													let minD = Infinity;
													for (const p of norm) {
														const dlat = p[0] - pos[0];
														const dlon = p[1] - pos[1];
														const d2 = dlat * dlat + dlon * dlon;
														if (d2 < minD) minD = d2;
													}
													if (best == null || minD < best.minD) {
														best = { variant, norm, minD };
													}
												}

												if (!best) {
													setSelectedVehicleTrack(null);
													return;
												}
											} else {
												setSelectedVehicleTrack(null);
												return;
											}

											const color = busIconColor(marker.delayMinutes, isBusMappedLocal(marker));
											const stops = Array.isArray(best.variant && best.variant.stops) ? stopsToLatLngs(best.variant.stops) : [];
											setSelectedVehicleTrack({ id: marker.id, coords: best.norm, color, stops, label: null });
											try { 
												try { if (onOpenPopupSignature) onOpenPopupSignature(makeMarkerSignature(marker)); } catch (ee) { /* ignore */ }
												onOpenPopup(marker.id);
											} catch (e) { /* ignore */ }
										} catch (e) {
											// If either request failed or timed out, ensure no partial UI is shown.
											setSelectedVehicleTrack(null);
											try {
												const t1 = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
												 
												console.warn(`[map] route/label fetch failed for line=${line} marker=${marker.id}:`, e, `took=${Math.round(t1-t0)}ms`);
												setLoadingError({ id: marker.id, message: (e && e.message) ? e.message : String(e) });
											} catch (ee) { /* ignore */ }
										} finally {
											try { setLoadingPopupId(null); } catch (e) { /* ignore */ }
										}
									}
								}
							}}
						>
							{/* Show popup when openPopupId matches (successful fetch) or when loadingPopupId matches (in-progress) */}
							{(openPopupId === marker.id || loadingPopupId === marker.id) && (
								<AdaptivePopup marker={marker} onClose={onClosePopup}>
									<Box sx={{ minWidth: '200px', pb: 1 }}>
										<Typography variant="subtitle2" fontWeight={700} sx={{ mb: 0.5 }}>
											{marker.name}
										</Typography>
										{loadingPopupId === marker.id && (
											<Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
												<CircularProgress size={18} />
												<Typography variant="body2">Loading route…</Typography>
											</Box>
										)}
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
												backgroundColor: busIconColor(marker.delayMinutes, isBusMappedLocal(marker)),
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
										{/* Bearing value intentionally hidden from popup to avoid clutter; icon shows heading visually */}
										{marker.meta && Object.keys(marker.meta).length > 0 && (
											<Box sx={{ mt: 1 }}>
												{Object.entries(marker.meta)
													.filter(([k]) => {
														const kk = String(k).toLowerCase();
															// Exclude coordinate/operator/delay/status, identifier fields and any bearing-like fields
															return ![
																'lat', 'lon', 'latitude', 'longitude',
																'operator', 'operator_name', 'operatorname', 'operator_ref', 'operatorref', 'operatorname',
																'delay_minutes', 'delayminutes', 'status',
																'bearing', 'bearing_degrees', 'bearingdegrees', 'heading', 'course',
																// Administrative/identifier fields that should not be shown in the popup
																'logged_journey_id', 'loggedjourneyid', 'journey_id', 'journeyid',
																'framed_journey_ref', 'framedjourneyref', 'dated_journey_ref', 'datedjourneyref',
																'vehicle_journey_code', 'vehiclejourneycode', 'vehicle_ref', 'vehicleref',
																// Origin/departure and ATCO fields (noisy for popup labels)
																'origin_dep_secs', 'origindepsecs', 'origin_dep', 'origindep',
																'origin_atco', 'originatco', 'destination_atco', 'destinationatco'
															].includes(kk);
													})
													.map(([key, value]) => (
														<Typography key={key} variant="body2" sx={{ mb: 0.5 }}>
															<strong>{key.replace(/_/g, ' ')}:</strong> {String(value)}
														</Typography>
													))}

													{/* Selected vehicle track overlay (shown when user clicks a live vehicle) */}
													{selectedVehicleTrack && Array.isArray(selectedVehicleTrack.coords) && selectedVehicleTrack.coords.length >= 2 && (
														<>
															{/* Cyan underlay/frame so the vehicle track has a cyan outline */}
															<Polyline
																pane="routePane"
																positions={selectedVehicleTrack.coords}
																pathOptions={{ color: '#00ffff', weight: 6, opacity: 0.95, lineCap: 'round', lineJoin: 'round' }}
																eventHandlers={{
																	click: () => {
																		try { setSelectedVehicleTrack(null); } catch (e) { /* ignore */ }
																		try { onClosePopup(); } catch (e) { /* ignore */ }
																	}
																}}
															/>
															{/* Main coloured track */}
															<Polyline
																pane="routePane"
																positions={selectedVehicleTrack.coords}
																pathOptions={{ color: selectedVehicleTrack.color || '#1a73e8', weight: 4, opacity: 1, lineCap: 'round', lineJoin: 'round' }}
																eventHandlers={{
																	click: () => {
																		try { setSelectedVehicleTrack(null); } catch (e) { /* ignore */ }
																		try { onClosePopup(); } catch (e) { /* ignore */ }
																	}
																}}
															/>
															{/* Render stop markers snapped to the displayed polyline so they lie exactly on the track */}
															{Array.isArray(selectedVehicleTrack.stops) && selectedVehicleTrack.stops.length > 0 && selectedVehicleTrack.coords.length >= 2 && (
																selectedVehicleTrack.stops.map((s, si) => {
																	try {
																		const snapped = _nearestPointOnPolyline(selectedVehicleTrack.coords, s);
																		if (!snapped) return null;
																		return (
																			<CircleMarker
																				key={`stop-${si}`}
																				center={snapped}
																				radius={4}
																				pathOptions={{ color: '#ffffff', weight: 2, fillColor: selectedVehicleTrack.color || '#1a73e8', fillOpacity: 1 }}
																			/>
																		);
																	} catch (e) {
																		return null;
																	}
																})
															)}
														</>
													)}
											</Box>
										)}
									</Box>
								</AdaptivePopup>
							)}
											</Marker>
										);
									}
								})}

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
						// Adjusted to be ~1.05x (5% wider) from the previous 0.9× baseline
						minWidth: { md: 363, lg: 381 },
						width: { md: 408, lg: 436 },
						maxWidth: '43vw',
					}}>
						{sideContent}
					</Box>
				)}
			</Box>

			{/* On small screens, render the sideContent below the map (full width) */}
			{showSideOverlay && sideContent && (
				<Box sx={{ display: { xs: 'block', md: 'none' }, width: '100%', maxHeight: '70dvh', overflow: 'hidden' }}>
					{sideContent}
				</Box>
			)}
		</Stack>
	);
}
