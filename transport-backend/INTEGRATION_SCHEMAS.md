# Backend ↔ Frontend Integration Schemas

> Auto-generated from `api.py` review — 2026-02-19

This document maps every backend endpoint to its **request contract** and
**response shape** so the frontend services (`transportApi.js`,
`useTransportData.js`) can be typed / tested without mismatch errors.

---

## 1. `GET /health`

**Purpose:** Liveness probe.

| Field    | Type   | Value  |
|----------|--------|--------|
| `status` | string | `"ok"` |

```json
{ "status": "ok" }
```

---

## 2. `GET /search/stops?q=<query>&limit=<n>`

**Purpose:** Autocomplete stop / location search (NaPTAN + Nominatim geocoder).

### Query Parameters
| Param            | Type   | Default | Required | Notes |
|------------------|--------|---------|----------|-------|
| `q`              | string | `""`    | Yes      |       |
| `limit`          | int    | `10`    | No       |       |
| `classification` | string | —       | No       | Filter by station class: `hub`, `interchange`, `local`, `request_stop`. When active, geocode locations are excluded. |

### Response — `Array<StopResult>`

Each item has:

| Field       | Type          | Notes                                      |
|-------------|---------------|---------------------------------------------|
| `id`        | string/int    | NaPTAN stop id or `"loc:<n>"` for geocoded  |
| `name`      | string        | Human-readable name                         |
| `atco_code` | string\|null  | NaPTAN ATCO code; `null` for locations      |
| `lat`       | number        | Latitude                                    |
| `lon`       | number        | Longitude                                   |
| `type`      | string        | `"stop"` or `"location"`                    |
| `classification` | string\|undefined | Present when `?classification=` is used. One of `hub`, `interchange`, `local`, `request_stop`. |

```json
[
  { "id": 1, "name": "Lancaster Bus Station", "atco_code": "LAN001", "lat": 54.048, "lon": -2.801, "type": "stop" },
  { "id": "loc:0", "name": "Lancaster, Lancashire, UK", "atco_code": null, "lat": 54.047, "lon": -2.801, "type": "location" }
]
```

### Error (503) — backend not initialised
```json
{ "error": "Backend not initialized" }
```

---

## 3. ~~`POST /api/bus_live`~~ (Removed)

> **Removed** — this duplicate endpoint was only used by the mock HTML frontend.
> All consumers should use `GET /bus/live/{operator}` (see section 4).

---

## 4. `GET /bus/live/{operator}?lat=&lon=&latTol=&lonTol=`

**Purpose:** Live bus positions for a specific operator — **this is the endpoint the React frontend calls**.

### Path Parameters
| Param      | Type   | Notes                                      |
|------------|--------|---------------------------------------------|
| `operator` | string | Operator code (e.g. `"SCCU"`) or `"all"`   |

### Query Parameters
| Param   | Type  | Default  | Required |
|---------|-------|----------|----------|
| `lat`   | float | —        | Yes      |
| `lon`   | float | —        | Yes      |
| `latTol`| float | `0.0003` | No       |
| `lonTol`| float | `0.0003` | No       |

### Response — `Array<BusPosition>`

| Field        | Type   |
|--------------|--------|
| `line`       | string |
| `destination`| string |
| `lat`        | float  |
| `lon`        | float  |

```json
[
  { "line": "1A", "destination": "Lancaster", "lat": 54.05, "lon": -2.80 }
]
```

### Error (400)
```json
{ "error": "lat and lon are required" }
```

---

## 5. `POST /journey/plan`

**Purpose:** Plan a journey between two lat/lon points. Returns structured legs + route geometries for map polylines.

### Request Body (`JourneyPlanRequest`)
| Field           | Type   | Default  | Notes                    |
|-----------------|--------|----------|--------------------------|
| `fromStop`      | object | —        | `{ lat: float, lon: float }` |
| `toStop`        | object | —        | `{ lat: float, lon: float }` |
| `departureTime` | string | —        | `"HH:MM:SS"`            |
| `date`          | string | —        | `"YYYY-MM-DD"`          |
| `maxTransfers`  | int    | `5`      |                          |
| `mode`          | string | `"both"` | `"bus"`, `"train"`, `"both"` |

### Response — `JourneyPlanResponse`

| Field             | Type          | Notes                                      |
|-------------------|---------------|---------------------------------------------|
| `success`         | bool          | `true` on success                          |
| `legs`            | Array\<Leg\>  | Ordered list of journey legs               |
| `meta`            | object        | Route metadata                             |
| `routeGeometries` | Array\<Geo\>  | Polyline coordinate arrays for the map     |
| `error`           | string\|null  | Only present on failure                    |

#### Leg Object
| Field               | Type        | Notes                                |
|---------------------|-------------|--------------------------------------|
| `type`              | string      | `"walking"`, `"bus"`, `"train"`, etc.|
| `from_stop`         | object      | `{ name, lat?, lon? }`               |
| `to_stop`           | object      | `{ name, lat?, lon? }`               |
| `duration_seconds`  | int\|null   |                                      |
| `departure_time`    | string\|null| `"HH:MM:SS"`                        |
| `arrival_time`      | string\|null| `"HH:MM:SS"`                        |
| `line_name`         | string\|null| Bus/train route name (transit legs)  |
| `journey_origin`    | string\|null| Service origin (transit legs)        |
| `journey_destination`| string\|null| Service destination (transit legs)  |
| `intermediate_stops` | Array\<Stop\>\|undefined | Intermediate stops between boarding and alighting (transit legs only). Each entry has `{ name, lat, lon }`. Empty array when adjacent stops; omitted for walking legs. |

#### Meta Object
| Field                | Type        |
|----------------------|-------------|
| `start_walk_seconds` | int         |
| `end_walk_seconds`   | int         |
| `total_arrival`      | string\|null|
| `start_point`        | Array\|null |
| `destination`        | Array\|null |

#### RouteGeometry Object
| Field   | Type              | Notes                           |
|---------|-------------------|---------------------------------|
| `id`    | string            | e.g. `"walk-0"`, `"bus-1"`      |
| `name`  | string            | Human label                     |
| `coords`| Array\<[lat,lon]\>| Array of `[lat, lon]` pairs. Transit legs include all intermediate stop coordinates for accurate polylines; walking legs use 2-point lines. |
| `color` | string            | Hex color for polyline          |

> **⚠️ Coordinate Order Convention**
>
> `routeGeometries[*].coords` uses **`[latitude, longitude]`** order.
> This matches what Leaflet's `L.polyline()` expects.
>
> This is **NOT** GeoJSON order — GeoJSON uses `[longitude, latitude]`.
> If the frontend ever switches to GeoJSON-based rendering (e.g. `L.geoJSON()`),
> the coordinate pairs must be transposed.
>
> The same `[lat, lon]` convention applies to `from_stop`/`to_stop` objects
> in each leg, and to `meta.start_point` / `meta.destination` arrays.
>
> Internally, the OSRM walking engine uses `lon,lat` in its URL — the
> backend handles that reversal in `walking.py`.

```json
{
  "success": true,
  "legs": [
    {
      "type": "walking",
      "from_stop": { "name": "Start", "lat": 54.048, "lon": -2.801 },
      "to_stop": { "name": "Lancaster Bus Station", "lat": 54.049, "lon": -2.800 },
      "duration_seconds": 120,
      "departure_time": null,
      "arrival_time": "10:02:00"
    },
    {
      "type": "bus",
      "from_stop": { "name": "Lancaster Bus Station", "lat": 54.049, "lon": -2.800 },
      "to_stop": { "name": "Preston Bus Station", "lat": 53.759, "lon": -2.699 },
      "duration_seconds": 1800,
      "departure_time": "10:05:00",
      "arrival_time": "10:35:00",
      "line_name": "40",
      "journey_origin": "Lancaster",
      "journey_destination": "Preston",
      "intermediate_stops": [
        { "name": "Galgate", "lat": 53.977, "lon": -2.782 },
        { "name": "Garstang", "lat": 53.898, "lon": -2.773 }
      ]
    }
  ],
  "meta": {
    "start_walk_seconds": 120,
    "end_walk_seconds": 0,
    "total_arrival": "10:35:00",
    "start_point": [54.048, -2.801],
    "destination": [53.759, -2.699]
  },
  "routeGeometries": [
    { "id": "walk-0", "name": "Walk to Lancaster Bus Station", "coords": [[54.048, -2.801], [54.049, -2.800]], "color": "#888888" },
    { "id": "bus-1", "name": "Bus 40", "coords": [[54.049, -2.800], [53.977, -2.782], [53.898, -2.773], [53.759, -2.699]], "color": "#1a73e8" }
  ]
}
```

### Error Response
```json
{
  "success": false,
  "error": "No route found",
  "legs": null,
  "meta": null,
  "routeGeometries": null
}
```

---

## 6. `POST /api/route` (Legacy mock frontend)

**Purpose:** Alternative route endpoint used by `index.html` mock frontend only.

### Request Body (`RouteRequest`)
| Field          | Type  | Default  |
|----------------|-------|----------|
| `start_lat`    | float | —        |
| `start_lon`    | float | —        |
| `end_lat`      | float | —        |
| `end_lon`      | float | —        |
| `date`         | string| —        |
| `time`         | string| —        |
| `max_transfers`| int   | `5`      |
| `mode`         | string| `"both"` |

### Response
```json
{
  "success": true,
  "route": { /* raw RAPTOR result dict */ },
  "route_text": "formatted text summary"
}
```

---

## 7. `GET /` — Mock Frontend Page

Serves `index.html` (Live Bus Data Viewer). Not consumed by React frontend.

---

## Frontend ↔ Backend Alignment Summary

| Frontend Function            | Backend Endpoint                         | Status        |
|------------------------------|------------------------------------------|---------------|
| `fetchLiveBusLocations()`    | `GET /bus/live/{operator}?lat=&lon=`     | ✅ Aligned     |
| `searchStops()`              | `GET /search/stops?q=&limit=`            | ✅ Aligned     |
| `getJourneyPlans()`          | `POST /journey/plan`                     | ✅ Aligned     |
| `liveUpdatesManager`         | `WS /ws/live` (STOMP 1.2)               | ✅ Aligned     |
| `fetchRailDepartures()`      | `GET /rail/departures/{station}`         | ❌ Not implemented |
| `fetchWeatherData()`         | `GET /weather?lat=&lon=`                 | ❌ Not implemented |
| `fetchServiceAlerts()`       | `GET /alerts`                            | ❌ Not implemented |
| `fetchPricing()`             | `GET /pricing?from=&to=`                 | ❌ Not implemented |
| `fetchBusTimes()`            | `GET /bus/times/{stopCode}`              | ❌ Not implemented |
| `fetchBusArrivals()`         | `GET /bus/arrivals/{stopCode}`           | ❌ Not implemented |

---

## 9. `GET /stops/classify?classification=<class>`

**Purpose:** Return station classification data for all stops based on network topology metrics.

### Query Parameters
| Param            | Type   | Default | Required | Notes |
|------------------|--------|---------|----------|-------|
| `classification` | string | —       | No       | Filter: `hub`, `interchange`, `local`, `request_stop` |

### Response — `Array<ClassifiedStop>`

Each item has:

| Field            | Type     | Notes                                    |
|------------------|----------|------------------------------------------|
| `stop_index`     | int      | Internal merged stop index               |
| `name`           | string   | Human-readable name                      |
| `degree`         | int      | Number of distinct routes serving stop   |
| `frequency`      | int      | Total daily departures through stop      |
| `interchange`    | int      | Number of distinct service line-names    |
| `lines`          | string[] | Sorted list of line names                |
| `classification` | string   | `"hub"`, `"interchange"`, `"local"`, or `"request_stop"` |

### Classification Rules

| Class          | Rule                                       |
|----------------|-------------------------------------------|
| `hub`          | degree ≥ 5 **and** frequency ≥ 100         |
| `interchange`  | interchange ≥ 3 **or** degree ≥ 4          |
| `local`        | frequency ≥ 10                             |
| `request_stop` | everything else                            |

```json
[
  { "stop_index": 42, "name": "Lancaster Bus Station", "degree": 8, "frequency": 210, "interchange": 6, "lines": ["1", "2", "40", "41", "100", "X1"], "classification": "hub" },
  { "stop_index": 7, "name": "Village Green", "degree": 1, "frequency": 4, "interchange": 1, "lines": ["87"], "classification": "request_stop" }
]
```

### Error (400)
```json
{ "error": "Invalid classification 'mega_hub'. Must be one of: hub, interchange, local, request_stop" }
```

### Error (503) — backend not initialised
```json
{ "error": "Backend not initialized" }
```

---

### Key Integration Notes

1. **API_BASE_URL mismatch**: Frontend `transportApi.js` uses `https://transport.scc.lancs.ac.uk` as base URL. For local dev, this must be changed to `http://localhost:5005` or a proxy configured in vite.
2. **`/bus/live/{operator}` response shape**: Backend returns `{line, destination, lat, lon}`. Frontend mock tests already use this shape — confirmed aligned.
3. **`/search/stops` mixed types**: Backend returns both `type: "stop"` and `type: "location"` results. Frontend tests already handle this.
4. **`/journey/plan` geometry format**: `routeGeometries[*].coords` uses `[lat, lon]` pairs (not GeoJSON `[lon, lat]`). Frontend polyline rendering must respect this order.
5. **`POST /api/bus_live` removed**: The duplicate endpoint was removed. All consumers (including the mock HTML frontend) now use `GET /bus/live/{operator}`.
6. **CORS**: Backend allows `localhost:3000` and `localhost:5173`. Vite dev server (default 5173) is covered.
7. **`/journey/plan` error shape**: On failure, returns `success: false` with `legs: null`, `meta: null`, `routeGeometries: null` — frontend hooks must gracefully handle null arrays.
8. **`WS /ws/live` STOMP broker**: Frontend `liveUpdatesManager` must set `brokerURL` to `ws://localhost:5005/ws/live` for local dev (default points at external server).
9. **Station classification**: `GET /stops/classify` computes classifications from today's network data. `GET /search/stops?classification=hub` filters search results by class. Classification is cached for the process lifetime.
9. **Station classification**: `GET /stops/classify` computes classifications from today's network data. `GET /search/stops?classification=hub` filters search results by class. Classification is cached for the process lifetime.

---

## 8. `WS /ws/live` — WebSocket/STOMP Live Updates

**Purpose:** Real-time push of live transport data via STOMP 1.2 over WebSocket.

### Connection

```
ws://localhost:5005/ws/live
```

The endpoint speaks STOMP 1.2 and is compatible with `@stomp/stompjs` v7.
The frontend `liveUpdatesManager` can connect by passing the URL as `brokerURL`.

### Supported STOMP Topics

| Topic                      | Description                        | Broadcast Interval |
|----------------------------|------------------------------------|--------------------|
| `/topic/BUS_MVT_ALL`       | Live bus vehicle positions         | ~15 s (polling)    |
| `/topic/TRAIN_MVT_ALL_TOC` | Train movement updates             | Placeholder        |
| `/topic/TD_ALL_SIG_AREA`   | Train signal-area updates          | Placeholder        |
| `/topic/SERVICE_ALERTS`    | Service disruption alerts          | Placeholder        |

### `/topic/BUS_MVT_ALL` Message Shape

```json
{
  "type": "bus_positions",
  "count": 5,
  "vehicles": [
    {
      "line": "1",
      "destination": "Lancaster",
      "lat": 54.046,
      "lon": -2.798,
      "operator": "Stagecoach Cumbria & North Lancashire",
      "timestamp": 1740000000.0
    }
  ],
  "timestamp": 1740000000.0
}
```

### Client Protocol Flow

```
Client                           Server
  |  CONNECT                        |
  |  accept-version:1.2             |
  |  heart-beat:4000,4000           |
  | ─────────────────────────────►  |
  |                                 |
  |  CONNECTED                      |
  |  version:1.2                    |
  |  heart-beat:0,0                 |
  | ◄─────────────────────────────  |
  |                                 |
  |  SUBSCRIBE                      |
  |  id:sub-0                       |
  |  destination:/topic/BUS_MVT_ALL |
  | ─────────────────────────────►  |
  |                                 |
  |  MESSAGE (periodic)             |
  |  subscription:sub-0             |
  |  destination:/topic/BUS_MVT_ALL |
  |  content-type:application/json  |
  |  body: {…vehicles…}            |
  | ◄─────────────────────────────  |
  |                                 |
  |  DISCONNECT                     |
  |  receipt:rcpt-1                 |
  | ─────────────────────────────►  |
  |  RECEIPT                        |
  |  receipt-id:rcpt-1              |
  | ◄─────────────────────────────  |
```

---

## 10. `GET /walking/status`

**Purpose:** Walking-engine health — reports OSRM availability, config, and
precomputed transfer counts.  Useful for the frontend to decide whether to
offer walking directions or show a degraded-mode indicator.

### Response

| Field               | Type    | Notes                                       |
|---------------------|---------|---------------------------------------------|
| `osrm_url`          | string  | Configured OSRM base URL                    |
| `osrm_available`    | boolean | `true` if OSRM responds to a probe request  |
| `max_walk_seconds`  | int     | Maximum walk duration considered (default 600) |
| `precomputed_stops` | int     | Stops with outgoing precomputed transfers    |
| `stops_with_coords` | int     | Stops with known coordinates                 |

```json
{
  "osrm_url": "http://localhost:5321",
  "osrm_available": false,
  "max_walk_seconds": 600,
  "precomputed_stops": 1200,
  "stops_with_coords": 4500
}
```

---

## 11. `GET /walking/reachable?lat=<lat>&lon=<lon>&limit=<n>`

**Purpose:** Return transit stops reachable by walking from an arbitrary
location.  Uses OSRM when available; falls back to the precomputed
inter-walk table augmented with haversine estimates.

### Query Parameters

| Param   | Type  | Default | Required | Notes                       |
|---------|-------|---------|----------|-----------------------------|
| `lat`   | float | —       | Yes      | Latitude of start point     |
| `lon`   | float | —       | Yes      | Longitude of start point    |
| `limit` | int   | `20`    | No       | Max stops to return         |

### Response

| Field            | Type    | Notes                                    |
|------------------|---------|------------------------------------------|
| `location`       | object  | `{lat, lon}` — echoed input              |
| `osrm_available` | boolean | Whether OSRM was used for this request   |
| `stops`          | array   | Nearby walkable stops sorted by time ↑   |

Each stop:

| Field          | Type        | Notes                              |
|----------------|-------------|------------------------------------|
| `stop_index`   | int         | Internal merged-data stop integer  |
| `name`         | string      | Human-readable stop name           |
| `walk_seconds` | int         | Walking time from the input point  |
| `atco_code`    | string/null | NaPTAN ATCO code (null if unknown) |
| `lat`          | number      | Latitude (omitted if unknown)      |
| `lon`          | number      | Longitude (omitted if unknown)     |

```json
{
  "location": {"lat": 54.048, "lon": -2.801},
  "osrm_available": false,
  "stops": [
    {
      "stop_index": 42,
      "name": "Lancaster Bus Station",
      "walk_seconds": 30,
      "atco_code": "2500LAA15791",
      "lat": 54.048,
      "lon": -2.801
    }
  ]
}
```

### Notes

10. Walking fallback strategy: when OSRM is unreachable the engine uses the
    precomputed inter-walk table (originally computed via OSRM during data
    loading) augmented with haversine distance estimates at 1 m/s.  The
    `osrm_available` field in the response tells the frontend which mode
    was used.  Set `OSRM_URL` env var to point at a custom OSRM instance.
