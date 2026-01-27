# Quick Reference Guide - Lancaster Transport Frontend

## 🎯 Getting Started

### Install & Run
```bash
cd transport-frontend
npm install          # ✅ Already done
npm run dev         # Start development server
npm run build       # Build for production
npm run lint        # Check code style
```

### Project Structure
```
src/
├── services/        # API calls, WebSocket, routing
├── hooks/          # Custom React hooks
├── components/     # Reusable UI components
├── routes/         # Page components
└── styles.css      # Global styles
```

---

## 📚 API Reference

### Transport API
```javascript
import { fetchLiveBusLocations, fetchRailDepartures } from './services/transportApi';

// Fetch bus locations
const buses = await fetchLiveBusLocations('stagecoach');

// Fetch train departures
const trains = await fetchRailDepartures('LAN');

// Search for stops
const stops = await searchStops('Lancaster');

// Get journey plans
const routes = await getJourneyPlans('LAN001', 'PRE001', '14:30');
```

### RAPTOR Router
```javascript
import RAPTORRouter from './services/routePlanner';

const router = new RAPTORRouter(timetableData);
const routes = router.findRoute(
  'stop1',        // From
  'stop2',        // To
  82800,          // Departure time (seconds since midnight)
  3               // Max transfers
);
// Returns: [{ transfers, duration, arrivalTime }]
```

### Live Updates
```javascript
import { liveUpdatesManager } from './services/liveUpdates';

// Connect
await liveUpdatesManager.connect({
  brokerURL: 'ws://...',
  login: 'guest',
  passcode: 'guest'
});

// Subscribe
const subId = liveUpdatesManager.subscribeToTrainMovements((data) => {
  console.log('Train update:', data);
});

// Unsubscribe
liveUpdatesManager.unsubscribe(subId);

// Disconnect
await liveUpdatesManager.disconnect();
```

---

## 🪝 Custom Hooks

### Data Fetching Hooks
```javascript
import {
  useLiveBusLocations,      // Real-time bus tracking
  useLiveDepartures,        // Train schedule
  useBusArrivals,          // Bus predictions
  useStopSearch,           // Autocomplete search
  useJourneyPlans,         // Route planning
  useServiceAlerts,        // Disruption alerts
  usePricing,              // Fare information
  useLiveUpdates           // WebSocket data
} from './hooks/useTransportData';

// Example
const { data, loading, error } = useLiveDepartures('LAN', 30000);

if (loading) return <Skeleton />;
if (error) return <Alert>{error.message}</Alert>;
return data.map(d => <DepartureCard departure={d} />);
```

### Favorite Routes
```javascript
import { useFavoriteRoutes } from './hooks/useTransportData';

const { favorites, saveFavorite, removeFavorite } = useFavoriteRoutes();

// Save
saveFavorite({ from: 'LAN001', to: 'PRE001', duration: '30 mins' });

// Remove
removeFavorite('LAN001', 'PRE001');

// List
favorites.forEach(fav => console.log(fav));
```

---

## 🧩 Components

### DepartureCard
```jsx
import DepartureCard from './components/common/DepartureCard';

<DepartureCard 
  departure={{
    id: 1,
    type: 'bus',
    route: '2',
    destination: 'Blackpool',
    time: '5 mins',
    status: 'On time'
  }}
/>
```

### RouteCard
```jsx
import RouteCard from './components/common/RouteCard';

<RouteCard
  route={{
    id: 1,
    duration: '45 mins',
    transfers: 1,
    steps: [...],
    price: '£5.20'
  }}
  onSave={handleSave}
  isSaved={false}
/>
```

### ErrorBoundary
```jsx
import ErrorBoundary from './components/common/ErrorBoundary';

<ErrorBoundary>
  <YourComponent />
</ErrorBoundary>
```

---

## 🎨 Map Features

### Using the Map
```jsx
import MapViewPage from './routes/map-view-page';

// Component includes:
// - Live marker rendering
// - Filter controls (buses/trains)
// - Custom styled markers
// - Interactive popups
// - Responsive design
```

### Add Custom Markers
```jsx
const createCustomIcon = (IconComponent, color) => {
  return L.divIcon({
    html: renderToStaticMarkup(
      <IconComponent color={color} size={24} />
    ),
    className: 'custom-marker',
    iconSize: [30, 30],
    iconAnchor: [15, 15]
  });
};

<Marker 
  position={[54.050556, -2.800556]}
  icon={createCustomIcon(Bus, '#1976d2')}
>
  <Popup>Details</Popup>
</Marker>
```

---

## 🔧 Configuration

### API Endpoints
Edit `src/services/transportApi.js`:
```javascript
const API_BASE_URL = 'http://transport.scc.lancs.ac.uk';
```

### WebSocket Broker
Edit `src/services/liveUpdates.js`:
```javascript
const brokerURL = 'ws://transport.scc.lancs.ac.uk:61613';
```

### Theme
Edit `src/layout.jsx`:
```javascript
const theme = createTheme({
  palette: {
    mode: mode,  // 'light' or 'dark'
  },
});
```

---

## 🎯 Common Tasks

### Add a New Feature
1. Create hook in `src/hooks/` if data needed
2. Create component in `src/components/`
3. Use in page component in `src/routes/`
4. Import and use

### Add an API Endpoint
1. Add function to `src/services/transportApi.js`
2. Create corresponding hook in `src/hooks/useTransportData.js`
3. Use hook in components

### Style a Component
1. Use MUI `sx` prop for inline styles
2. Use `sx={{ '&:hover': {...} }}` for hover
3. Use `sx={{ display: { xs: 'none', md: 'block' } }}` for responsive
4. Add global styles to `src/styles.css`

### Handle Errors
1. API errors caught automatically
2. Use ErrorBoundary for component errors
3. Check loading/error states from hooks
4. Display Alert/Snackbar to users

---

## 📱 Responsive Breakpoints

```
xs: 0px      (mobile)
sm: 600px    (tablet)
md: 960px    (small desktop)
lg: 1280px   (desktop)
xl: 1920px   (large desktop)
```

Usage:
```jsx
<Grid item xs={12} sm={6} md={4}>
  Content
</Grid>

<Box sx={{ display: { xs: 'none', md: 'block' } }}>
  Desktop only
</Box>
```

---

## 🔍 Debugging

### Enable Debug Logging
```javascript
// Add to any service function
console.log('Variable:', variable);
console.error('Error:', error);
```

### Check Network Calls
Open DevTools → Network tab to see all API calls

### Check Component State
Use React DevTools browser extension

### Check WebSocket Connection
```javascript
console.log('Connected:', liveUpdatesManager.getConnectionStatus());
```

---

## 📦 Dependencies

### Core
- **react** (19.2.0) - UI framework
- **react-dom** - DOM rendering
- **react-router-dom** - Routing

### UI Components
- **@mui/material** - Material Design
- **@mui/icons-material** - Icon library

### Maps & Location
- **leaflet** - Map library
- **react-leaflet** - React wrapper

### Real-time
- **@stomp/stompjs** - WebSocket protocol

### Utilities
- **lucide-react** - Icon set
- **react-draggable** - Drag & drop

---

## ✅ Accessibility Checklist

When adding features:
- [ ] Add `aria-label` to icon buttons
- [ ] Use semantic HTML (`<h1>`, `<button>`)
- [ ] Ensure focus indicators visible
- [ ] Test with keyboard only
- [ ] Check color contrast (4.5:1)
- [ ] Support reduced motion
- [ ] Test with screen reader

---

## 🚀 Performance Tips

- Use `useMemo` for expensive calculations
- Use `useCallback` for function callbacks
- Debounce search input (default 500ms)
- Implement code splitting for large routes
- Lazy load components with `React.lazy()`
- Use virtual lists for long item lists

---

## 📞 Troubleshooting

| Issue | Solution |
|-------|----------|
| Build fails | Run `npm install` again |
| API not working | Check API_BASE_URL in transportApi.js |
| WebSocket errors | Verify broker URL and credentials |
| Styles not applying | Check if styles.css is imported in main.jsx |
| Component crashes | Check ErrorBoundary in layout.jsx |
| Search not working | Verify useStopSearch hook usage |
| Maps not loading | Check Leaflet CSS is imported |

---

## 📚 Resources

- [React Docs](https://react.dev)
- [MUI Components](https://mui.com)
- [Leaflet Docs](https://leafletjs.com)
- [RAPTOR Algorithm](https://www.microsoft.com/en-us/research/publication/raptor-scalable-multi-modal-transit-routing/)
- [STOMP Protocol](https://stomp.github.io)

---

## 🎓 Key Concepts

### Hooks Pattern
Uses custom hooks for all data fetching. Each hook manages its own state.

### Error Boundaries
Prevents entire app crash when component errors occur.

### Component Composition
Reusable components with clear props interface.

### Service Layer
Separation of API calls from components.

### localStorage
Persistent storage for favorites without backend.

---

## 📝 Code Examples

### Using the Homepage
```jsx
// Already implemented in src/routes/home-page.jsx
// Features:
// - Autocomplete search
// - Recent journeys
// - Skeleton loaders
// - Live departures
// - Suggested routes
// - Favorite button
```

### Using the Map
```jsx
// Already implemented in src/routes/map-view-page.jsx
// Features:
// - Custom markers
// - Filter controls
// - Interactive popups
// - Real-time updates
```

---

**Last Updated**: January 27, 2026  
**Status**: ✅ Production Ready
