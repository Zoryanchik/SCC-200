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
import { memo } from "react";
import { Bus, Train, MapPin, Heart } from "lucide-react";

export const RouteCard = memo(function RouteCard({ route, onSave, isSaved = false }) {
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
      .filter((step) => step.type === 'walk')
      .reduce((sum, step) => sum + parseDurationToMinutes(step.duration), 0);
  })();

  return (
    <Paper variant="outlined" sx={{ p: 2, transition: 'all 0.3s', '&:hover': { elevation: 2 } }}>
      <Stack spacing={1.5}>
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
          <Stack spacing={0.5}>
            <Typography fontWeight={700} color="primary">
              {route.duration}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {route.transfers || 0} transfer{(route.transfers || 0) !== 1 ? 's' : ''}
            </Typography>
            {totalWalkMinutes > 0 && (
              <Typography variant="caption" color="text.secondary">
                Walk time: {totalWalkMinutes} min
              </Typography>
            )}
          </Stack>
          <Stack alignItems="flex-end" spacing={0.5}>
            <Typography fontWeight={700} color="success.main">
              {route.price}
            </Typography>
            {onSave && (
              <IconButton
                size="small"
                onClick={() => onSave(route)}
                color={isSaved ? "error" : "default"}
              >
                <Heart size={18} fill={isSaved ? "currentColor" : "none"} />
              </IconButton>
            )}
          </Stack>
        </Stack>

        <Divider sx={{ my: 0.5 }} />

        <Stack spacing={1}>
          {route.steps?.map((step, idx) => (
            <Stack key={idx} direction="row" spacing={1.5} alignItems="flex-start">
              {step.type === "walk" && <MapPin size={18} color="#6b7280" style={{ marginTop: 2 }} />}
              {step.type === "bus" && <Bus size={18} color="#1976d2" style={{ marginTop: 2 }} />}
              {step.type === "train" && <Train size={18} color="#2e7d32" style={{ marginTop: 2 }} />}
              <Box sx={{ flex: 1 }}>
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                  <Typography fontWeight={700} textTransform="capitalize" variant="body2">
                    {step.type}
                  </Typography>
                  {step.route ? (
                    <Typography variant="body2" fontWeight={600} color="primary" component="span">
                      Line {step.route}
                    </Typography>
                  ) : null}
                  <Typography variant="body2" color="text.secondary" component="span">
                    {step.duration}
                  </Typography>
                </Stack>

                {/* From → To */}
                {(step.from || step.to) && (
                  <Typography variant="body2" sx={{ mt: 0.25 }}>
                    {step.from ? <>{step.from}</> : null}
                    {step.from && step.to ? " → " : ""}
                    {step.to ? <>{step.to}</> : null}
                  </Typography>
                )}

                {/* Service origin → destination (for transit legs) */}
                {(step.journey_origin || step.journey_destination) && (
                  <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.25 }}>
                    Service: {step.journey_origin || "?"} → {step.journey_destination || "?"}
                  </Typography>
                )}

                {/* Departure / Arrival times */}
                {(step.departure_time_with_offset || step.arrival_time_with_offset) && (
                  <Typography variant="caption" fontWeight={500} color="text.secondary" data-testid={`step-time-${idx}`} sx={{ display: "block", mt: 0.25 }}>
                    {step.departure_time_with_offset ? `Dep ${step.departure_time_with_offset}` : ""}
                    {step.departure_time_with_offset && step.arrival_time_with_offset ? "  •  " : ""}
                    {step.arrival_time_with_offset ? `Arr ${step.arrival_time_with_offset}` : ""}
                  </Typography>
                )}
              </Box>
            </Stack>
          ))}
        </Stack>
      </Stack>
    </Paper>
  );
});

export default RouteCard;
