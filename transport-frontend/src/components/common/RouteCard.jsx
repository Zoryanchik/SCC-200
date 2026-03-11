/**
 * Route Card Component
 * Displays route details with pricing and duration
 */

import React from "react";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Divider from "@mui/material/Divider";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Popover from "@mui/material/Popover";
import { memo } from "react";
import { Bus, Train, MapPin, Heart, Info } from "lucide-react";

export const RouteCard = memo(function RouteCard({ route, onSave, isSaved = false, isSelected = false, fullHeight = false }) {
  function ClassificationInfo({ classification }) {
    const buttonRef = React.useRef(null);
    const [open, setOpen] = React.useState(false);
    if (!classification) return null;
    const label = String(classification);
    const cap = label.charAt(0).toUpperCase() + label.slice(1);
    const id = `classification-popover-${label}`;
    return (
      <>
        <IconButton
          size="small"
          aria-label={`classification-${label}`}
          aria-describedby={open ? id : undefined}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); setOpen((s) => !s); }}
          ref={buttonRef}
          sx={{ ml: 1, p: 0.5 }}
        >
          <Info size={14} />
        </IconButton>
        <Popover
          id={id}
          open={open}
          anchorEl={buttonRef.current}
          onClose={() => setOpen(false)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        >
          <Box sx={{ p: 1, maxWidth: 240 }}>
            <Typography fontWeight={700}>{cap}</Typography>
          </Box>
        </Popover>
      </>
    );
  }
  const parseDurationToMinutes = (value) => {
    if (!value) return 0;
    if (typeof value === 'number') return value;
    const text = String(value).toLowerCase();
    const hoursMatch = text.match(/(\d+(?:\.\d+)?)\s*h/);
    const minutesMatch = text.match(/(\d+(?:\.\d+)?)\s*m/);
    const hours = hoursMatch ? parseFloat(hoursMatch[1]) : 0;
    const minutes = minutesMatch ? parseFloat(minutesMatch[1]) : 0;
    return Math.round(hours * 60 + minutes);
  };

  const totalWalkMinutes = (() => {
    if (typeof route.walkMinutes === 'number') return route.walkMinutes;
    if (typeof route.walkTime === 'number') return route.walkTime;
    if (typeof route.walkTime === 'string') return parseDurationToMinutes(route.walkTime);
    if (!Array.isArray(route.steps)) return 0;
    return route.steps
      .filter((s) => s.type === 'walk')
      .reduce((sum, s) => sum + parseDurationToMinutes(s.duration), 0);
  })();

  return (
    <Paper
      variant="outlined"
      sx={(theme) => ({
        p: 2,
        transition: 'all 0.3s',
        height: fullHeight ? '100%' : 'auto',
        minHeight: 160,
        display: 'flex',
        flexDirection: 'column',
        borderColor: isSelected ? undefined : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.grey[400]),
        color: isSelected ? undefined : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.grey[400]),
      })}
    >
      {/* Header (fixed) */}
      <Stack spacing={1.5} sx={{ flex: '0 0 auto' }}>
            <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
            <Stack spacing={0.5}>
              {/* Small label above the duration number */}
              <Typography
                variant="caption"
                sx={(theme) => ({ color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[600] : theme.palette.text.secondary), mb: 0.25 })}
              >
                Duration:
              </Typography>
              <Typography
                fontWeight={700}
                sx={() => ({ color: isSelected ? '#00bcd4' : '#ffffff' })}
              >
                {route.duration}
              </Typography>
              {/* Arrival caption and computed arrival time (if available) */}
              {typeof route?.initialDepartureSecs === 'number' && Number.isFinite(route.initialDepartureSecs) && Number.isFinite(route.totalSeconds) && (
                (() => {
                  const arr = Math.floor(route.initialDepartureSecs + route.totalSeconds);
                  const secsOfDay = ((arr % 86400) + 86400) % 86400; // normalize
                  const hh = Math.floor(secsOfDay / 3600).toString().padStart(2, '0');
                  const mm = Math.floor((secsOfDay % 3600) / 60).toString().padStart(2, '0');
                  const arrivalStr = `${hh}:${mm}`;
                  return (
                    <>
                      <Typography variant="caption" sx={(theme) => ({ color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[600] : theme.palette.text.secondary), mt: 0.5 })}>
                        Arrives at:
                      </Typography>
                      <Typography fontWeight={700} sx={() => ({ color: isSelected ? '#00bcd4' : '#ffffff' })}>
                        {arrivalStr}
                      </Typography>
                    </>
                  );
                })()
              )}
                <Typography
                  variant="caption"
                  sx={(theme) => ({ color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary) })}
                >
                {route.transfers === 0 ? 'Direct' : `${route.transfers || 0} transfer${(route.transfers || 0) !== 1 ? 's' : ''}`}
              </Typography>
              {totalWalkMinutes > 0 && (
                  <Typography
                    variant="caption"
                    sx={(theme) => ({ color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary) })}
                  >
                    Walk time: {totalWalkMinutes} min
                  </Typography>
              )}
            </Stack>
            <Stack alignItems="flex-end" spacing={0.5}>
              {route.price && (
                <>
                  <Typography
                    fontWeight={700}
                    sx={() => ({
                      lineHeight: 1.2,
                      color: route && route.isCheapest ? '#4CBB17' : '#FF8787',
                    })}
                  >
                    {route.price}
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={(theme) => ({ color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary), whiteSpace: 'nowrap' })}
                  >
                    approx. cost
                  </Typography>
                  {route.busLegs > 1 && (
                    <Typography
                      variant="caption"
                      sx={(theme) => ({ color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary), whiteSpace: 'nowrap' })}
                    >
                      {route.busLegs} × £2.10 single
                    </Typography>
                  )}
                  {route.busLegs === 1 && (
                    <Typography
                      variant="caption"
                      sx={(theme) => ({ color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary), whiteSpace: 'nowrap' })}
                    >
                      bus single ticket
                    </Typography>
                  )}
                </>
              )}
              {onSave && (
                <IconButton size="small" onClick={() => onSave(route)} color={isSaved ? "error" : "default"}>
                  <Heart size={18} fill={isSaved ? "currentColor" : "none"} />
                </IconButton>
              )}
            </Stack>
          </Stack>

        <Divider sx={{ my: 0.5 }} />
      </Stack>

  {/* Steps container — make non-scrollable so card content flows naturally */}
  <Box sx={{ overflowY: 'visible', overflowX: 'hidden', pr: 1, flex: '1 1 auto' }}>
        <Stack spacing={1}>
          {route.steps?.map((step, idx) => (
            <Box key={idx}>
              {/* Show starting stop for the first step */}
              {idx === 0 && step.from && (
                <Typography variant="body1" fontWeight={800} sx={{ mb: 0.5, color: (theme) => theme.palette.mode === 'light' ? theme.palette.grey[800] : '#f5f5dc' }}>
                  <Box
                    component="span"
                    sx={(theme) => ({
                      textDecoration: 'underline',
                      color: isSelected
                        ? (theme.palette.mode === 'light' ? '#8B5E3C' : '#f5f5dc')
                        : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.grey[400]),
                    })}
                  >
                    {step.from}
                    {step.from_classification && (
                      <Box component="span" sx={{ ml: 0 }}>
                        <ClassificationInfo classification={step.from_classification} />
                      </Box>
                    )}
                  </Box>
                </Typography>
              )}

              {/* Mode + duration line with a downward indicator */}
              <Stack direction="row" spacing={1} alignItems="flex-start">
                {/* bold vertical connector to visually join stops */}
                <Box sx={{ width: 28, display: 'flex', justifyContent: 'center' }}>
                  <Box sx={{ width: 6, bgcolor: isSelected ? '#00bcd4' : (theme) => (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.grey[400]), borderRadius: 3, minHeight: 36 }} />
                </Box>
                <Box sx={{ flex: 1 }}>
                  <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                    <Typography fontWeight={800} textTransform="capitalize" variant="body1">
                      {step.type === 'walk' ? 'Walk' : step.type === 'bus' ? 'Bus' : step.type}
                    </Typography>
                    {step.type === 'bus' && step.route && (
                      <Typography
                        variant="body1"
                        fontWeight={700}
                        component="span"
                        sx={(theme) => ({
                          display: 'inline-block',
                          px: 0.6,
                          py: 0.15,
                          borderRadius: 1,
                          backgroundColor: isSelected ? theme.palette.primary.main : 'transparent',
                          // In dark mode: unselected Line matches unselected location colour (grey[400]); selected Line is white.
                          color: theme.palette.mode === 'dark'
                            ? (isSelected ? '#ffffff' : theme.palette.grey[400])
                            : (isSelected ? theme.palette.text.primary : theme.palette.grey[800]),
                        })}
                      >
                        Line {step.route}
                      </Typography>
                    )}
                    <Typography
                      variant="body2"
                      component="span"
                      fontWeight={700}
                      sx={(theme) => ({ color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary) })}
                    >
                      {step.duration}
                    </Typography>
                  </Stack>

                  {/* For walk show arrival; for vehicles show service and dep/arr */}
                  {step.type === 'walk' ? (
                    <Typography
                      variant="caption"
                      data-testid={`step-time-${idx}`}
                      sx={(theme) => ({ display: 'block', mt: 0.5, color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary) })}
                      fontWeight={400}
                    >
                      {step.departure_time_with_offset ? `Dep ${step.departure_time_with_offset}` : ''}
                      {step.departure_time_with_offset && step.arrival_time_with_offset ? '  •  ' : ''}
                      {step.arrival_time_with_offset ? `Arr ${step.arrival_time_with_offset}` : ''}
                    </Typography>
                  ) : (
                    <>
                      {(step.journey_origin || step.journey_destination) && (
                        <Typography
                          variant="caption"
                          data-testid={`step-time-${idx}`}
                          sx={(theme) => ({ display: 'block', mt: 0.5, color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary) })}
                          fontWeight={400}
                        >
                            Service: {step.journey_origin || '?'} → {step.journey_destination || '?'}
                          </Typography>
                      )}
                      {(step.departure_time_with_offset || step.arrival_time_with_offset) && (
                        <Typography
                          variant="caption"
                          data-testid={`step-time-${idx}`}
                          sx={(theme) => ({ display: 'block', mt: 0.5, color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary) })}
                          fontWeight={400}
                        >
              {step.departure_time_with_offset ? `Dep ${step.departure_time_with_offset}` : ''}
              {step.departure_time_with_offset && step.arrival_time_with_offset ? '  •  ' : ''}
              {step.arrival_time_with_offset ? `Arr ${step.arrival_time_with_offset}` : ''}
              {step.type !== 'walk' ? ' (planned)' : ''}
                        </Typography>
                      )}
                    </>
                  )}

                  {/* Realtime / status indicators (kept below the times) */}
                  {step.delay_seconds != null && step.delay_seconds !== 0 && (step.realtime_departure_time_with_offset || step.realtime_arrival_time_with_offset) && (
                    <Typography variant="caption" fontWeight={700} color={step.delay_seconds > 0 ? "error.main" : "success.main"} data-testid={`step-realtime-${idx}`} sx={{ display: "block", mt: 0.5 }}>
                      {step.realtime_departure_time_with_offset ? `Dep ${step.realtime_departure_time_with_offset}` : ""}
                      {step.realtime_departure_time_with_offset && step.realtime_arrival_time_with_offset ? "  •  " : ""}
                      {step.realtime_arrival_time_with_offset ? `Arr ${step.realtime_arrival_time_with_offset}` : ""}
                      {" (expected)"}
                    </Typography>
                  )}

                  {step.status && step.status !== "On time" && (
                    <Typography variant="caption" fontWeight={700} color="error.main" data-testid={`step-delay-${idx}`} sx={{ display: "inline-block", mt: 0.5, px: 0.75, py: 0.15, borderRadius: 1, bgcolor: "error.50" }}>
                      ⚠ {step.status}
                    </Typography>
                  )}
                </Box>
              </Stack>

              {/* Destination stop for this step */}
              {step.to && (
                <Typography variant="body1" fontWeight={800} sx={{ mt: 0.5, mb: 1, color: (theme) => theme.palette.mode === 'light' ? '#8B5E3C' : '#f5f5dc' }}>
                  <Box
                    component="span"
                    sx={(theme) => ({
                      textDecoration: 'underline',
                      color: isSelected
                        ? (theme.palette.mode === 'light' ? '#8B5E3C' : '#f5f5dc')
                        : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.grey[400]),
                    })}
                  >
                    {step.to}
                    {step.to_classification && (
                      <Box component="span" sx={{ ml: 0 }}>
                        <ClassificationInfo classification={step.to_classification} />
                      </Box>
                    )}
                  </Box>
                </Typography>
              )}
            </Box>
          ))}
        </Stack>
      </Box>

    </Paper>
  );
});

export default RouteCard;
