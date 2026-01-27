/**
 * Route Card Component
 * Displays route details with pricing and duration
 */

import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Divider from "@mui/material/Divider";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import { Bus, Train, MapPin, Heart } from "lucide-react";

export function RouteCard({ route, onSave, isSaved = false }) {
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
              <Box>
                <Typography fontWeight={700} textTransform="capitalize" variant="body2">
                  {step.type}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {step.route ? `${step.route} - ` : ""}
                  {step.duration}
                  {step.from ? ` from ${step.from}` : ""}
                  {step.to ? ` to ${step.to}` : ""}
                </Typography>
              </Box>
            </Stack>
          ))}
        </Stack>
      </Stack>
    </Paper>
  );
}

export default RouteCard;
