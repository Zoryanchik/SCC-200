import { Link } from "@mui/material";
import { Link as RouterLink } from 'react-router-dom';

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
			<Link component={RouterLink} to="/">
				Go Home
			</Link>
		</>
	)
}