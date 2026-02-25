/**
 * Weather Widget Component
 * Draggable weather display for the map
 */

import React, { useState, useEffect } from 'react';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import IconButton from '@mui/material/IconButton';
import Box from '@mui/material/Box';
import { Cloud, CloudRain, Sun, Wind, Droplets, X } from 'lucide-react';
import { fetchWeatherData } from '../../services/transportApi';

export function WeatherWidget() {
  const DEFAULT_LOCATION = { lat: 54.050556, lon: -2.800556 };
  // Mock data so something shows up always
  const [weather, setWeather] = useState({
    temp: 12,
    condition: 'Partly Cloudy',
    humidity: 65,
    windSpeed: 15,
    icon: 'cloud'
  });
  const [visible, setVisible] = useState(true);
  // Default to Lancaster
  const [location, setLocation] = useState(DEFAULT_LOCATION);

  // Get the current location, or default to lancaster if not available
  const getLocation = () => {
    navigator.geolocation.getCurrentPosition(
        pos => setLocation({ lat: pos.coords.latitude, lon: pos.coords.longitude }), 
        () => setLocation(DEFAULT_LOCATION));
  }

  // Fetch the user's location every minute
  useEffect(() => {
    getLocation();
    const interval = setInterval(getLocation, 60000);

    return () => clearInterval(interval);
  }, []);

  // Poll the server for weather and update
  const updateWeather = async () => {
    const result = await fetchWeatherData(location.lat, location.lon);

    //TODO: Better icons for the weather
    const icon = result.weather[0].main.toLowerCase().includes("cloud") ? 'cloud' : 
      result.weather[0].maintoLowerCase().includes("rain") ? 'cloudRain' : 'sun';

    setWeather({
      temp: Math.round(result.main.temp),
      humidity: result.main.humidity,
      condition: result.weather[0].main,
      windSpeed: result.wind.speed,
      icon: icon,
    });
  };

  useEffect(() => {
    updateWeather();
  }, [location]);

  const getWeatherIcon = () => {
    switch (weather.icon) {
      case 'cloudRain':
        return <CloudRain size={32} color="#2196F3" />;
      case 'sun':
        return <Sun size={32} color="#FFC107" />;
      default:
        return <Cloud size={32} color="#9E9E9E" />;
    }
  };

  if (!visible) return null;

  return (
    <Paper
      sx={{
        p: 3,
        cursor: 'default',
        minWidth: '100%',
        background: 'linear-gradient(135deg, #6366F1 0%, #EC4899 100%)',
        color: 'white',
        boxShadow: '0 8px 24px rgba(99,102,241,0.25)',
        borderRadius: '16px',
        userSelect: 'none',
        transition: 'box-shadow 0.3s ease',
        '&:hover': {
          boxShadow: '0 12px 32px rgba(99,102,241,0.35)'
        },
        height: '100%',
        display: 'flex',
        flexDirection: 'column'
      }}
    >
      <Stack spacing={2.5} sx={{ height: '100%' }}>
        {/* Header with close button */}
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Typography variant="h6" fontWeight={700}>
            Weather
          </Typography>
          <IconButton
            size="small"
            onClick={() => setVisible(false)}
            sx={{ 
              color: 'white', 
              '&:hover': { backgroundColor: 'rgba(255,255,255,0.2)' },
              transition: 'all 0.2s ease'
            }}
          >
            <X size={20} />
          </IconButton>
        </Box>

        {/* Temperature and condition */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2.5 }}>
          <Box sx={{ fontSize: '56px', lineHeight: 1 }}>
            {getWeatherIcon()}
          </Box>
          <Box>
            <Typography variant="h3" fontWeight={700} sx={{ lineHeight: 1 }}>
              {weather.temp}°C
            </Typography>
            <Typography variant="body1" sx={{ opacity: 0.95, fontWeight: 500, mt: 0.5 }}>
              {weather.condition}
            </Typography>
          </Box>
        </Box>

        {/* Weather details */}
        <Stack spacing={1.5} sx={{ pt: 2, borderTop: '1px solid rgba(255,255,255,0.2)' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Droplets size={20} />
            <Typography variant="body2" fontWeight={500}>Humidity: {weather.humidity}%</Typography>
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Wind size={20} />
            <Typography variant="body2" fontWeight={500}>Wind: {weather.windSpeed} km/h</Typography>
          </Box>
        </Stack>

        {/* Status info */}
        <Typography variant="caption" sx={{ opacity: 0.85, textAlign: 'center', pt: 1.5, mt: 'auto', fontWeight: 500 }}>
          💧 Updates every minute
        </Typography>
      </Stack>
    </Paper>
  );
}

export default WeatherWidget;
