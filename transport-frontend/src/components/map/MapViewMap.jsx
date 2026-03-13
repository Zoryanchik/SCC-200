import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import { MapContainer, Marker, Popup, Polyline, CircleMarker, TileLayer, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import icon from 'leaflet/dist/images/marker-icon.png';
import iconShadow from 'leaflet/dist/images/marker-shadow.png';
import "leaflet/dist/leaflet.css";
import React, { useEffect } from "react";
// Allow a sideContent prop to be injected by the parent (e.g. Suggested routes)
import BusStopLayer from "./BusStopLayer";
import RouteLineLayer from "./RouteLineLayer";
import { useRouteLine } from "../../hooks/useRouteLine";
import { fetchRouteLineWithFallback, stopsToLatLngs } from '../../services/routeLineApi';
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
	let innerHtml;
	if (hasBearing) {
		innerHtml = `<g transform="rotate(${rot} ${cx} ${cx})">${arrowSvg}${circleEl}</g>${staticContent}`;
	} else {
		innerHtml = `${circleEl}${staticContent}`;
	}

	const svg = `<svg width="${S}" height="${S}" viewBox="0 0 ${S} ${S}" xmlns="http://www.w3.org/2000/svg">${innerHtml}</svg>`;

	return L.divIcon({
		html: svg,
		className: 'custom-marker-icon',
		iconSize: [S, S],
		iconAnchor: [cx, cx],
		popupAnchor: [0, -(cx + 2)],
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
 * Create a prominent endpoint icon (Start / Destination).
 * @param {string} color - fill color for the endpoint
 * @param {string} label - short label to render inside the icon ('S'|'D' or text)
 */
const createEndpointIcon = (color = '#10B981', label = '') => {
	const size = 36;
	const c = size / 2;
	const r = c - 2;
	const svg = `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg">
		<circle cx="${c}" cy="${c}" r="${r}" fill="${color}" stroke="#ffffff" stroke-width="3" />
		<text x="${c}" y="${c}" text-anchor="middle" dominant-baseline="middle" font-family="Arial,Helvetica,sans-serif" font-size="12" font-weight="700" fill="#fff">${label}</text>
	</svg>`;
	return L.divIcon({ html: svg, className: 'endpoint-div-icon', iconSize: [size, size], iconAnchor: [c, c], popupAnchor: [0, -c] });
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
		if (start && Array.isArray(start) && start.length === 2) {
			const m = L.marker(start, { icon: createEndpointIcon('#10B981', 'S') }).addTo(map);
			created.push(m);
		}
		if (end && Array.isArray(end) && end.length === 2) {
			const m = L.marker(end, { icon: createEndpointIcon('#d32f2f', 'D') }).addTo(map);
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
const busIconColor = (delayMinutes) => {
	// Treat negative delays (early) visually the same as on-time.
	if (delayMinutes == null) return '#1976d2';      // unknown → blue
	if (delayMinutes >= 10) return '#d32f2f';        // very late → red
	if (delayMinutes >= 2) return '#f57c00';         // delayed → orange
	return '#1976d2';                                // on time / early → blue
};

const TRAIN_ICON = createCustomIcon('train', '#2e7d32', null, null);
const USER_ICON = createUserIcon();

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

	// Helper to normalise coords returned from the backend/OSRM to [[lat,lon],...]
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
			const firstSeg = displaySegments[0];
			const lastSeg = displaySegments[displaySegments.length - 1];
			const start = firstSeg?.coords?.[0] ?? null;
			const end = lastSeg?.coords?.[lastSeg.coords.length - 1] ?? null;
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
					const url = `${API_BASE}/route/leg-geometry?from_lat=${encodeURIComponent(from[0])}&from_lon=${encodeURIComponent(from[1])}&to_lat=${encodeURIComponent(to[0])}&to_lon=${encodeURIComponent(to[1])}&mode=driving`;
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
										weight: isWalk ? 5 : 6,
										opacity: 0.95,
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
										// Walking legs: deep grey, dashed so they remain distinct from vehicle tracks
										color: isWalk ? 'hsla(307, 53%, 67%, 1.00)' : (seg.color || '#1a73e8'),
										weight: isWalk ? 4 : 4,
										opacity: isWalk ? 1 : 1,
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
				{/* Origin dot */}
				{displaySegments[0]?.coords?.[0] && (
					<CircleMarker
						pane="endpointPane"
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
							pane="endpointPane"
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
		// Create dedicated panes so we can control z-order between
		// route polylines (routePane) and transfer/endpoint markers
		// (transferPane). This ensures transfer stop markers render
		// above route tracks regardless of render order.
		try {
			if (!map.getPane('routePane')) {
				map.createPane('routePane');
				map.getPane('routePane').style.zIndex = 400;
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
		/** show or hide currently-active route overlays (from useRouteLine) */
		showRouteLines = true,
	/** Route geometry from journey planner. Array of {id, name, coords, color} */
	journeyRoute = null,
}) {
	const countdownTotal = Math.max(1, Math.round(busRefreshInterval / 1000));
	const ringValue = Math.round((busCountdown / countdownTotal) * 100);

	const { activeRoutes, toggleRoute, isActive, clearRoutes } = useRouteLine();

	// Selected vehicle track overlay shown when user clicks a live vehicle marker.
	const [selectedVehicleTrack, setSelectedVehicleTrack] = React.useState(null);

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

	// When active route overlays are cleared, also clear any selected vehicle track
	React.useEffect(() => {
		try {
			if (!activeRoutes || activeRoutes.size === 0) {
				setSelectedVehicleTrack(null);
			}
		} catch (e) {
			// ignore
		}
	}, [activeRoutes]);
	const { highContrast } = useAccessibility();

	// Debug toggle mirrored from MapViewPage: when set in localStorage under
	// SHOW_ALL_BUSES_DEBUG, allow the UI to fall back to nearest-geometry variant
	// selection for debugging. Default is false in normal operation.
	const debugShowAllBuses = (typeof window !== 'undefined' && window.localStorage && window.localStorage.getItem('SHOW_ALL_BUSES_DEBUG') === '1');

	// Cache routeData per-line so first-click has immediate access when possible.
	const [routeDataCache, setRouteDataCache] = React.useState({});

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
		for (const line of lines) {
			if (routeDataCache[line]) continue;
			fetchRouteLineWithFallback(line).then((data) => {
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
				{showRouteLines && activeRoutes.size > 0 && (
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
					{showRouteLines && <RouteLineLayer activeRoutes={activeRoutes} />}
				{/* Journey-plan route overlay */}
				{(function(){
					try { console.debug('[MapViewMap] journeyRoute segments=', Array.isArray(journeyRoute) ? journeyRoute.length : journeyRoute); } catch(e) {}
					return (showRouteLines && Array.isArray(journeyRoute) && journeyRoute.length > 0) ? <JourneyRouteLayer segments={journeyRoute} /> : null;
				})()}
				{/* Developer debug overlay removed - rely on JourneyRouteLayer smoothing and styling */}
					{filteredMarkers.map((marker) => (
						<Marker
							key={marker.id}
							position={marker.position}
							icon={marker.type === 'bus'
							? createCustomIcon('bus', busIconColor(marker.delayMinutes), marker.routeNumber != null ? String(marker.routeNumber) : null, marker.bearing != null ? Number(marker.bearing) : null)
								: TRAIN_ICON}
							eventHandlers={{
								click: async () => {
									// open popup immediately
									try { onOpenPopup(marker.id); } catch (e) { /* ignore */ }
									// If the same vehicle is already selected, toggle it off
									try {
										if (selectedVehicleTrack && selectedVehicleTrack.id === marker.id) {
											setSelectedVehicleTrack(null);
											return;
										}
									} catch (e) { /* ignore */ }
									if (marker.type === 'bus') {
										try {
											const line = marker.routeNumber || marker.route || null;
											if (!line) return;
											// Prefer cached data (prefetched) to avoid falling back to mock on first click
											let routeData = routeDataCache[String(line)];
											if (!routeData) {
												routeData = await fetchRouteLineWithFallback(String(line));
												// cache result for future clicks
												setRouteDataCache((prev) => ({ ...prev, [String(line)]: routeData }));
											}
											if (!routeData || !Array.isArray(routeData.variants) || routeData.variants.length === 0) {
												setSelectedVehicleTrack(null);
												return;
											}

											// Prefer an explicit mapping included in the live marker metadata
											// (the bus feed / backend augmentation often attaches route_id,
											// journey_id or similar fields when computing delays). If present
											// use that to pick the exact variant instead of nearest-geometry.
											let metaRouteId = null;
											if (marker.meta) {
												metaRouteId = marker.meta.route_id || marker.meta.routeId || marker.meta.route || marker.meta.logged_journey_id || marker.meta.journey_id || marker.meta.journeyId || null;
											}

											const pos = marker.position;
											// If we have an explicit route_id from the marker metadata, try to
											// find a matching variant immediately and use its geometry.
											if (metaRouteId) {
												const match = routeData.variants.find(v => v && (v.route_id === metaRouteId || String(v.route_id) === String(metaRouteId)));
												if (match) {
													let geom = Array.isArray(match.geometry) ? match.geometry : stopsToLatLngs(match.stops);
													const stops = Array.isArray(match.stops) ? stopsToLatLngs(match.stops) : [];
													if (Array.isArray(geom) && geom.length >= 2) {
														const norm = geom.map((pt) => ([Number(pt[0]), Number(pt[1])]));
														const color = busIconColor(marker.delayMinutes);
														setSelectedVehicleTrack({ id: marker.id, coords: norm, color, stops });
														return;
													}
													// If geometry absent but stops present, render stops-only polyline
													if (stops.length >= 2) {
														const norm = stops.map((pt) => ([Number(pt[0]), Number(pt[1])]));
														const color = busIconColor(marker.delayMinutes);
														setSelectedVehicleTrack({ id: marker.id, coords: norm, color, stops });
														return;
													}
												}
											}


											// If no explicit mapping was provided, only fall back to
											// nearest-geometry when developer debug mode is enabled.
											// This avoids the UI picking an unrelated route variant
											// purely based on geometric proximity when the server
											// hasn't provided an authoritative id.
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
												// No authoritative mapping and not in debug mode:
												// do not attempt to guess the variant by geometry.
												setSelectedVehicleTrack(null);
												return;
											}

											const color = busIconColor(marker.delayMinutes);
											// include stops if variant provides them
											const stops = Array.isArray(best.variant && best.variant.stops) ? stopsToLatLngs(best.variant.stops) : [];
											setSelectedVehicleTrack({ id: marker.id, coords: best.norm, color, stops });
										} catch (e) {
											setSelectedVehicleTrack(null);
										}
									}
								}
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
															<Polyline pane="routePane" positions={selectedVehicleTrack.coords} pathOptions={{ color: '#00ffff', weight: 6, opacity: 0.95, lineCap: 'round', lineJoin: 'round' }} />
															{/* Main coloured track */}
															<Polyline pane="routePane" positions={selectedVehicleTrack.coords} pathOptions={{ color: selectedVehicleTrack.color || '#1a73e8', weight: 4, opacity: 1, lineCap: 'round', lineJoin: 'round' }} />
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
