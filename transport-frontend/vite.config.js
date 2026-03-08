import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react({
      babel: {
        plugins: [['babel-plugin-react-compiler']],
      },
    }),
  ],
  test: {
    environment: 'jsdom',
    setupFiles: [],
  },
  server: {
    // Default dev server port for this project
    port: 5075,
    // Note: do not bind to all interfaces by default here. Leaving
    // `host` unset ensures Vite prints the standard network hint
    // ("➜  Network: use --host to expose") and developers can opt-in
    // to exposing the server with `npm run dev -- --host` when needed.
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          // Vendor chunks
          'vendor-react': ['react', 'react-dom', 'react-router-dom'],
          'vendor-ui': ['@mui/material', '@mui/icons-material'],
          'vendor-map': ['leaflet', 'react-leaflet'],
          'vendor-utils': ['lucide-react', '@stomp/stompjs'],
          
          // Feature chunks
          'feature-map': ['./src/routes/map-view-page.jsx'],
          'feature-home': ['./src/routes/home-page.jsx'],
          
          // Service chunks
          'service-api': ['./src/services/transportApi.js'],
          'service-router': ['./src/services/routePlanner.js'],
          'service-live': ['./src/services/liveUpdates.js'],
          
          // Component chunks
          'components-common': ['./src/components/common/ErrorBoundary.jsx', './src/components/common/RouteCard.jsx', './src/components/common/DepartureCard.jsx', './src/components/common/WeatherWidget.jsx'],
        }
      }
    },
    chunkSizeWarningLimit: 1000,
  }
})
