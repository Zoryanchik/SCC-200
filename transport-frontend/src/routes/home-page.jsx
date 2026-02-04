import { useMemo, useState, useEffect } from "react";
import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import Grid from "@mui/material/Grid";
import InputAdornment from "@mui/material/InputAdornment";
import Paper from "@mui/material/Paper";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import { AlertCircle, Bus, Clock, MapPin, Navigation as NavIcon, Train, Heart } from "lucide-react";
import { useStopSearch, useFavoriteRoutes, useLiveDepartures, useServiceAlerts, useLiveUpdates } from "../hooks/useTransportData";
import { getJourneyPlans } from "../services/transportApi";
import DepartureCard from "../components/common/DepartureCard";
import RouteCard from "../components/common/RouteCard";

const MOCK_STOPS = [
  { id: 1, name: "Lancaster Bus Station", code: "LAN001" },
  { id: 2, name: "Lancaster Train Station", code: "LAN002" },
  { id: 3, name: "Morecambe Bus Station", code: "MOR001" },
  { id: 4, name: "Preston Bus Station", code: "PRE001" },
  { id: 5, name: "Blackpool North Station", code: "BLK001" }
];

const MOCK_ROUTES = [
  {
    id: 1,
    duration: "45 mins",
    transfers: 1,
    steps: [
      { type: "walk", duration: "5 mins", to: "Lancaster Station" },
      { type: "train", route: "Northern", duration: "30 mins", from: "Lancaster", to: "Preston" },
      { type: "walk", duration: "10 mins", to: "Destination" }
    ],
    price: "£5.20"
  },
  {
    id: 2,
    duration: "38 mins",
    transfers: 0,
    steps: [
      { type: "bus", route: "2", duration: "38 mins", from: "Lancaster", to: "Destination" }
    ],
    price: "£3.80"
  }
];

export default function HomePage() {
  const [fromLocation, setFromLocation] = useState("");
  const [toLocation, setToLocation] = useState("");
  const [selectedFromStop, setSelectedFromStop] = useState(null);
  const [selectedToStop, setSelectedToStop] = useState(null);
  const [isSearching, setIsSearching] = useState(false);
  const { favorites, saveFavorite, removeFavorite } = useFavoriteRoutes();
  const { results: fromStopResults, loading: fromLoading } = useStopSearch(fromLocation);
  const { results: toStopResults, loading: toLoading } = useStopSearch(toLocation);
  
  // Fetch real data from API
  const { alerts: serviceAlerts, loading: alertsLoading } = useServiceAlerts();
  const { data: departures, loading: departuresLoading } = useLiveDepartures('LAN');
  const { data: liveAlertUpdate, isConnected: alertsConnected } = useLiveUpdates('alerts');
  const [liveAlerts, setLiveAlerts] = useState([]);
  const [routes, setRoutes] = useState(MOCK_ROUTES); // Start with mock routes for instant display

  useEffect(() => {
    if (!liveAlertUpdate) return;
    const updates = Array.isArray(liveAlertUpdate) ? liveAlertUpdate : [liveAlertUpdate];
    const normalized = updates
      .map((alert, idx) => ({
        id: alert?.id || alert?.alertId || `${Date.now()}-${idx}`,
        severity: alert?.severity || alert?.level || "info",
        message: alert?.message || alert?.description || alert?.text || "Service update"
      }))
      .filter((alert) => alert.message);

    if (normalized.length === 0) return;

    setLiveAlerts((prev) => {
      const merged = [...normalized, ...prev];
      const seen = new Set();
      const deduped = [];
      for (const item of merged) {
        const key = `${item.severity}-${item.message}`;
        if (seen.has(key)) continue;
        seen.add(key);
        deduped.push(item);
      }
      return deduped.slice(0, 5);
    });
  }, [liveAlertUpdate]);

  // Transform departures data
  const liveDepartures = useMemo(() => {
    if (!Array.isArray(departures) || departures.length === 0) {
      return [
        { id: 1, type: "bus", route: "2", destination: "Blackpool", time: "2 mins", status: "On time" },
        { id: 2, type: "train", route: "Northern", destination: "Manchester", time: "5 mins", status: "Delayed 3 mins" },
        { id: 3, type: "bus", route: "100", destination: "Morecambe", time: "8 mins", status: "On time" },
      ];
    }
    return departures.slice(0, 3).map((dep, idx) => ({
      id: idx + 1,
      type: dep.type || "bus",
      route: dep.routeNumber || dep.route || "—",
      destination: dep.destination || dep.to || "Unknown",
      time: dep.minutesToDeparture ? `${dep.minutesToDeparture} mins` : dep.time || "—",
      status: dep.status || (dep.delayMinutes ? `Delayed ${dep.delayMinutes} mins` : "On time")
    }));
  }, [departures]);

  // Transform alerts data
  const alerts = useMemo(() => {
    const apiAlerts = Array.isArray(serviceAlerts) && serviceAlerts.length > 0
      ? serviceAlerts.slice(0, 3).map((alert, idx) => ({
          id: alert?.id || idx + 1,
          severity: alert?.severity || "info",
          message: alert?.message || alert?.description || "Service update"
        }))
      : [];

    const combined = [...liveAlerts, ...apiAlerts];
    if (combined.length > 0) {
      return combined.slice(0, 3);
    }

    return [
      { id: 1, severity: "warning", message: "M6 delays between J33-J36: 15 mins" },
      { id: 2, severity: "info", message: "Bus route 2 diversion via King Street" },
    ];
  }, [serviceAlerts, liveAlerts]);

  const allStops = useMemo(() => {
    const fromResults = fromLoading ? [] : (fromStopResults?.length ? fromStopResults : MOCK_STOPS);
    const toResults = toLoading ? [] : (toStopResults?.length ? toStopResults : MOCK_STOPS);
    return { from: fromResults, to: toResults };
  }, [fromLoading, toLoading, fromStopResults, toStopResults]);

  const handleSearch = async () => {
    if (!selectedFromStop || !selectedToStop) return;
    setIsSearching(true);
    try {
      const journeys = await getJourneyPlans(
        selectedFromStop?.code,
        selectedToStop?.code,
        new Date().toISOString()
      );
      setRoutes(Array.isArray(journeys) ? journeys : []);
    } catch (error) {
      console.error('Journey search error:', error);
      setRoutes(MOCK_ROUTES);
    } finally {
      setIsSearching(false);
    }
  };

  const handleSaveRoute = (route) => {
    saveFavorite({
      from: selectedFromStop?.code,
      fromName: selectedFromStop?.name,
      to: selectedToStop?.code,
      toName: selectedToStop?.name,
      ...route
    });
  };

  const isFavorited = (route) => {
    return favorites.some(fav => 
      fav.from === selectedFromStop?.code && 
      fav.to === selectedToStop?.code && 
      fav.id === route.id
    );
  };

  return (
    <Stack spacing={{ xs: 2, md: 3 }}>
      <Paper elevation={0} sx={{ 
        p: { xs: 2.5, md: 3.5 }, 
        background: 'linear-gradient(135deg, #6366F1 0%, #EC4899 100%)',
        color: 'white',
        borderRadius: '16px'
      }}>
        <Stack direction="row" spacing={1.5} alignItems="center">
          <Bus size={24} />
          <Typography variant="h5" fontWeight={700}>
            Dashboard
          </Typography>
          <Chip 
            label="Live" 
            sx={{ 
              fontWeight: 700,
              backgroundColor: 'rgba(255,255,255,0.25)',
              color: 'white'
            }} 
            size="small" 
          />
        </Stack>
      </Paper>

      <Paper elevation={0} sx={{ 
        p: { xs: 2, md: 3 }, 
        borderRadius: '16px',
        border: '1px solid',
        borderColor: 'divider',
        background: 'transparent'
      }}>
        <Stack direction="row" spacing={1.5} alignItems="center" mb={2}>
          <AlertCircle size={20} color="#EC4899" />
          <Typography variant="subtitle1" fontWeight={700}>Service alerts</Typography>
          {alertsConnected && (
            <Chip
              label="Live"
              size="small"
              color="primary"
              variant="outlined"
              sx={{ ml: 1 }}
            />
          )}
        </Stack>
        <Stack spacing={1.5}>
          {alertsLoading ? (
            <Stack spacing={1}>
              {[1, 2].map((i) => (
                <Skeleton key={i} height={44} variant="rounded" />
              ))}
            </Stack>
          ) : alerts.length > 0 ? (
            alerts.map(alert => (
              <Alert 
                key={alert.id} 
                severity={alert.severity === "warning" ? "warning" : "info"} 
                variant="outlined"
                sx={{ 
                  borderRadius: '8px',
                  backgroundColor: alert.severity === "warning" 
                    ? 'rgba(245, 158, 11, 0.05)'
                    : 'rgba(59, 130, 246, 0.05)'
                }}
              >
                {alert.message}
              </Alert>
            ))
          ) : (
            <Typography variant="body2" color="text.secondary">
              No service alerts right now.
            </Typography>
          )}
        </Stack>
      </Paper>

      <Grid container spacing={{ xs: 2, md: 3 }}>
        <Grid item xs={12} md={6}>
          <Paper elevation={0} sx={{ 
            p: { xs: 2.5, md: 3.5 }, 
            height: "100%",
            borderRadius: '16px',
            border: '1px solid',
            borderColor: 'divider'
          }}>
            <Stack spacing={2.5}>
              <Typography variant="h6" fontWeight={700}>Quick journey search</Typography>
              
              <Autocomplete
                freeSolo
                options={allStops.from}
                getOptionLabel={(option) => typeof option === 'string' ? option : option.name}
                value={selectedFromStop}
                onChange={(e, value) => {
                  setSelectedFromStop(value);
                  if (typeof value === 'object') setFromLocation(value.name);
                }}
                inputValue={fromLocation}
                onInputChange={(e, value) => setFromLocation(value)}
                loading={fromLoading}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="From"
                    InputProps={{
                      ...params.InputProps,
                      startAdornment: (
                        <InputAdornment position="start">
                          <MapPin size={18} />
                        </InputAdornment>
                      ),
                      endAdornment: fromLoading ? <CircularProgress color="inherit" size={20} /> : params.InputProps.endAdornment
                    }}
                  />
                )}
              />

              <Autocomplete
                freeSolo
                options={allStops.to}
                getOptionLabel={(option) => typeof option === 'string' ? option : option.name}
                value={selectedToStop}
                onChange={(e, value) => {
                  setSelectedToStop(value);
                  if (typeof value === 'object') setToLocation(value.name);
                }}
                inputValue={toLocation}
                onInputChange={(e, value) => setToLocation(value)}
                loading={toLoading}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="To"
                    InputProps={{
                      ...params.InputProps,
                      startAdornment: (
                        <InputAdornment position="start">
                          <NavIcon size={18} />
                        </InputAdornment>
                      ),
                      endAdornment: toLoading ? <CircularProgress color="inherit" size={20} /> : params.InputProps.endAdornment
                    }}
                  />
                )}
              />

              <Button 
                variant="contained" 
                size="large" 
                sx={{ alignSelf: "stretch" }}
                onClick={handleSearch}
                disabled={!selectedFromStop || !selectedToStop || isSearching}
              >
                {isSearching ? <CircularProgress size={24} color="inherit" /> : "Search routes"}
              </Button>

              {favorites.length > 0 && (
                <Box>
                  <Typography variant="caption" fontWeight={700} display="block" mb={1}>
                    Recent Journeys
                  </Typography>
                  <Stack spacing={1}>
                    {favorites.slice(0, 3).map((fav, idx) => (
                      <Box
                        key={idx}
                        onClick={() => {
                          setFromLocation(fav.fromName);
                          setToLocation(fav.toName);
                        }}
                        sx={{
                          p: 1,
                          borderRadius: 1,
                          backgroundColor: '#f5f5f5',
                          cursor: 'pointer',
                          '&:hover': { backgroundColor: '#eeeeee' }
                        }}
                      >
                        <Typography variant="caption" fontWeight={600}>
                          {fav.fromName} → {fav.toName}
                        </Typography>
                      </Box>
                    ))}
                  </Stack>
                </Box>
              )}
            </Stack>
          </Paper>
        </Grid>

        <Grid item xs={12} md={6}>
          <Paper elevation={1} sx={{ p: { xs: 2.5, md: 3 }, height: "100%" }}>
            <Stack spacing={2}>
              <Stack direction="row" spacing={1} alignItems="center">
                <Clock size={18} />
                <Typography variant="h6" fontWeight={700}>Nearby departures</Typography>
              </Stack>
              <Stack spacing={1.5}>
                {departuresLoading ? (
                  <Stack spacing={1}>
                    {[1, 2, 3].map((i) => (
                      <Skeleton key={i} height={80} variant="rounded" />
                    ))}
                  </Stack>
                ) : (
                  liveDepartures.map(dep => (
                    <DepartureCard key={dep.id} departure={dep} />
                  ))
                )}
              </Stack>
            </Stack>
          </Paper>
        </Grid>
      </Grid>

      <Paper elevation={1} sx={{ p: { xs: 2.5, md: 3 } }}>
        <Stack spacing={2}>
          <Typography variant="h6" fontWeight={700}>Suggested routes</Typography>
          {isSearching ? (
            <Stack spacing={2}>
              {[1, 2, 3].map(i => (
                <Skeleton key={i} height={120} variant="rounded" />
              ))}
            </Stack>
          ) : routes.length > 0 ? (
            <Stack spacing={2}>
              {routes.map(route => (
                <RouteCard 
                  key={route.id} 
                  route={route}
                  onSave={handleSaveRoute}
                  isSaved={isFavorited(route)}
                />
              ))}
            </Stack>
          ) : (
            <Typography variant="body2" color="text.secondary">
              No routes found. Try adjusting your search.
            </Typography>
          )}
        </Stack>
      </Paper>
    </Stack>
  );
}