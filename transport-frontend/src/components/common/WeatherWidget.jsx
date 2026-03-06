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

export function WeatherWidget({ compact = false, variant = 'full' }) {
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
  const [showDetails, setShowDetails] = useState(false);
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

  const getWeatherIcon = (size = 32, colorOverride) => {
    const color = colorOverride || (weather.icon === 'cloudRain' ? '#2196F3' : weather.icon === 'sun' ? '#FFC107' : '#9E9E9E');
    switch (weather.icon) {
      case 'cloudRain':
        return <CloudRain size={size} color={color} />;
      case 'sun':
        return <Sun size={size} color={color} />;
      default:
        return <Cloud size={size} color={color} />;
    }
  };

  if (!visible) return null;

  const mode = variant === 'inline' ? 'inline' : (compact ? 'compact' : variant);

  // Inline flattened pill (single-line) used in the map filter row.
  // Clicking the pill should show the details in a separate small box
  // (so the pill doesn't expand). We render the pill and, when
  // requested, a sibling details box to the right.
  if (mode === 'inline') {
    return (
      <Box sx={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 1 }}>
        <Box
          role="button"
          tabIndex={0}
          onClick={() => setShowDetails((s) => !s)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') setShowDetails((s) => !s);
          }}
          sx={(theme) => ({
            padding: '8px 16px',
            border: 'none',
            borderRadius: '10px',
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            backgroundColor: theme.palette.info ? theme.palette.info.light : '#EFF6FF',
            cursor: 'pointer',
            color: 'white',
            fontWeight: 600,
            minWidth: 140,
            minHeight: 40,
            '&:hover': { filter: 'brightness(0.98)' },
            outline: 'none',
          })}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 36 }}>
            {getWeatherIcon(18, 'white')}
          </Box>
          <Box sx={{ display: 'flex', flexDirection: 'column', lineHeight: 1 }}>
            <Typography variant="body2" sx={{ fontWeight: 700, lineHeight: 1 }}>
              {weather.temp}°C — {weather.condition}
            </Typography>
          </Box>
        </Box>

        {showDetails && (
          <Paper
            elevation={4}
            sx={(theme) => ({
              position: 'absolute',
              bottom: 'calc(100% + 8px)', // place above the pill
              left: '50%',
              transform: 'translateX(-50%)',
              p: '8px 16px',
              borderRadius: '10px',
                border: 'none',
                minWidth: 180,
                minHeight: 40,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                backgroundColor: theme.palette.info ? theme.palette.info.light : '#EFF6FF',
                color: 'white',
                zIndex: theme.zIndex.tooltip || 1300,
                boxShadow: theme.shadows[4],
                pointerEvents: 'auto',
                fontWeight: 600,
            })}
          >
              <Stack spacing={0.5} sx={{ color: 'white' }}>
                <Typography variant="body2" fontWeight={600} sx={{ color: 'white' }}>
                  Humidity: {weather.humidity}%
                </Typography>
                <Typography variant="body2" fontWeight={600} sx={{ color: 'white' }}>
                  Wind: {weather.windSpeed} km/h
                </Typography>
                <Typography variant="caption" sx={{ opacity: 0.95, color: 'white' }}>
                  Updates every minute
                </Typography>
              </Stack>
          </Paper>
        )}
      </Box>
    );
  }

  if (mode === 'compact') {
    return (
      <Paper
        sx={{
          p: 1,
          cursor: 'default',
          minWidth: 140,
          maxWidth: 220,
          background: 'linear-gradient(135deg, #6366F1 0%, #EC4899 100%)',
          color: 'white',
          boxShadow: '0 6px 18px rgba(99,102,241,0.18)',
          borderRadius: '12px',
          userSelect: 'none',
          display: 'flex',
          alignItems: 'center',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 44 }}>
            {getWeatherIcon(28)}
          </Box>
          <Box sx={{ flex: 1 }}>
            <Typography variant="h6" fontWeight={700} sx={{ lineHeight: 1 }}>
              {weather.temp}°C
            </Typography>
            <Typography variant="caption" sx={{ opacity: 0.95, display: 'block' }}>
              {weather.condition}
            </Typography>
            <Typography variant="caption" sx={{ opacity: 0.85, display: 'block' }}>
              Hum {weather.humidity}% • Wind {weather.windSpeed} km/h
            </Typography>
          </Box>
          <IconButton
            size="small"
            onClick={() => setVisible(false)}
            sx={{ 
              color: 'white', 
              '&:hover': { backgroundColor: 'rgba(255,255,255,0.12)' },
              ml: 0.5
            }}
          >
            <X size={16} />
          </IconButton>
        </Box>
      </Paper>
    );
  }

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
