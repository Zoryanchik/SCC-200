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

export function WeatherWidget() {
  const [weather, setWeather] = useState({
    temp: 12,
    condition: 'Partly Cloudy',
    humidity: 65,
    windSpeed: 15,
    icon: 'cloud'
  });
  const [visible, setVisible] = useState(true);

  // Simulate weather data update
  useEffect(() => {
    const interval = setInterval(() => {
      const temps = [8, 10, 12, 14, 13, 11];
      const conditions = ['Cloudy', 'Partly Cloudy', 'Rainy', 'Sunny', 'Windy'];
      const icons = ['cloud', 'cloudRain', 'sun'];
      
      setWeather({
        temp: temps[Math.floor(Math.random() * temps.length)],
        condition: conditions[Math.floor(Math.random() * conditions.length)],
        humidity: Math.floor(Math.random() * 40) + 50,
        windSpeed: Math.floor(Math.random() * 20) + 5,
        icon: icons[Math.floor(Math.random() * icons.length)]
      });
    }, 60000); // Update every minute

    return () => clearInterval(interval);
  }, []);

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
