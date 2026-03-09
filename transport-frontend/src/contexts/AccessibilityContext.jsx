import { createContext, useContext, useState } from 'react';

const AccessibilityContext = createContext({
	highContrast: false,
	fontSize: 'normal', // 'normal' | 'large' | 'xlarge'
	setHighContrast: () => {},
	setFontSize: () => {},
});

/** Persist accessibility preferences across page reloads via localStorage. */
export function AccessibilityProvider({ children }) {
	const [highContrast, setHighContrastState] = useState(
		() => localStorage.getItem('a11y-hc') === 'true'
	);
	const [fontSize, setFontSizeState] = useState(
		() => localStorage.getItem('a11y-fs') || 'normal'
	);

	const setHighContrast = (v) => {
		setHighContrastState(v);
		localStorage.setItem('a11y-hc', String(v));
	};

	const setFontSize = (v) => {
		setFontSizeState(v);
		localStorage.setItem('a11y-fs', v);
	};

	return (
		<AccessibilityContext.Provider value={{ highContrast, setHighContrast, fontSize, setFontSize }}>
			{children}
		</AccessibilityContext.Provider>
	);
}

export function useAccessibility() {
	return useContext(AccessibilityContext);
}
