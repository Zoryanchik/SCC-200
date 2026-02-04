/**
 * Departure Card Component
 * Displays bus/train departure information with status
 */

import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Chip from "@mui/material/Chip";
import Box from "@mui/material/Box";
import { memo } from "react";
import { Bus, Train, Clock } from "lucide-react";

export const DepartureCard = memo(function DepartureCard({ departure }) {
  const isDelayed = departure.status?.toLowerCase().includes('delayed');
  const isCancelled = departure.status?.toLowerCase().includes('cancel');

  return (
    <Paper
      variant="outlined"
      sx={{
        p: 1.5,
        borderColor: isCancelled ? '#d32f2f' : isDelayed ? '#f57c00' : 'divider',
        backgroundColor: isCancelled ? 'rgba(211, 47, 47, 0.04)' : isDelayed ? 'rgba(245, 124, 0, 0.04)' : 'transparent'
      }}
    >
      <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={2}>
        <Stack direction="row" spacing={1.5} alignItems="center" flex={1} minWidth={0}>
          {departure.type === "bus" ? (
            <Bus size={18} color="#1976d2" style={{ flexShrink: 0 }} />
          ) : (
            <Train size={18} color="#2e7d32" style={{ flexShrink: 0 }} />
          )}
          <Box minWidth={0}>
            <Typography fontWeight={700} variant="body2">
              {departure.route}
            </Typography>
            <Typography variant="caption" color="text.secondary" noWrap>
              {departure.destination}
            </Typography>
          </Box>
        </Stack>

        <Stack alignItems="flex-end" spacing={0.5} flexShrink={0}>
          <Stack direction="row" spacing={0.5} alignItems="center">
            <Clock size={14} color="inherit" />
            <Typography color="primary" fontWeight={700} variant="body2">
              {departure.time}
            </Typography>
          </Stack>
          <Chip
            label={departure.status}
            size="small"
            color={isCancelled ? "error" : isDelayed ? "warning" : "success"}
            variant={isDelayed || isCancelled ? "filled" : "outlined"}
          />
        </Stack>
      </Stack>
    </Paper>
  );
});

export default DepartureCard;
