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
import ErrorBoundary from "./components/common/ErrorBoundary";

/**
 * Define the outer wrapper for all pages.
 * This includes all common features such as the navigation bar and error handling.
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
			...(mode === 'light' ? {
				primary: {
					main: '#6366F1',
					light: '#818CF8',
					dark: '#4F46E5',
				},
				secondary: {
					main: '#EC4899',
				},
				background: {
					default: '#F8FAFC',
					paper: '#FFFFFF',
				},
			} : {
				primary: {
					main: '#6366F1',
					light: '#818CF8',
					dark: '#4F46E5',
				},
				secondary: {
					main: '#EC4899',
				},
				background: {
					default: '#0F172A',
					paper: '#1E293B',
				},
			}),
		},
		typography: {
			fontFamily: '"Inter", "Segoe UI", "Roboto", sans-serif',
			h1: { fontWeight: 700, fontSize: '2.5rem' },
			h2: { fontWeight: 700, fontSize: '2rem' },
			h3: { fontWeight: 700, fontSize: '1.75rem' },
			h4: { fontWeight: 700, fontSize: '1.5rem' },
			h5: { fontWeight: 600, fontSize: '1.25rem' },
			h6: { fontWeight: 600, fontSize: '1rem' },
			button: { fontWeight: 600, textTransform: 'none' },
		},
		components: {
			MuiAppBar: {
				styleOverrides: {
					root: {
						background: mode === 'light' 
							? 'linear-gradient(135deg, #FFFFFF 0%, #F8FAFC 100%)'
							: 'linear-gradient(135deg, #1E293B 0%, #0F172A 100%)',
						boxShadow: mode === 'light'
							? '0 1px 3px rgba(0,0,0,0.08)'
							: '0 1px 3px rgba(0,0,0,0.3)',
						borderBottom: mode === 'light' ? '1px solid #E2E8F0' : '1px solid #334155',
					}
				}
			},
			MuiButton: {
				styleOverrides: {
					contained: {
						borderRadius: '8px',
						boxShadow: 'none',
						transition: 'all 0.3s ease',
						'&:hover': {
							boxShadow: '0 4px 12px rgba(99,102,241,0.3)',
							transform: 'translateY(-2px)',
						}
					},
					outlined: {
						borderRadius: '8px',
						transition: 'all 0.3s ease',
						'&:hover': {
							boxShadow: '0 4px 12px rgba(99,102,241,0.15)',
						}
					},
					text: {
						transition: 'all 0.3s ease',
					}
				}
			},
			MuiPaper: {
				styleOverrides: {
					root: {
						borderRadius: '12px',
						transition: 'all 0.3s ease',
					}
				}
			},
			MuiTextField: {
				styleOverrides: {
					root: {
						'& .MuiOutlinedInput-root': {
							borderRadius: '8px',
							transition: 'all 0.3s ease',
						}
					}
				}
			}
		}
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
							<Typography 
								variant="h6" 
								fontWeight={700} 
								sx={{ letterSpacing: 0.4 }}
								component="h1"
							>
								Lancaster Transport
							</Typography>
							<Stack direction="row" spacing={1.5} alignItems="center">
								<Button
									component={RouterLink}
									to="/"
									color={pathname === "/" ? "secondary" : "inherit"}
									variant={pathname === "/" ? "contained" : "text"}
									sx={{ 
										textTransform: "none", 
										fontWeight: 600,
										'&:focus-visible': { outline: '2px solid', outlineOffset: 2 }
									}}
									aria-current={pathname === "/" ? "page" : undefined}
								>
									Home
								</Button>
								<Button
									component={RouterLink}
									to="/map-view"
									color={pathname.startsWith("/map-view") ? "secondary" : "inherit"}
									variant={pathname.startsWith("/map-view") ? "contained" : "text"}
									sx={{ 
										textTransform: "none", 
										fontWeight: 600,
										'&:focus-visible': { outline: '2px solid', outlineOffset: 2 }
									}}
									aria-current={pathname.startsWith("/map-view") ? "page" : undefined}
								>
									Map
								</Button>
								<IconButton 
									onClick={toggleTheme} 
									color="inherit"
									aria-label={`Switch to ${mode === "dark" ? "light" : "dark"} theme`}
									size="small"
									sx={{ '&:focus-visible': { outline: '2px solid', outlineOffset: 2 } }}
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
					<ErrorBoundary>
						{children}
					</ErrorBoundary>
				</Container>
			</Box>
		</ThemeProvider>
	)
}