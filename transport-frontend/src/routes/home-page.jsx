import { Link } from "@mui/material";
import { Link as RouterLink } from 'react-router-dom';

/**
 * The home, or dashboard, page.
 * This is the index page, and therefore the one accessed by '/'.
 * 
 * @returns Home Page Root Element
 */
export default function HomePage() {
	return (
		<>
			<h1>Home Page</h1>
			<Link component={RouterLink} to="/map-view">
				Go to Map View
			</Link>
		</>
	)
}