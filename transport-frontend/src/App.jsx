import { Outlet } from "react-router-dom";
import AppLayout from "./layout";

/**
 * The Root node for all pages, which wraps their content within the global navigation.
 * 
 * @returns Root App Element
 */
export default function App() {
	return (
		<AppLayout>
			<Outlet />
		</AppLayout>
	)
}