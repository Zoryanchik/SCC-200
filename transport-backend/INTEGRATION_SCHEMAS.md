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
| Param  | Type   | Default | Required |
|--------|--------|---------|----------|
| `q`    | string | `""`    | Yes      |
| `limit`| int    | `10`    | No       |

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
| `coords`| Array\<[lat,lon]\>| Array of `[lat, lon]` pairs     |
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
      "journey_destination": "Preston"
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
    { "id": "bus-1", "name": "Bus 40", "coords": [[54.049, -2.800], [53.759, -2.699]], "color": "#1a73e8" }
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
| `fetchRailDepartures()`      | `GET /rail/departures/{station}`         | ❌ Not implemented |
| `fetchWeatherData()`         | `GET /weather?lat=&lon=`                 | ❌ Not implemented |
| `fetchServiceAlerts()`       | `GET /alerts`                            | ❌ Not implemented |
| `fetchPricing()`             | `GET /pricing?from=&to=`                 | ❌ Not implemented |
| `fetchBusTimes()`            | `GET /bus/times/{stopCode}`              | ❌ Not implemented |
| `fetchBusArrivals()`         | `GET /bus/arrivals/{stopCode}`           | ❌ Not implemented |

### Key Integration Notes

1. **API_BASE_URL mismatch**: Frontend `transportApi.js` uses `https://transport.scc.lancs.ac.uk` as base URL. For local dev, this must be changed to `http://localhost:5005` or a proxy configured in vite.
2. **`/bus/live/{operator}` response shape**: Backend returns `{line, destination, lat, lon}`. Frontend mock tests already use this shape — confirmed aligned.
3. **`/search/stops` mixed types**: Backend returns both `type: "stop"` and `type: "location"` results. Frontend tests already handle this.
4. **`/journey/plan` geometry format**: `routeGeometries[*].coords` uses `[lat, lon]` pairs (not GeoJSON `[lon, lat]`). Frontend polyline rendering must respect this order.
5. **`POST /api/bus_live` removed**: The duplicate endpoint was removed. All consumers (including the mock HTML frontend) now use `GET /bus/live/{operator}`.
6. **CORS**: Backend allows `localhost:3000` and `localhost:5173`. Vite dev server (default 5173) is covered.
7. **`/journey/plan` error shape**: On failure, returns `success: false` with `legs: null`, `meta: null`, `routeGeometries: null` — frontend hooks must gracefully handle null arrays.
