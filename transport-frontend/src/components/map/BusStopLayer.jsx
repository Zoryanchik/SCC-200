import React, { useMemo } from 'react';
import { Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import { useBusStops } from '../../hooks/useBusStops';

/**
 * Colour palette for stop classifications.
 * hub          → vivid red-pink (major interchange)
 * interchange  → orange
 * local        → blue
 * request_stop → grey
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

/**
 * Returns the marker dimensions for a given zoom level.
 * Keeps markers tiny at lower zooms to reduce clutter.
 */
const getMarkerSize = (zoom) => {
  if (zoom <= 14) return { circle: 6, post: 8, stroke: 1.5, total: 16 };
  if (zoom <= 15) return { circle: 8, post: 10, stroke: 2, total: 20 };
  if (zoom <= 16) return { circle: 10, post: 12, stroke: 2, total: 24 };
  return { circle: 12, post: 14, stroke: 2.5, total: 28 };
};

/**
 * Build an SVG bus-stop sign icon: a coloured circle on a thin post.
 * Returns a Leaflet DivIcon.
 */
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
 * Renders a single bus-stop marker as a small coloured bus-stop sign.
 * Clicking opens a Popup listing the bus lines that serve the stop.
 *
 * @param {{ stop: Object, zoom: number }} props
 */
function BusStopMarker({ stop, zoom = 16 }) {
  const color = CLASS_COLORS[stop.classification] || CLASS_COLORS.local;
  const label = CLASS_LABELS[stop.classification] || 'Stop';

  const icon = useMemo(() => makeBusStopIcon(color, zoom), [color, zoom]);

  return (
    <Marker
      position={[stop.lat, stop.lon]}
      icon={icon}
      data-testid={`bus-stop-marker-${stop.id}`}
    >
      <Popup>
        <div style={{ minWidth: 160, fontFamily: 'Arial, sans-serif' }}>
          <div
            style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}
            data-testid="bus-stop-name"
          >
            {stop.name}
          </div>

          <div
            style={{
              display: 'inline-block',
              padding: '2px 8px',
              borderRadius: 10,
              backgroundColor: color,
              color: '#fff',
              fontSize: 11,
              fontWeight: 600,
              marginBottom: 6,
            }}
            data-testid="bus-stop-classification"
          >
            {label}
          </div>

          {stop.atco_code && (
            <div
              style={{ fontSize: 11, color: '#666', marginBottom: 4 }}
              data-testid="bus-stop-atco"
            >
              {stop.atco_code}
            </div>
          )}

          {stop.lines && stop.lines.length > 0 && (
            <div data-testid="bus-stop-lines">
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 600,
                  marginBottom: 4,
                  marginTop: 4,
                }}
              >
                Bus routes:
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {stop.lines.map((line) => (
                  <span
                    key={line}
                    style={{
                      display: 'inline-block',
                      padding: '2px 8px',
                      borderRadius: 8,
                      backgroundColor: '#E3F2FD',
                      color: '#1565C0',
                      fontSize: 12,
                      fontWeight: 600,
                    }}
                  >
                    {line}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </Popup>
    </Marker>
  );
}

/**
 * Map layer that renders all bus stops as small bus-stop sign icons.
 *
 * Props are forwarded to `useBusStops` so the parent can control
 * classification filtering and enabled state.
 *
 * Bus stops are only rendered when the map zoom level is ≥ 14 to
 * prevent visual clutter at low zoom levels.
 *
 * @param {Object}  [props]
 * @param {string}  [props.classification] - Optional classification filter
 * @param {boolean} [props.enabled=true]   - Enable/disable the layer
 * @param {number}  [props.minZoom=14]     - Min zoom level to show stops
 */
export default function BusStopLayer({
  classification,
  enabled = true,
  minZoom = 14,
}) {
  const map = useMap();
  const { stops, loading } = useBusStops({ classification, enabled });

  // Only render stops when zoomed in enough
  const [zoom, setZoom] = React.useState(map.getZoom());

  React.useEffect(() => {
    const onZoom = () => setZoom(map.getZoom());
    map.on('zoomend', onZoom);
    return () => {
      map.off('zoomend', onZoom);
    };
  }, [map]);

  // Filter stops that fall within the current map bounds for performance
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

// Named export for testing
export { BusStopMarker };
