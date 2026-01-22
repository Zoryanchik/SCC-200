/**
 * Define the outer wrapper for all pages.
 * This includes all common features such as the navigation bar.
 * 
 * @param {children} props The child(ren) to render, most likely a ____Page Element
 * @returns 
 */
export default function AppLayout({ children }) {
	return (
		<>
			<nav>Hello Router</nav>
			<main>
				{children}
			</main>
		</>
	)
}