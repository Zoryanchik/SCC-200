import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import {
  createBrowserRouter,
  RouterProvider,
} from "react-router-dom";
import App from "./app";
import ErrorPage from './error-page';
import HomePage from './routes/home-page';
import AppLayout from './layout';
import MapViewPage from './routes/map-view-page';

/*
  Set up routing for SPA (Single Page Application)
  Root Element is App.
  Error Page is wrapped in the same layout manager as Root.
  Routes:
    - / -> HomePage
    - /map-view -> MapViewPage
*/
const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    errorElement: 
      <AppLayout>
        <ErrorPage />
      </AppLayout>,
    children: [
      {
        index: true,
        element: <HomePage />
      },
      {
        path: "map-view/",
        element: <MapViewPage />
      }
    ],
  },
]);

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
