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
          <Box sx={{ p: 1, maxWidth: { xs: 240, md: 360 } }}>
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
  // Increased RouteCard max width by ~1.2x from current values per request
  width: '100%',
  // increased by 1.05× from the current values to be slightly wider on desktop
  maxWidth: { xs: '100%', md: 381, lg: 436 },
        boxSizing: 'border-box',
        borderColor: isSelected ? undefined : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.grey[400]),
        color: isSelected ? undefined : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.grey[400]),
      })}
    >
      {/* Header (fixed) */}
      <Stack spacing={1.5} sx={{ flex: '0 0 auto' }}>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr auto',
            columnGap: 2,
            rowGap: 0.5,
            alignItems: 'baseline',
          }}
        >
          {/* Row 1: labels */}
          <Typography variant="caption" sx={(theme) => ({ color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[600] : theme.palette.text.secondary) })}>
            Arrival
          </Typography>
          <Typography variant="caption" sx={(theme) => ({ color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[600] : theme.palette.text.secondary) })}>
            Transfer
          </Typography>
          <Typography
            variant="caption"
            sx={(theme) => ({
              color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[600] : theme.palette.text.secondary),
              justifySelf: 'end',
              whiteSpace: 'nowrap',
            })}
          >
            Cost
          </Typography>

          {/* Row 2: values (arrival / transfer / price) */}
          {(() => {
            const lastStep = Array.isArray(route.steps) && route.steps.length > 0
              ? route.steps[route.steps.length - 1]
              : null;
            const rawArrival = lastStep?.arrival_time_with_offset
              || lastStep?.scheduled_arrival_time
              || route.finalArrivalWithOffset
              || null;
            if (!rawArrival) return <Box />;

            let timeDisplay = rawArrival;
            let dayLabel = '';
            try {
              const dm = rawArrival.match(/\(\s*\+\s*(\d+)\s*d\s*\)/i);
              if (dm && dm[1]) {
                const n = Number(dm[1]);
                dayLabel = n === 1 ? ' (+1 day)' : ` (+${n} days)`;
              }
              if (!dayLabel && route.dayShift > 0) {
                dayLabel = route.dayShift === 1 ? ' (+1 day)' : ` (+${route.dayShift} days)`;
              }
              const core = rawArrival.split('(')[0].trim();
              const parts = core.split(':');
              timeDisplay = parts.length >= 2 ? `${parts[0]}:${parts[1]}` : core;
            } catch (e) {
              // keep rawArrival as-is
            }

            const arrivalColor = (route && typeof route.isEarliestArrival !== 'undefined')
              ? (route.isEarliestArrival ? '#4CBB17' : '#FF8787')
              : (isSelected ? '#00bcd4' : '#ffffff');

            return (
              <Typography fontWeight={700} sx={() => ({ color: arrivalColor })}>
                {timeDisplay}{dayLabel && <span style={{ fontWeight: 600, fontSize: '0.8em', marginLeft: 4 }}>{dayLabel}</span>}
              </Typography>
            );
          })()}

          {(() => {
            const trColor = (route && typeof route.isFewestTransfers !== 'undefined')
              ? (route.isFewestTransfers ? '#4CBB17' : '#FF8787')
              : (isSelected ? '#00bcd4' : '#ffffff');
            return (
              <Typography fontWeight={700} sx={() => ({ color: trColor })}>
                {route.transfers == null ? '—' : (route.transfers === 0 ? 'None' : String(route.transfers))}
              </Typography>
            );
          })()}

          {route.price ? (
            <Typography
              fontWeight={700}
              sx={() => ({
                lineHeight: 1.2,
                color: route && route.isCheapest ? '#4CBB17' : '#FF8787',
                justifySelf: 'end',
                whiteSpace: 'nowrap',
              })}
            >
              {route.price}
            </Typography>
          ) : (
            <Box />
          )}

          {/* Row 3: labels */}
          <Typography
            variant="caption"
            sx={(theme) => ({
              color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[600] : theme.palette.text.secondary),
              mb: 0.25,
            })}
          >
            Duration
          </Typography>
          <Typography variant="caption" sx={(theme) => ({ color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[600] : theme.palette.text.secondary) })}>
            Walk
          </Typography>
          {(() => {
            if (!route.busLegs) return <Box />;
            
            let standardCount = 0;
            let extendedCount = 0;
            (route.steps || []).forEach(s => {
              if (s.type === 'bus') {
                const stopsCount = (s.stops || s.path_stops || s.pathStops || s.intermediate_stops || s.intermediateStops || []).length;
                if (stopsCount > 25) extendedCount++;
                else standardCount++;
              }
            });
            
            const parts = [];
            if (standardCount > 0) parts.push(`£2/Single${standardCount > 1 ? `*${standardCount}` : ''}`);
            if (extendedCount > 0) parts.push(`£3/Single${extendedCount > 1 ? `*${extendedCount}` : ''}`);
            
            const text = parts.length > 0 ? `Bus ` + parts.join(' + ') : 'bus single ticket';
            
            return (
              <Typography
                variant="caption"
                sx={(theme) => ({
                  color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary),
                  whiteSpace: 'nowrap',
                  justifySelf: 'end',
                  fontStyle: 'italic',
                })}
              >
                {text}
              </Typography>
            );
          })()}

          {/* Row 4: values */}
          {(() => {
            const durColor = (route && typeof route.isFastestDuration !== 'undefined')
              ? (route.isFastestDuration ? '#4CBB17' : '#FF8787')
              : (isSelected ? '#00bcd4' : '#ffffff');
            return (
              <Typography fontWeight={700} sx={() => ({ color: durColor })}>
                {route.duration}
              </Typography>
            );
          })()}

          {(() => {
            const wColor = (route && typeof route.isLeastWalk !== 'undefined')
              ? (route.isLeastWalk ? '#4CBB17' : '#FF8787')
              : (isSelected ? '#00bcd4' : '#ffffff');
            return (
              <Typography fontWeight={700} sx={() => ({ color: wColor })}>
                {totalWalkMinutes > 0 ? `${totalWalkMinutes} min` : '0 min'}
              </Typography>
            );
          })()}

          <Box />

          {/* Row 5: actions */}
          <Box />
          <Box />
          <Box />
        </Box>

        <Divider sx={{ my: 0.5 }} />
      </Stack>

  {/* Steps container — make non-scrollable so card content flows naturally */}
  <Box sx={{ overflowY: 'visible', overflowX: 'hidden', pr: 1, flex: '1 1 auto' }}>
        <Stack spacing={1}>
          {route.steps?.map((step, idx) => (
            <Box key={idx}>
              {/* whether this step currently has a real-time delay/expected time */}
              {(() => {
                // keep a small inline helper in scope for styling decisions
                // (we can't declare `const` at top-level JSX easily)
                return null;
              })()}
              {/* Show starting stop for the first step */}
              {idx === 0 && step.from && (
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 0.5 }}>
                  <Typography variant="body1" fontWeight={800} sx={{ color: (theme) => theme.palette.mode === 'light' ? theme.palette.grey[800] : '#f5f5dc', overflow: 'hidden', textOverflow: 'ellipsis' }}>
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

                  <Box sx={{ minWidth: 72, textAlign: 'right', ml: 1 }}>
                    {(() => {
                      const isDelayed = step.delay_seconds != null && step.delay_seconds !== 0 && (step.realtime_departure_time_with_offset || step.realtime_arrival_time_with_offset);
                      return (
                        <Typography
                          variant="body2"
                          fontWeight={700}
                          sx={(theme) => ({
                            color: isSelected ? '#00bcd4' : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary),
                            fontSize: '1.05rem',
                            lineHeight: 1,
                            textDecoration: isDelayed && step.realtime_departure_time_with_offset ? 'line-through' : 'none',
                          })}
                        >
                          {step.departure_time_with_offset ? step.departure_time_with_offset : ''}
                        </Typography>
                      );
                    })()}
                  </Box>
                </Box>
              )}

              {/* Mode + duration line with a downward indicator */}
              <Stack direction="row" spacing={1} alignItems="flex-start">
                {/* bold vertical connector to visually join stops */}
                <Box sx={{ width: 28, display: 'flex', justifyContent: 'center' }}>
                  <Box sx={{ width: 6, bgcolor: isSelected ? '#00bcd4' : (theme) => (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.grey[400]), borderRadius: 3, minHeight: 36 }} />
                </Box>
                <Box sx={{ flex: 1 }}>
                  {/* Mode + duration row: left side shows mode/line, right side shows duration (aligned right) */}
                  <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" justifyContent="space-between">
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}>
                      <Typography fontWeight={800} textTransform="capitalize" variant="body1" sx={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
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
                            color: theme.palette.mode === 'dark'
                              ? (isSelected ? '#ffffff' : theme.palette.grey[400])
                              : (isSelected ? theme.palette.text.primary : theme.palette.grey[800]),
                          })}
                        >
                          Line {step.route}
                        </Typography>
                      )}
                    </Box>

                    <Box sx={{ minWidth: 72, textAlign: 'right', ml: 1 }}>
                      <Typography
                        variant="body2"
                        component="span"
                        fontWeight={700}
                        sx={(theme) => ({ color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary) })}
                      >
                        {step.duration}
                      </Typography>
                    </Box>
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
                            {step.journey_origin || '?'} → {step.journey_destination || '?'}
                          </Typography>
                      )}
                      {(step.departure_time_with_offset || step.arrival_time_with_offset) && (
                        (() => {
                          const isDelayed = step.delay_seconds != null && step.delay_seconds !== 0 && (step.realtime_departure_time_with_offset || step.realtime_arrival_time_with_offset);
                          return (
                            <Typography
                              variant="caption"
                              data-testid={`step-time-${idx}`}
                              sx={(theme) => ({ display: 'block', mt: 0.5, color: isSelected ? (theme.palette.mode === 'light' ? theme.palette.text.primary : '#ffffff') : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary), textDecoration: isDelayed ? 'line-through' : 'none' })}
                              fontWeight={400}
                            >
              {step.departure_time_with_offset ? `Dep ${step.departure_time_with_offset}` : ''}
              {step.departure_time_with_offset && step.arrival_time_with_offset ? '  •  ' : ''}
              {step.arrival_time_with_offset ? `Arr ${step.arrival_time_with_offset}` : ''}
              {/* removed '(planned)' label per UX request */}
                            </Typography>
                          );
                        })()
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
                /* Only show right-aligned arrival time for the final step (destination). Intermediate stops render without a right-aligned time. */
                (idx === (Array.isArray(route.steps) ? route.steps.length - 1 : 0)) ? (
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mt: 0.5, mb: 1 }}>
                    <Typography variant="body1" fontWeight={800} sx={{ color: (theme) => theme.palette.mode === 'light' ? theme.palette.grey[800] : '#f5f5dc', overflow: 'hidden', textOverflow: 'ellipsis' }}>
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

                    <Box sx={{ minWidth: 72, textAlign: 'right', ml: 1 }}>
                      {(() => {
                        const isDelayed = step.delay_seconds != null && step.delay_seconds !== 0 && (step.realtime_departure_time_with_offset || step.realtime_arrival_time_with_offset);
                        return (
                          <Typography
                            variant="body2"
                            fontWeight={700}
                            sx={(theme) => ({
                              color: isSelected ? '#00bcd4' : (theme.palette.mode === 'light' ? theme.palette.grey[800] : theme.palette.text.secondary),
                              fontSize: '1.05rem',
                              lineHeight: 1,
                              textDecoration: isDelayed && step.realtime_arrival_time_with_offset ? 'line-through' : 'none',
                            })}
                          >
                            {step.arrival_time_with_offset ? step.arrival_time_with_offset : ''}
                          </Typography>
                        );
                      })()}
                    </Box>
                  </Box>
                ) : (
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
                )
              )}
            </Box>
          ))}
        </Stack>
      </Box>

    </Paper>
  );
});

export default RouteCard;
