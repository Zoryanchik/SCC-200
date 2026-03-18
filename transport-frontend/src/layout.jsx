import { useState, useEffect } from "react";
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
import { Moon, Sun, Contrast, Type } from "lucide-react";
import ErrorBoundary from "./components/common/ErrorBoundary";
import { useAccessibility } from "./contexts/AccessibilityContext";

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
	const { highContrast, setHighContrast, fontSize, setFontSize } = useAccessibility();

	// Apply font-size scale to HTML root so rem-based layouts respond
	const fontSizePx = { normal: 16, large: 18, xlarge: 20 }[fontSize] ?? 16;
	useEffect(() => {
		document.documentElement.style.fontSize = `${fontSizePx}px`;
	}, [fontSizePx]);

	// Sync high-contrast body class for CSS rules that can't be reached by MUI theme
	useEffect(() => {
		document.body.classList.toggle('high-contrast', highContrast);
	}, [highContrast]);

	// Cycle: normal → large → xlarge → normal
	const cycleFontSize = () => {
		const next = { normal: 'large', large: 'xlarge', xlarge: 'normal' };
		setFontSize(next[fontSize] ?? 'normal');
	};

	const hcPalette = mode === 'dark'
		? { primary: { main: '#FFD600' }, background: { default: '#000000', paper: '#111111' }, text: { primary: '#FFFFFF', secondary: '#DDDDDD' } }
		: { primary: { main: '#003399' }, background: { default: '#FFFFFF', paper: '#FFFFFF' }, text: { primary: '#000000', secondary: '#222222' } };

	const theme = createTheme({
		palette: {
			mode: mode,
			...(highContrast ? hcPalette : (mode === 'light' ? {
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
			})),
		},
		typography: {
			htmlFontSize: fontSizePx,
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
				<Toolbar sx={{ minHeight: { xs: 84, sm: 64 }, py: { xs: 1, sm: 0 } }}>
					<Container maxWidth={false} disableGutters sx={{ px: { xs: 1.25, sm: 2, md: 4 } }}>
						<Stack direction={{ xs: "column", sm: "row" }} alignItems={{ xs: "stretch", sm: "center" }} justifyContent="space-between" spacing={{ xs: 1, sm: 3 }}>
							<Typography 
								variant="h6" 
								fontWeight={700} 
								sx={{
									letterSpacing: 0.4,
									textAlign: { xs: "center", sm: "left" },
									fontSize: { xs: "1.05rem", sm: "1.25rem" },
									lineHeight: 1.2,
									whiteSpace: 'nowrap',
									minWidth: 0,
								}}
								component="h1"
							>
									Lancashire Transport
								</Typography>
							<Stack direction="row" spacing={1} alignItems="center" justifyContent={{ xs: "space-between", sm: "flex-end" }} sx={{ width: "100%", flexWrap: "wrap", rowGap: 0.75 }}>
								<Stack direction="row" spacing={1} sx={{ flexGrow: { xs: 1, sm: 0 } }}>
									<Button
										component={RouterLink}
										to="/"
										color={pathname === "/" ? "secondary" : "inherit"}
										variant={pathname === "/" ? "contained" : "text"}
										size="small"
										sx={{ 
											textTransform: "none", 
											fontWeight: 600,
											minWidth: { xs: 72, sm: 84 },
											px: { xs: 1.25, sm: 1.75 },
											flex: { xs: 1, sm: "0 0 auto" },
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
										size="small"
										sx={{ 
											textTransform: "none", 
											fontWeight: 600,
											minWidth: { xs: 72, sm: 84 },
											px: { xs: 1.25, sm: 1.75 },
											flex: { xs: 1, sm: "0 0 auto" },
											'&:focus-visible': { outline: '2px solid', outlineOffset: 2 }
										}}
										aria-current={pathname.startsWith("/map-view") ? "page" : undefined}
									>
										Map
									</Button>
								</Stack>
								<Stack direction="row" spacing={0.5} alignItems="center">
									<IconButton 
										onClick={toggleTheme} 
										color="inherit"
										aria-label={`Switch to ${mode === "dark" ? "light" : "dark"} theme`}
										size="small"
										sx={{ '&:focus-visible': { outline: '2px solid', outlineOffset: 2 } }}
									>
										{mode === "dark" ? <Sun size={20} /> : <Moon size={20} />}
									</IconButton>
									{/* Text scale: cycles normal → large → xlarge */}
									<IconButton
										onClick={cycleFontSize}
										color="inherit"
										aria-label={`Text size: ${fontSize} (click to change)`}
										title={`Text size: ${fontSize}`}
										size="small"
										sx={{ '&:focus-visible': { outline: '2px solid', outlineOffset: 2 } }}
									>
										<Type size={fontSize === 'normal' ? 16 : fontSize === 'large' ? 19 : 22} />
									</IconButton>
									{/* High-contrast toggle */}
									<IconButton
										onClick={() => setHighContrast(!highContrast)}
										color={highContrast ? 'primary' : 'inherit'}
										aria-label={`${highContrast ? 'Disable' : 'Enable'} high contrast`}
										aria-pressed={highContrast}
										title={highContrast ? 'Disable high contrast' : 'Enable high contrast'}
										size="small"
										sx={{ '&:focus-visible': { outline: '2px solid', outlineOffset: 2 } }}
									>
										<Contrast size={20} />
									</IconButton>
								</Stack>
							</Stack>
						</Stack>
					</Container>
				</Toolbar>
			</AppBar>
			<Box component="main" sx={{ bgcolor: "background.default", minHeight: "100dvh", pb: { xs: 3, md: 6 } }}>
				<Container maxWidth={false} sx={{ pt: { xs: 2, md: 4 }, px: { xs: 1.25, sm: 2, md: 4 } }}>
					<ErrorBoundary>
						{children}
					</ErrorBoundary>
				</Container>
			</Box>
		</ThemeProvider>
	)
}