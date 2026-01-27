import Box from "@mui/material/Box";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Skeleton from "@mui/material/Skeleton";
import CircularProgress from "@mui/material/CircularProgress";
import Alert from "@mui/material/Alert";
import { MapPin, Bus, Train } from "lucide-react";
import { MapContainer, Marker, Popup, TileLayer } from 'react-leaflet';
import L from 'leaflet';
import icon from 'leaflet/dist/images/marker-icon.png';
import iconShadow from 'leaflet/dist/images/marker-shadow.png';
import "leaflet/dist/leaflet.css";
import { useState, useEffect } from "react";
import { renderToStaticMarkup } from 'react-dom/server';
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


export default function MapViewPage() {
	const [markers, setMarkers] = useState([]);
	const [filters, setFilters] = useState({
		showBuses: true,
		showTrains: true
	});
	const [openPopupId, setOpenPopupId] = useState(null);
	const [apiError, setApiError] = useState(null);

	// Fetch real data from API
	const { data: busLocations, loading: busLoading, error: busError } = useLiveBusLocations('stagecoach', 30000);
	const { data: trainDepartures, loading: trainLoading, error: trainError } = useLiveDepartures('LAN', 30000);

	// Update markers when API data arrives
	useEffect(() => {
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

		// If no data from API, use mock data as fallback
		if (newMarkers.length === 0) {
			const mockData = [
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
			setMarkers(mockData);
			if (busError || trainError) {
				setApiError('Using demo data - API temporarily unavailable');
			}
		} else {
			setMarkers(newMarkers);
			setApiError(null);
		}
	}, [busLocations, trainDepartures, busError, trainError]);

	const isLoading = busLoading || trainLoading;
	const filteredMarkers = markers.filter(m => 
		(m.type === 'bus' && filters.showBuses) || 
		(m.type === 'train' && filters.showTrains)
	);

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
				<Stack direction="row" spacing={1.5} mb={3}>
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
				</Stack>

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
					{isLoading ? (
							<Box
								sx={{
									height: '100%',
									display: 'flex',
									alignItems: 'center',
									justifyContent: 'center',
									flexDirection: 'column',
									gap: 2,
									backgroundColor: '#f5f5f5',
									borderRadius: 1
								}}
							>
								<CircularProgress />
								<Typography color="text.secondary">
									Loading map and stations...
								</Typography>
							</Box>
						) : (
							<MapContainer 
								center={[54.050556, -2.800556]} 
							zoom={10} 
								scrollWheelZoom 
								style={{ height: "100%", width: "100%" }}
								className="leaflet-container-custom"
							>
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
							</MapContainer>
						)}
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