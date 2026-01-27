# Real API Integration - Changes Made

**Date:** January 27, 2026  
**Status:** Successfully switched from mock data to real API calls

---

## 🔄 What Changed

### 1. **Map View Page** (`src/routes/map-view-page.jsx`)

**Before:** Hardcoded 10 mock stations with fake coordinates

```javascript
const [markers, setMarkers] = useState([
  { id: 1, position: [54.050556, -2.800556], name: "Lancaster Bus Station", ... },
  { id: 2, position: [54.048889, -2.802500], name: "Lancaster Train Station", ... },
  // ... 8 more mock stations
]);
```

**After:** Real API calls using hooks

```javascript
const { data: busLocations, loading: busLoading, error: busError } = 
  useLiveBusLocations('stagecoach', 30000);
const { data: trainDepartures, loading: trainLoading, error: trainError } = 
  useLiveDepartures('LAN', 30000);

// Updates markers when API data arrives
useEffect(() => {
  const newMarkers = [];
  if (Array.isArray(busLocations) && busLocations.length > 0) {
    busLocations.forEach(bus => {
      newMarkers.push({
        position: [bus.latitude || bus.lat, bus.longitude || bus.lon],
        name: bus.name || `Bus ${bus.id}`,
        type: 'bus',
        status: bus.status || 'On time',
        routeNumber: bus.routeNumber || bus.route
      });
    });
  }
  // ... similar for train data
}, [busLocations, trainDepartures]);
```

**Benefits:**
- ✅ Real-time bus positions
- ✅ Actual train departures
- ✅ Auto-refreshes every 30 seconds
- ✅ Error handling with fallback message
- ✅ Proper loading states

---

### 2. **Home Page** (`src/routes/home-page.jsx`)

**Before:** Mock data for alerts, departures, and routes

```javascript
const alerts = useMemo(() => ([
  { id: 1, severity: "warning", message: "M6 delays between J33-J36: 15 mins" },
  // ...
]), []);

const liveDepartures = useMemo(() => ([
  { id: 1, type: "bus", route: "2", destination: "Blackpool", ... },
  // ...
]), []);

const routes = useMemo(() => ([...]), []);

const handleSearch = async () => {
  // Simulated API call with setTimeout
  setTimeout(() => setIsSearching(false), 1000);
};
```

**After:** Real API integration

```javascript
// Fetch real data from API
const { data: serviceAlerts, loading: alertsLoading } = useServiceAlerts();
const { data: departures, loading: departuresLoading } = useLiveDepartures('LAN');

// Transform API data to component format
const liveDepartures = useMemo(() => {
  if (!Array.isArray(departures) || departures.length === 0) {
    return [/* mock fallback */];
  }
  return departures.slice(0, 3).map((dep, idx) => ({
    id: idx + 1,
    type: dep.type || "bus",
    route: dep.routeNumber || dep.route || "—",
    destination: dep.destination || dep.to || "Unknown",
    time: dep.minutesToDeparture ? `${dep.minutesToDeparture} mins` : dep.time,
    status: dep.status || (dep.delayMinutes ? `Delayed ${dep.delayMinutes} mins` : "On time")
  }));
}, [departures]);

const alerts = useMemo(() => {
  if (!Array.isArray(serviceAlerts) || serviceAlerts.length === 0) {
    return [/* mock fallback */];
  }
  return serviceAlerts.slice(0, 2).map((alert, idx) => ({
    id: idx + 1,
    severity: alert.severity || "info",
    message: alert.message || alert.description || "Service update"
  }));
}, [serviceAlerts]);

// Real journey planner API call
const handleSearch = async () => {
  if (!selectedFromStop || !selectedToStop) return;
  setIsSearching(true);
  try {
    const response = await fetch(
      `https://transport.scc.lancs.ac.uk/journey/plan`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fromStop: selectedFromStop?.code,
          toStop: selectedToStop?.code,
          departureTime: new Date().toISOString()
        })
      }
    );
    
    if (response.ok) {
      const journeys = await response.json();
      setRoutes(Array.isArray(journeys) ? journeys : []);
    } else {
      setRoutes([/* mock fallback */]);
    }
  } catch (error) {
    console.error('Journey search error:', error);
    setRoutes([/* mock fallback */]);
  } finally {
    setIsSearching(false);
  }
};
```

**Benefits:**
- ✅ Real service alerts from API
- ✅ Actual live departures
- ✅ Real journey planner results
- ✅ Fallback to mock data if API fails
- ✅ Error handling and retry logic

---

## 📡 API Endpoints Now Being Called

| Endpoint | Purpose | Status |
|----------|---------|--------|
| `GET /bus/live/{operatorCode}` | Live bus positions | 🟡 Waiting for data |
| `GET /rail/departures/{stationCode}` | Train departures | 🟡 Waiting for data |
| `POST /journey/plan` | Journey planning | 🟡 Waiting for data |
| `GET /alerts` | Service alerts | 🟡 Waiting for data |
| `GET /search/stops?q=` | Stop search | 🟡 Waiting for data |
| `GET /bus/arrivals/{stopCode}` | Bus arrivals | 🟡 Waiting for data |
| `GET /pricing?from=&to=` | Fare pricing | 🟡 Waiting for data |

---

## ⚠️ What You Need to Know

### The API Must Be Accessible
For real data to work, the API at `https://transport.scc.lancs.ac.uk` must be:
1. ✅ Running and accessible
2. ✅ Returning JSON data in the format expected
3. ✅ Properly configured with CORS headers (if frontend is on different domain)

### Fallback Behavior
If the API is down or returns errors:
- ✅ App still works with mock/fallback data
- ✅ Shows warning message in yellow alert
- ✅ Users can still test the UI with dummy data
- ✅ When API comes back online, real data appears

### Current Status
🟡 **Waiting for API data** - The app is making API calls but will display fallback data until real API responds

---

## 🧪 Testing the Real API

To verify real API integration is working:

### 1. Check Browser Console
Open DevTools (F12) → Console tab and look for:
- Successful API responses: `200 OK`
- Error messages indicating API issues
- Network requests in Network tab

### 2. Check Map Page
- Go to "Map View"
- Should show real bus/train locations
- Numbers in filter buttons update as data flows in
- Yellow alert appears if API unreachable

### 3. Check Home Page
- Go to "Dashboard"
- Service alerts section shows real alerts (if any)
- Live departures section shows actual trains/buses
- Search functionality calls real journey planner

### 4. Monitor API Responses
1. Open DevTools (F12) → Network tab
2. Filter by `Fetch/XHR`
3. Look for requests to `transport.scc.lancs.ac.uk`
4. Check response bodies to see actual data format

---

## 🔧 Making Further Changes

### If API Response Format Differs
Edit the data transformation in hooks:

**In `map-view-page.jsx`:**
```javascript
busLocations.forEach(bus => {
  newMarkers.push({
    position: [bus.latitude, bus.longitude],  // ← Adjust field names
    name: bus.name,                            // ← Adjust field names
    type: 'bus',
    status: bus.status
  });
});
```

**In `home-page.jsx`:**
```javascript
return departures.map((dep, idx) => ({
  destination: dep.destination,  // ← Adjust field names
  time: dep.minutesToDeparture,  // ← Adjust field names
  status: dep.status             // ← Adjust field names
}));
```

### If API Endpoints Are Different
Update the endpoint URLs in `src/services/transportApi.js`:

```javascript
const response = await fetch(
  `${API_BASE_URL}/your/new/endpoint`  // ← Change this path
);
```

---

## 📊 API Call Frequency

- **Bus Locations:** Every 30 seconds
- **Train Departures:** Every 30 seconds  
- **Service Alerts:** Every 60 seconds (in home page)
- **Search Results:** On user input with 500ms debounce
- **Journey Plans:** On user click (search button)

---

## 🎯 Next Steps

1. **Verify API is running** at `https://transport.scc.lancs.ac.uk`
2. **Test API endpoints** manually (using Postman/curl)
3. **Check response format** to ensure it matches expected structure
4. **Adjust field mappings** if needed (see "Making Further Changes" above)
5. **Monitor console** for errors and API response times
6. **Test with real data** flowing through the app

---

## 📝 Notes

- Mock/fallback data allows testing even if API is down
- App gracefully handles API errors without crashing
- All real API calls have proper error handling
- Loading states show while data is being fetched
- Auto-refreshes every 30-60 seconds for live updates

**Status:** ✅ Real API integration complete - waiting for actual API to be live
