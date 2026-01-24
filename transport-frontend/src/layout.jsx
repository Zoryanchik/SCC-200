import CssBaseline from "@mui/material/CssBaseline";
import AppBar from "@mui/material/AppBar";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Container from "@mui/material/Container";
import Stack from "@mui/material/Stack";
import Toolbar from "@mui/material/Toolbar";
import Typography from "@mui/material/Typography";
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { Link as RouterLink, useLocation } from "react-router-dom";

/*
	Create a custom theme which allows for dark mode.
*/
const theme = createTheme({
	colorSchemes: {
		dark: true,
	},
});

/**
 * Define the outer wrapper for all pages.
 * This includes all common features such as the navigation bar.
 * 
 * @param {children} props The child(ren) to render, most likely a ____Page Element
 * @returns The App Layout Wrapper, containing the main page content
 */
export default function AppLayout({ children }) {
	const { pathname } = useLocation();

	return (
		<ThemeProvider theme={theme}>
			{/* Baseline CSS (e.g. padding: 0) and enable automatic use of the user's color scheme */}
			<CssBaseline enableColorScheme />
			<AppBar position="sticky" color="primary" enableColorOnDark>
				<Toolbar>
					<Container maxWidth="lg" disableGutters>
						<Stack direction="row" alignItems="center" justifyContent="space-between" spacing={3}>
							<Typography variant="h6" fontWeight={700} sx={{ letterSpacing: 0.4 }}>
								Lancaster Transport
							</Typography>
							<Stack direction="row" spacing={1.5}>
								<Button
									component={RouterLink}
									to="/"
									color={pathname === "/" ? "secondary" : "inherit"}
									variant={pathname === "/" ? "contained" : "text"}
									sx={{ textTransform: "none", fontWeight: 600 }}
								>
									Home
								</Button>
								<Button
									component={RouterLink}
									to="/map-view"
									color={pathname.startsWith("/map-view") ? "secondary" : "inherit"}
									variant={pathname.startsWith("/map-view") ? "contained" : "text"}
									sx={{ textTransform: "none", fontWeight: 600 }}
								>
									Map
								</Button>
							</Stack>
						</Stack>
					</Container>
				</Toolbar>
			</AppBar>
			<Box component="main" sx={{ bgcolor: "background.default", minHeight: "100vh", pb: 6 }}>
				<Container maxWidth="lg" sx={{ pt: 4 }}>
					{children}
				</Container>
			</Box>
		</ThemeProvider>
	)
}