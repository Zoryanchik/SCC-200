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
