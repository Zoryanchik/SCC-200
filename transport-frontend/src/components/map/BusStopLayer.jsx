import React, { useMemo } from 'react';
import { Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import { useBusStops } from '../../hooks/useBusStops';
import { fetchBusArrivals } from '../../services/busStopsApi';

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

const makeBusStopIcon = (color, zoom, selected = false) => {
  const s = getMarkerSize(zoom);
  const w = s.circle + s.stroke * 2;
  const h = s.total;
  const cx = w / 2;
  const r = s.circle / 2;
  const postTop = s.circle + s.stroke;
  const cy = r + s.stroke / 2;

  // When the popup is open, draw two concentric halo rings behind the
  // main circle so the selected stop stands out clearly on the map.
  const halo = selected
    ? `<circle cx="${cx}" cy="${cy}" r="${r + 5}" fill="${color}" fill-opacity="0.28" stroke="${color}" stroke-width="1.5" stroke-opacity="0.55"/>
       <circle cx="${cx}" cy="${cy}" r="${r + 10}" fill="none" stroke="${color}" stroke-width="1" stroke-opacity="0.2"/>`
    : '';
  const circleStroke = selected ? s.stroke + 1.5 : s.stroke;

  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" overflow="visible">
      ${halo}
      <line x1="${cx}" y1="${postTop}" x2="${cx}" y2="${h}" stroke="${color}" stroke-width="${s.stroke}" stroke-linecap="round"/>
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="${color}" stroke="#fff" stroke-width="${circleStroke}"/>
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
 * Shows upcoming arrivals for a stop, fetching from the API on mount.
 * Renders a loading/error/empty/list state inside the popup.
 */
export function ArrivalsPanel({ atcoCode }) {
  const [arrivals, setArrivals] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);

  React.useEffect(() => {
    setLoading(true);
    setError(null);
    fetchBusArrivals(atcoCode)
      .then((data) => { setArrivals(data); setLoading(false); })
      .catch((err) => { setError(err.message || 'Error'); setLoading(false); });
  }, [atcoCode]);

  const baseStyle = {
    fontSize: 11,
    padding: '4px 0',
    borderTop: '1px solid #eee',
    marginTop: 6,
  };

  if (loading) {
    return (
      <div data-testid="arrivals-loading" style={{ ...baseStyle, color: '#888' }}>
        Loading arrivals…
      </div>
    );
  }
  if (error) {
    return (
      <div data-testid="arrivals-error" style={{ ...baseStyle, color: '#c00' }}>
        Could not load arrivals
      </div>
    );
  }
  if (!arrivals || arrivals.length === 0) {
    return (
      <div data-testid="arrivals-empty" style={{ ...baseStyle, color: '#888' }}>
        No upcoming arrivals
      </div>
    );
  }

  return (
    <div data-testid="arrivals-panel" style={{ borderTop: '1px solid #eee', marginTop: 6, paddingTop: 6 }}>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>Upcoming arrivals</div>
      {arrivals.map((a, i) => (
        <div
          key={i}
          data-testid={`arrival-row-${i}`}
          style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '2px 0' }}
        >
          <span data-testid="arrival-line" style={{ fontWeight: 700, minWidth: 28 }}>{a.line}</span>
          <span
            data-testid="arrival-destination"
            style={{ flex: 1, color: '#444', overflow: 'hidden', textOverflow: 'ellipsis' }}
          >{a.destination}</span>
          <span data-testid="arrival-time" style={{ color: '#1976d2', fontWeight: 600, marginLeft: 4 }}>
            {a.scheduledTime}
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * Renders a single bus-stop marker.
 * Popup content is plain HTML (for route buttons) + a React ArrivalsPanel.
 * Arrivals are only fetched when the popup is opened.
 */
function BusStopMarker({ stop, zoom = 16 }) {
  const color = CLASS_COLORS[stop.classification] || CLASS_COLORS.local;
  const [popupOpen, setPopupOpen] = React.useState(false);
  // Re-derive the icon whenever zoom, colour, or popup-open state changes so
  // the marker enlarges / gains a halo ring while its popup is visible.
  const icon = useMemo(() => makeBusStopIcon(color, zoom, popupOpen), [color, zoom, popupOpen]);
  const popupHtml = useMemo(() => buildPopupHtml(stop), [stop]);

  return (
    <Marker
      position={[stop.lat, stop.lon]}
      icon={icon}
      data-testid={`bus-stop-marker-${stop.id}`}
    >
      <Popup
        closeOnClick={false}
        eventHandlers={{
          add: () => setPopupOpen(true),
          remove: () => setPopupOpen(false),
        }}
      >
        <div dangerouslySetInnerHTML={{ __html: popupHtml }} />
        {popupOpen && stop.atco_code && <ArrivalsPanel atcoCode={stop.atco_code} />}
      </Popup>
    </Marker>
  );
}

/** Compute a bbox string "south,west,north,east" from current map bounds. */
const getBboxString = (map) => {
  const b = map.getBounds();
  return `${b.getSouth()},${b.getWest()},${b.getNorth()},${b.getEast()}`;
};

/**
 * Map layer that renders all bus stops as small bus-stop sign icons.
 *
 * Registers window.__busRouteToggle so inline onclick handlers
 * in Leaflet popups can trigger route line toggling.
 *
 * The current viewport bbox is passed to the hook so the API only
 * returns stops in the visible area, reducing data transfer.
 */
export default function BusStopLayer({
  classification,
  enabled = true,
  minZoom = 14,
  onToggleRoute,
  isRouteActive,
}) {
  const map = useMap();
  const [zoom, setZoom] = React.useState(map.getZoom());
  const [bbox, setBbox] = React.useState(
    map.getZoom() >= minZoom ? getBboxString(map) : null,
  );

  const { stops, loading } = useBusStops({ classification, enabled, bbox });

  // Register global function for popup button clicks
  const toggleRef = React.useRef(onToggleRoute);
  toggleRef.current = onToggleRoute;

  React.useEffect(() => {
    window.__busRouteToggle = (line) => {
      if (toggleRef.current) toggleRef.current(line);
    };
    return () => { delete window.__busRouteToggle; };
  }, []);

  // Update zoom + bbox on both zoomend and moveend so the API receives
  // the current viewport whenever the user is panning or zooming.
  React.useEffect(() => {
    const update = () => {
      const z = map.getZoom();
      setZoom(z);
      setBbox(z >= minZoom ? getBboxString(map) : null);
    };
    map.on('zoomend', update);
    map.on('moveend', update);
    return () => {
      map.off('zoomend', update);
      map.off('moveend', update);
    };
  }, [map, minZoom]);

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
