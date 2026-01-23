import CssBaseline from "@mui/material/CssBaseline";
import { ThemeProvider, createTheme } from '@mui/material/styles';

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
	return (
		<ThemeProvider theme={theme}>
			{/* Baseline CSS (e.g. padding: 0) and enable automatic use of the user's color scheme */}
			<CssBaseline enableColorScheme />
			<nav>Navigation Bar Goes Here</nav>
			<main>
				{children}
			</main>
		</ThemeProvider>
	)
}