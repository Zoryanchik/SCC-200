import React, { useMemo } from 'react';
import { Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import { useBusStops } from '../../hooks/useBusStops';

/**
 * Colour palette for stop classifications.
 */
const CLASS_COLORS = {
  hub: '#E91E63',
  interchange: '#FF9800',
  local: '#1976d2',
  request_stop: '#9E9E9E',
};

const CLASS_LABELS = {
  hub: 'Hub',
  interchange: 'Interchange',
  local: 'Local Stop',
  request_stop: 'Request Stop',
};

const getMarkerSize = (zoom) => {
  if (zoom <= 14) return { circle: 6, post: 8, stroke: 1.5, total: 16 };
  if (zoom <= 15) return { circle: 8, post: 10, stroke: 2, total: 20 };
  if (zoom <= 16) return { circle: 10, post: 12, stroke: 2, total: 24 };
  return { circle: 12, post: 14, stroke: 2.5, total: 28 };
};

const makeBusStopIcon = (color, zoom) => {
  const s = getMarkerSize(zoom);
  const w = s.circle + s.stroke * 2;
  const h = s.total;
  const cx = w / 2;
  const r = s.circle / 2;
  const postTop = s.circle + s.stroke;

  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
      <line x1="${cx}" y1="${postTop}" x2="${cx}" y2="${h}" stroke="${color}" stroke-width="${s.stroke}" stroke-linecap="round"/>
      <circle cx="${cx}" cy="${r + s.stroke / 2}" r="${r}" fill="${color}" stroke="#fff" stroke-width="${s.stroke}"/>
    </svg>`;

  return L.divIcon({
    html: svg,
    className: '',
    iconSize: [w, h],
    iconAnchor: [cx, h],
    popupAnchor: [0, -h],
  });
};

/**
 * Build popup HTML as a plain string with real <button> elements
 * and inline onclick attributes.  This is the ONLY way to get
 * clickable elements inside a Leaflet popup because Leaflet blocks
 * every other form of event handling (React synthetic events,
 * addEventListener, etc.).  Inline onclick fires directly on the
 * element before any propagation.
 */
function buildPopupHtml(stop) {
  const color = CLASS_COLORS[stop.classification] || CLASS_COLORS.local;
  const label = CLASS_LABELS[stop.classification] || 'Stop';

  let html = '<div style="min-width:160px;font-family:Arial,sans-serif">';

  html += '<div data-testid="bus-stop-name" style="font-weight:700;font-size:14px;margin-bottom:4px">'
    + stop.name + '</div>';

  html += '<div data-testid="bus-stop-classification" style="display:inline-block;padding:2px 8px;'
    + 'border-radius:10px;background:' + color + ';color:#fff;font-size:11px;font-weight:600;'
    + 'margin-bottom:6px">' + label + '</div>';

  if (stop.atco_code) {
    html += '<div data-testid="bus-stop-atco" style="font-size:11px;color:#666;margin-bottom:4px">'
      + stop.atco_code + '</div>';
  }

  if (stop.lines && stop.lines.length > 0) {
    html += '<div data-testid="bus-stop-lines">';
    html += '<div style="font-size:12px;font-weight:600;margin:4px 0">'
      + 'Bus routes: <span style="font-size:10px;font-weight:400;color:#888">'
      + '(click to show route)</span></div>';
    html += '<div style="display:flex;flex-wrap:wrap;gap:4px">';

    for (const line of stop.lines) {
      const safe = line.replace(/'/g, "\\'").replace(/"/g, '&quot;');
      html += '<button onclick="window.__busRouteToggle(\'' + safe + '\')" '
        + 'data-testid="route-chip-' + line + '" '
        + 'style="display:inline-block;padding:4px 10px;border-radius:8px;'
        + 'background:#E3F2FD;color:#1565C0;font-size:12px;font-weight:700;'
        + 'cursor:pointer;border:2px solid #90CAF9;margin:0;line-height:1.2;'
        + 'min-height:0;min-width:0">'
        + line + '</button>';
    }

    html += '</div></div>';
  }

  html += '</div>';
  return html;
}

/**
 * Renders a single bus-stop marker.
 * Popup content is plain HTML with inline onclick handlers.
 */
function BusStopMarker({ stop, zoom = 16 }) {
  const color = CLASS_COLORS[stop.classification] || CLASS_COLORS.local;
  const icon = useMemo(() => makeBusStopIcon(color, zoom), [color, zoom]);
  const popupHtml = useMemo(() => buildPopupHtml(stop), [stop]);

  return (
    <Marker
      position={[stop.lat, stop.lon]}
      icon={icon}
      data-testid={`bus-stop-marker-${stop.id}`}
    >
      <Popup closeOnClick={false}>
        <div dangerouslySetInnerHTML={{ __html: popupHtml }} />
      </Popup>
    </Marker>
  );
}

/**
 * Map layer that renders all bus stops as small bus-stop sign icons.
 *
 * Registers window.__busRouteToggle so inline onclick handlers
 * in Leaflet popups can trigger route line toggling.
 */
export default function BusStopLayer({
  classification,
  enabled = true,
  minZoom = 14,
  onToggleRoute,
  isRouteActive,
}) {
  const map = useMap();
  const { stops, loading } = useBusStops({ classification, enabled });

  // Register global function for popup button clicks
  const toggleRef = React.useRef(onToggleRoute);
  toggleRef.current = onToggleRoute;

  React.useEffect(() => {
    window.__busRouteToggle = (line) => {
      if (toggleRef.current) toggleRef.current(line);
    };
    return () => { delete window.__busRouteToggle; };
  }, []);

  const [zoom, setZoom] = React.useState(map.getZoom());

  React.useEffect(() => {
    const onZoom = () => setZoom(map.getZoom());
    map.on('zoomend', onZoom);
    return () => { map.off('zoomend', onZoom); };
  }, [map]);

  const visibleStops = useMemo(() => {
    if (zoom < minZoom) return [];
    const bounds = map.getBounds();
    return stops.filter(
      (s) =>
        s.lat >= bounds.getSouth() &&
        s.lat <= bounds.getNorth() &&
        s.lon >= bounds.getWest() &&
        s.lon <= bounds.getEast(),
    );
  }, [stops, zoom, minZoom, map]);

  if (loading || zoom < minZoom) return null;

  return (
    <>
      {visibleStops.map((stop) => (
        <BusStopMarker key={stop.id} stop={stop} zoom={zoom} />
      ))}
    </>
  );
}

export { BusStopMarker };
