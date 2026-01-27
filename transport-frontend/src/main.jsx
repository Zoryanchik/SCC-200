import { StrictMode, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import {
  createBrowserRouter,
  RouterProvider,
} from "react-router-dom";
import App from "./app";
import ErrorPage from './error-page';
import AppLayout from './layout';
import './styles.css';

// Lazy load route components for better code splitting
const HomePage = lazy(() => import('./routes/home-page'));
const MapViewPage = lazy(() => import('./routes/map-view-page'));

// Loading component
const LoadingFallback = () => (
  <AppLayout>
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh' }}>
      <p>Loading...</p>
    </div>
  </AppLayout>
);

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
        element: (
          <Suspense fallback={<LoadingFallback />}>
            <HomePage />
          </Suspense>
        )
      },
      {
        path: "map-view/",
        element: (
          <Suspense fallback={<LoadingFallback />}>
            <MapViewPage />
          </Suspense>
        )
      }
    ],
  },
]);

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
