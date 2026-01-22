import { useRouteError } from "react-router-dom";

/**
 * The Error Page is displayed whenever an unexpected error occurs,
 * such as a Not Found error.
 * 
 * @returns Error Page Element
 */
export default function ErrorPage() {
	const error = useRouteError();

	// Log the error too, for debugging
	console.error(error);

	return (
		<>
			<h1>Error!</h1>
			<p>An error has occurred.</p>
			<p>
				{/* Display the error */ error.statusText || error.message}
			</p>
		</>
	);
}
