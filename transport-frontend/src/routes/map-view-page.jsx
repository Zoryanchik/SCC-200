import { Link } from "@mui/material";
import { Link as RouterLink } from 'react-router-dom';
import { MapContainer, TileLayer, Marker, Popup } from 'react-leaflet';
import "leaflet/dist/leaflet.css";

/**
 * The Map View page, where the user can visually see and plot routes
 * between stations.
 * 
 * @returns Map View Page Element
 */
export default function MapViewPage() {
	return (
		<>
			<h1>Map View Page</h1>

			{/** Create a basic map container with Leaflet. N.B.: height must be specified */}
			<MapContainer center={[54.050556, -2.800556]} zoom={14} scrollWheelZoom={true} style={{ height: 536, width: 536 }}>
				<TileLayer
					attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
					url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
				/>
				<Marker position={[54.050556, -2.800556]}>
					<Popup>
						{/** TODO Dummy Information */}
						<h4>Lancaster Bus Station</h4>
						<p>Station Information</p>
					</Popup>
				</Marker>
			</MapContainer>

			<Link component={RouterLink} to="/">
				Go Home
			</Link>
		</>
	)
}