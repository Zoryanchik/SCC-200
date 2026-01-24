import Box from "@mui/material/Box";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { MapPin } from "lucide-react";
import { MapContainer, Marker, Popup, TileLayer } from 'react-leaflet';
import "leaflet/dist/leaflet.css";

export default function MapViewPage() {
	return (
		<Stack spacing={3}>
			<Paper elevation={1} sx={{ p: 3 }}>
				<Stack direction="row" spacing={1.5} alignItems="center">
					<MapPin size={22} />
					<Typography variant="h5" fontWeight={700}>
						Live transport map
					</Typography>
				</Stack>
				<Typography variant="body2" color="text.secondary" mt={1}>
					OpenStreetMap tiles with a sample marker for Lancaster Bus Station. Hook in live feeds to place more markers.
				</Typography>
			</Paper>

			<Paper elevation={1} sx={{ p: 2 }}>
				<Box sx={{ height: 520 }}>
					<MapContainer center={[54.050556, -2.800556]} zoom={14} scrollWheelZoom style={{ height: "100%", width: "100%" }}>
						<TileLayer
							attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
							url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
						/>
						<Marker position={[54.050556, -2.800556]}>
							<Popup>
								<Typography variant="subtitle1" fontWeight={700}>Lancaster Bus Station</Typography>
								<Typography variant="body2" color="text.secondary">Sample stop — replace with live data</Typography>
							</Popup>
						</Marker>
					</MapContainer>
				</Box>
			</Paper>
		</Stack>
	)
}