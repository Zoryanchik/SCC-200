import Box from "@mui/material/Box";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import { MapPin, Bus, Train } from "lucide-react";
import { MapContainer, Marker, Popup, TileLayer, useMap } from 'react-leaflet';
import L from 'leaflet';
import icon from 'leaflet/dist/images/marker-icon.png';
import iconShadow from 'leaflet/dist/images/marker-shadow.png';
import "leaflet/dist/leaflet.css";
import { useMemo, useState, useEffect } from "react";
import WeatherWidget from "../components/common/WeatherWidget";
import { useLiveBusLocations, useLiveDepartures } from "../hooks/useTransportData";

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

// Create custom icons for buses and trains
const createCustomIcon = (type, color) => {
	const svg = `<svg width="40" height="40" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
		<circle cx="20" cy="20" r="18" fill="white" stroke="${color}" stroke-width="3"/>
		${type === 'bus' 
			? '<path d="M12 14h16v8H12z" fill="none" stroke="' + color + '" stroke-width="2" stroke-linecap="round"/><circle cx="16" cy="24" r="2" fill="' + color + '"/><circle cx="24" cy="24" r="2" fill="' + color + '"/>' 
			: '<path d="M20 12l-6 4v8h12v-8z" fill="none" stroke="' + color + '" stroke-width="2"/><line x1="14" y1="24" x2="26" y2="24" stroke="' + color + '" stroke-width="2"/>'}
	</svg>`;
	
	return L.divIcon({
		html: svg,
		className: 'custom-marker-icon',
		iconSize: [40, 40],
		iconAnchor: [20, 20],
		popupAnchor: [0, -20]
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


// Mock data for instant display
const MOCK_MARKERS = [
	{ id: 1, position: [54.050556, -2.800556], name: "Lancaster Bus Station", type: "bus", status: "On time" },
	{ id: 2, position: [54.048889, -2.802500], name: "Lancaster Train Station", type: "train", status: "On time" },
	{ id: 3, position: [54.064560, -2.798890], name: "Lancaster City Center Stop", type: "bus", status: "On time" },
	{ id: 4, position: [54.045000, -2.810000], name: "Greyhound Bus Park", type: "bus", status: "Delayed 2 mins" },
	{ id: 5, position: [53.995000, -2.700000], name: "Morecambe Station", type: "train", status: "On time" },
	{ id: 6, position: [53.990000, -2.750000], name: "Morecambe Bus Station", type: "bus", status: "On time" },
	{ id: 7, position: [53.760000, -2.700000], name: "Preston Bus Station", type: "bus", status: "On time" },
	{ id: 8, position: [53.750000, -2.680000], name: "Preston Train Station", type: "train", status: "Delayed 5 mins" },
	{ id: 9, position: [54.080000, -2.700000], name: "Carnforth Station", type: "train", status: "On time" },
	{ id: 10, position: [54.120000, -2.650000], name: "Kendal Bus Station", type: "bus", status: "On time" },
];

export default function MapViewPage() {
	const [markers, setMarkers] = useState(MOCK_MARKERS); // Start with mock data for instant display
	const [filters, setFilters] = useState({
		showBuses: true,
		showTrains: true
	});
	const [openPopupId, setOpenPopupId] = useState(null);
	const [apiError, setApiError] = useState(null);
	const [userLocation, setUserLocation] = useState(null);
	const [locationStatus, setLocationStatus] = useState('idle');
	const [locationError, setLocationError] = useState(null);
	const [mapInstance, setMapInstance] = useState(null);

	// Fetch real data from API in background using correct operator codes
	// SCCU = Stagecoach Cumbria & North Lancashire
	const { data: busLocations, loading: busLoading, error: busError } = useLiveBusLocations('SCCU', 30000);
	const { data: trainDepartures, loading: trainLoading, error: trainError } = useLiveDepartures('LAN', 30000);

	// Update markers when real API data arrives
	useEffect(() => {
		console.log('🚌 Bus API Response:', { busLocations, busError });
		console.log('🚂 Train API Response:', { trainDepartures, trainError });
		
		// Only update if we have real data from the API
		if ((Array.isArray(busLocations) && busLocations.length > 0) || 
		    (Array.isArray(trainDepartures) && trainDepartures.length > 0)) {
			
			console.log('✅ Received real data from API, updating markers...');
			const newMarkers = [];
			let id = 1;

			// Add bus locations
			if (Array.isArray(busLocations) && busLocations.length > 0) {
				busLocations.forEach(bus => {
					newMarkers.push({
						id: id++,
						position: [bus.latitude || bus.lat, bus.longitude || bus.lon],
						name: bus.name || `Bus ${bus.id}`,
						type: 'bus',
						status: bus.status || 'On time',
						routeNumber: bus.routeNumber || bus.route
					});
				});
			}

			// Add train departures
			if (Array.isArray(trainDepartures) && trainDepartures.length > 0) {
				trainDepartures.forEach(train => {
					newMarkers.push({
						id: id++,
						position: [train.latitude || train.lat, train.longitude || train.lon],
						name: train.station || train.name || 'Train Station',
						type: 'train',
						status: train.status || train.delayMinutes ? `Delayed ${train.delayMinutes} mins` : 'On time',
						destination: train.destination,
						departureTime: train.departureTime || train.scheduledTime
					});
				});
			}

			setMarkers(newMarkers);
			setApiError(null);
		} else if (busError || trainError) {
			// Show error message but keep mock data
			setApiError('Using demo data - API temporarily unavailable');
		}
	}, [busLocations, trainDepartures, busError, trainError]);

	const filteredMarkers = markers.filter(m => 
		(m.type === 'bus' && filters.showBuses) || 
		(m.type === 'train' && filters.showTrains)
	);

	const distanceMeters = useMemo(() => {
		const toRadians = (deg) => (deg * Math.PI) / 180;
		return (a, b) => {
			const R = 6371000;
			const dLat = toRadians(b[0] - a[0]);
			const dLon = toRadians(b[1] - a[1]);
			const lat1 = toRadians(a[0]);
			const lat2 = toRadians(b[0]);
			const x = Math.sin(dLat / 2) ** 2 + Math.sin(dLon / 2) ** 2 * Math.cos(lat1) * Math.cos(lat2);
			const c = 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
			return R * c;
		};
	}, []);

	const nearestStop = useMemo(() => {
		if (!userLocation || !markers.length) return null;
		let nearest = null;
		for (const marker of markers) {
			const distance = distanceMeters(userLocation, marker.position);
			if (!nearest || distance < nearest.distance) {
				nearest = { ...marker, distance };
			}
		}
		return nearest;
	}, [userLocation, markers, distanceMeters]);

	const closestStops = useMemo(() => {
		if (!userLocation || !markers.length) return [];
		const withDistance = markers.map((marker) => ({
			...marker,
			distance: distanceMeters(userLocation, marker.position)
		}));
		return withDistance.sort((a, b) => a.distance - b.distance).slice(0, 3);
	}, [userLocation, markers, distanceMeters]);

	const formatWalkTime = (distance) => {
		const minutes = Math.max(1, Math.round(distance / 84)); // ~1.4 m/s walking speed
		return `${minutes} min walk`;
	};

	const requestLocation = () => {
		if (!navigator.geolocation) {
			setLocationStatus('error');
			setLocationError('Geolocation is not supported by this browser.');
			return;
		}

		setLocationStatus('loading');
		setLocationError(null);
			navigator.geolocation.getCurrentPosition(
			(position) => {
				const coords = [position.coords.latitude, position.coords.longitude];
				setUserLocation(coords);
				setLocationStatus('granted');
				if (mapInstance) {
					mapInstance.flyTo(coords, Math.max(mapInstance.getZoom(), 12), { duration: 1.2 });
				}
			},
			(error) => {
				setLocationStatus('error');
				setLocationError(error?.message || 'Location permission denied.');
			},
			{ enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
		);
	};

	const handleCenterOnUser = () => {
		if (userLocation && mapInstance) {
			mapInstance.flyTo(userLocation, Math.max(mapInstance.getZoom(), 12), { duration: 1.2 });
		}
	};

	const MapController = ({ onReady }) => {
		const map = useMap();
		useEffect(() => {
			onReady(map);
		}, [map, onReady]);
		return null;
	};

	return (
		<Stack spacing={2} sx={{ height: '100%', mb: 2 }}>
			<Paper elevation={0} sx={{ 
				p: 3.5, 
				background: 'linear-gradient(135deg, #6366F1 0%, #EC4899 100%)',
				color: 'white',
				borderRadius: '16px'
			}}>
				<Stack direction="row" spacing={1.5} alignItems="center">
					<MapPin size={26} />
					<Typography variant="h5" fontWeight={700}>
						Live Transport Map
					</Typography>
				</Stack>
				<Typography variant="body2" sx={{ color: 'rgba(255,255,255,0.95)', mt: 1.5, fontWeight: 500 }}>
					Real-time bus and train locations across Lancashire
				</Typography>
			</Paper>

			{apiError && (
				<Alert severity="warning" sx={{ borderRadius: '12px' }}>
					<Typography variant="body2">
						⚠️ Note: Using mock data as fallback. To see real data, ensure the API at <code>https://transport.scc.lancs.ac.uk</code> is accessible. Error: {apiError}
					</Typography>
				</Alert>
			)}

			<Paper elevation={0} sx={{ 
				p: 3, 
				borderRadius: '16px',
				border: '1px solid',
				borderColor: 'divider'
			}}>
				<Stack direction="row" spacing={1.5} mb={2} flexWrap="wrap" alignItems="center">
					<Box
						onClick={() => setFilters(f => ({ ...f, showBuses: !f.showBuses }))}
						sx={{
							padding: '12px 20px',
							border: `2px solid ${filters.showBuses ? '#6366F1' : '#E2E8F0'}`,
							borderRadius: '10px',
							display: 'flex',
							alignItems: 'center',
							gap: 1,
							cursor: 'pointer',
							backgroundColor: filters.showBuses ? '#6366F1' : 'transparent',
							color: filters.showBuses ? 'white' : 'inherit',
							fontWeight: 600,
							transition: 'all 0.3s ease',
							'&:hover': {
								boxShadow: '0 4px 12px rgba(99,102,241,0.25)',
								transform: 'translateY(-2px)',
								borderColor: '#6366F1',
								backgroundColor: filters.showBuses ? '#4F46E5' : 'rgba(99,102,241,0.08)'
							},
							'&:active': {
								transform: 'translateY(0px)'
							}
						}}
					>
						<Bus size={20} />
						Buses {filteredMarkers.filter(m => m.type === 'bus').length}
					</Box>
					<Box
						onClick={() => setFilters(f => ({ ...f, showTrains: !f.showTrains }))}
						sx={{
							padding: '12px 20px',
							border: `2px solid ${filters.showTrains ? '#10B981' : '#E2E8F0'}`,
							borderRadius: '10px',
							display: 'flex',
							alignItems: 'center',
							gap: 1,
							cursor: 'pointer',
							backgroundColor: filters.showTrains ? '#10B981' : 'transparent',
							color: filters.showTrains ? 'white' : 'inherit',
							fontWeight: 600,
							transition: 'all 0.3s ease',
							'&:hover': {
								boxShadow: '0 4px 12px rgba(16,185,129,0.25)',
								transform: 'translateY(-2px)',
								borderColor: '#10B981',
								backgroundColor: filters.showTrains ? '#059669' : 'rgba(16,185,129,0.08)'
							},
							'&:active': {
								transform: 'translateY(0px)'
							}
						}}
					>
						<Train size={20} />
						Trains {filteredMarkers.filter(m => m.type === 'train').length}
					</Box>

					<Box sx={{ flex: 1 }} />
					<Button
						variant="outlined"
						size="small"
						onClick={requestLocation}
						disabled={locationStatus === 'loading'}
						sx={{ borderRadius: '10px', textTransform: 'none' }}
					>
						{locationStatus === 'loading' ? (
							<Stack direction="row" spacing={1} alignItems="center">
								<CircularProgress size={16} />
								<Typography variant="caption">Locating…</Typography>
							</Stack>
						) : (
							'Use my location'
						)}
					</Button>
					{userLocation && (
						<Button
							variant="contained"
							size="small"
							onClick={handleCenterOnUser}
							sx={{ borderRadius: '10px', textTransform: 'none' }}
						>
							Center on me
						</Button>
					)}
				</Stack>

				{locationError && (
					<Alert severity="warning" sx={{ borderRadius: '10px', mb: 2 }}>
						<Typography variant="body2">{locationError}</Typography>
					</Alert>
				)}

				{userLocation && nearestStop && (
					<Stack direction="row" spacing={1.5} alignItems="center" mb={2} flexWrap="wrap">
						<Chip
							label={`Nearest: ${nearestStop.name}`}
							variant="outlined"
							sx={{ borderRadius: '10px' }}
						/>
						<Chip
							label={`${(nearestStop.distance / 1000).toFixed(1)} km away`}
							color="warning"
							variant="outlined"
							sx={{ borderRadius: '10px' }}
						/>
						{locationStatus === 'watching' && (
							<Chip
								label="Following your location"
								color="success"
								variant="outlined"
								sx={{ borderRadius: '10px' }}
							/>
						)}
					</Stack>
				)}

				{userLocation && closestStops.length > 0 && (
					<Paper elevation={0} sx={{
						p: 2,
						mb: 2,
						borderRadius: '12px',
						border: '1px solid',
						borderColor: 'divider',
						backgroundColor: 'rgba(99,102,241,0.04)'
					}}>
						<Stack spacing={1}>
							<Typography variant="subtitle2" fontWeight={700}>
								Closest stops
							</Typography>
							{closestStops.map((stop) => (
								<Stack key={stop.id} direction="row" spacing={1} alignItems="center" justifyContent="space-between">
									<Stack direction="row" spacing={1} alignItems="center">
										<Chip
											label={stop.type === 'bus' ? 'Bus' : 'Train'}
											size="small"
											color={stop.type === 'bus' ? 'primary' : 'success'}
											variant="outlined"
											sx={{ borderRadius: '10px' }}
										/>
										<Typography variant="body2" fontWeight={600}>
											{stop.name}
										</Typography>
									</Stack>
									<Typography variant="caption" color="text.secondary">
										{(stop.distance / 1000).toFixed(1)} km · {formatWalkTime(stop.distance)}
									</Typography>
								</Stack>
							))}
						</Stack>
					</Paper>
				)}

				{/* Map and Weather Widget side by side */}
				<Stack direction="row" spacing={3} sx={{ height: 750 }}>
					{/* Map Container */}
					<Box sx={{ 
						flex: 1,
						position: 'relative',
						borderRadius: '12px',
						overflow: 'hidden',
						boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
						border: '1px solid',
						borderColor: 'divider'
					}}>
						{(busLoading || trainLoading) && (
							<Box sx={{
								position: 'absolute',
								inset: 0,
								zIndex: 1000,
								backgroundColor: 'rgba(255,255,255,0.7)',
								display: 'flex',
								alignItems: 'center',
								justifyContent: 'center'
							}}>
								<Stack spacing={1} alignItems="center">
									<CircularProgress size={28} />
									<Typography variant="caption" fontWeight={600}>Loading live locations…</Typography>
								</Stack>
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
							scrollWheelZoom 
							style={{ height: "100%", width: "100%" }}
							className="leaflet-container-custom"
						>
							<MapController onReady={setMapInstance} />
							<TileLayer
								attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
									url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
								/>
								{filteredMarkers.map((marker) => (
									<Marker 
										key={marker.id} 
										position={marker.position}
										icon={createCustomIcon(marker.type, marker.type === 'bus' ? '#1976d2' : '#2e7d32')}
										eventHandlers={{
											click: () => setOpenPopupId(marker.id)
										}}
									>
										{openPopupId === marker.id && (
											<Popup 
												onClose={() => setOpenPopupId(null)}
												autoClose={false}
											>
												<Box sx={{ minWidth: '200px', pb: 1 }}>
													<Typography variant="subtitle2" fontWeight={700} sx={{ mb: 0.5 }}>
														{marker.name}
													</Typography>
													<Typography variant="caption" display="block" color="text.secondary" sx={{ mb: 1 }}>
														{marker.type === 'bus' ? '🚌 Bus Station' : '🚂 Train Station'}
													</Typography>
													<Box sx={{
														display: 'inline-block',
														padding: '4px 12px',
														borderRadius: '12px',
														backgroundColor: marker.status === 'On time' ? '#e8f5e9' : '#ffebee',
														color: marker.status === 'On time' ? '#2e7d32' : '#c62828',
														fontSize: '12px',
														fontWeight: '600',
														marginBottom: '8px'
													}}>
														{marker.status === 'On time' ? '✓' : '⚠'} {marker.status}
													</Box>
												</Box>
											</Popup>
										)}
									</Marker>
								))}

								{userLocation && (
									<Marker
										position={userLocation}
										icon={createUserIcon()}
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
					</Box>

					{/* Weather Widget - Separate box on the right */}
					<Box sx={{ minWidth: 320 }}>
						<WeatherWidget />
					</Box>
				</Stack>
			</Paper>
		</Stack>
	)
}