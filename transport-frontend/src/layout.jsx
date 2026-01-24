import { useState } from "react";
import CssBaseline from "@mui/material/CssBaseline";
import AppBar from "@mui/material/AppBar";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Container from "@mui/material/Container";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Toolbar from "@mui/material/Toolbar";
import Typography from "@mui/material/Typography";
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { Link as RouterLink, useLocation } from "react-router-dom";
import { Moon, Sun } from "lucide-react";

/**
 * Define the outer wrapper for all pages.
 * This includes all common features such as the navigation bar.
 * 
 * @param {children} props The child(ren) to render, most likely a ____Page Element
 * @returns The App Layout Wrapper, containing the main page content
 */
export default function AppLayout({ children }) {
	const { pathname } = useLocation();
	const [mode, setMode] = useState("dark");

	const theme = createTheme({
		palette: {
			mode: mode,
		},
	});

	const toggleTheme = () => {
		setMode((prevMode) => (prevMode === "light" ? "dark" : "light"));
	};

	return (
		<ThemeProvider theme={theme}>
			{/* Baseline CSS (e.g. padding: 0) and enable automatic use of the user's color scheme */}
			<CssBaseline enableColorScheme />
			<AppBar position="sticky" color="default" enableColorOnDark>
				<Toolbar>
					<Container maxWidth="lg" disableGutters>
						<Stack direction="row" alignItems="center" justifyContent="space-between" spacing={3}>
							<Typography variant="h6" fontWeight={700} sx={{ letterSpacing: 0.4 }}>
								Lancaster Transport
							</Typography>
							<Stack direction="row" spacing={1.5} alignItems="center">
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
								<IconButton 
									onClick={toggleTheme} 
									color="inherit"
									aria-label="Toggle theme"
									size="small"
								>
									{mode === "dark" ? <Sun size={20} /> : <Moon size={20} />}
								</IconButton>
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