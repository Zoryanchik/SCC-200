import { useMemo, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import Grid from "@mui/material/Grid";
import InputAdornment from "@mui/material/InputAdornment";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { AlertCircle, Bus, Clock, MapPin, Navigation as NavIcon, Train } from "lucide-react";

export default function HomePage() {
	const [fromLocation, setFromLocation] = useState("");
	const [toLocation, setToLocation] = useState("");

	const alerts = useMemo(() => ([
		{ id: 1, severity: "warning", message: "M6 delays between J33-J36: 15 mins" },
		{ id: 2, severity: "info", message: "Bus route 2 diversion via King Street" },
	]), []);

	const liveDepartures = useMemo(() => ([
		{ id: 1, type: "bus", route: "2", destination: "Blackpool", time: "2 mins", status: "On time" },
		{ id: 2, type: "train", route: "Northern", destination: "Manchester", time: "5 mins", status: "Delayed 3 mins" },
		{ id: 3, type: "bus", route: "100", destination: "Morecambe", time: "8 mins", status: "On time" },
	]), []);

	const routes = useMemo(() => ([
		{
			id: 1,
			duration: "45 mins",
			steps: [
				{ type: "walk", duration: "5 mins", to: "Lancaster Station" },
				{ type: "train", route: "Northern", duration: "30 mins", from: "Lancaster", to: "Preston" },
				{ type: "walk", duration: "10 mins", to: "Destination" }
			],
			price: "£5.20"
		}
	]), []);

	return (
		<Stack spacing={3}>
			<Paper elevation={1} sx={{ p: 3 }}>
				<Stack direction="row" spacing={1.5} alignItems="center">
					<Bus size={22} />
					<Typography variant="h5" fontWeight={700}>
						Dashboard
					</Typography>
					<Chip label="Live" color="success" size="small" sx={{ fontWeight: 700 }} />
				</Stack>
			</Paper>

			<Paper elevation={1} sx={{ p: 3 }}>
				<Stack direction="row" spacing={1} alignItems="center" mb={2}>
					<AlertCircle size={18} />
					<Typography variant="subtitle1" fontWeight={700}>Service alerts</Typography>
				</Stack>
				<Stack spacing={1.5}>
					{alerts.map(alert => (
						<Alert key={alert.id} severity={alert.severity === "warning" ? "warning" : "info"} variant="outlined">
							{alert.message}
						</Alert>
					))}
				</Stack>
			</Paper>

			<Grid container spacing={3}>
				<Grid item xs={12} md={6}>
					<Paper elevation={1} sx={{ p: 3, height: "100%" }}>
						<Stack spacing={2}>
							<Typography variant="h6" fontWeight={700}>Quick journey search</Typography>
							<TextField
								fullWidth
								label="From"
								value={fromLocation}
								onChange={(e) => setFromLocation(e.target.value)}
								InputProps={{ startAdornment: <InputAdornment position="start"><MapPin size={18} /></InputAdornment> }}
							/>
							<TextField
								fullWidth
								label="To"
								value={toLocation}
								onChange={(e) => setToLocation(e.target.value)}
								InputProps={{ startAdornment: <InputAdornment position="start"><NavIcon size={18} /></InputAdornment> }}
							/>
							<Button variant="contained" size="large" sx={{ alignSelf: "stretch" }}>
								Search routes
							</Button>
						</Stack>
					</Paper>
				</Grid>
				<Grid item xs={12} md={6}>
					<Paper elevation={1} sx={{ p: 3, height: "100%" }}>
						<Stack spacing={2}>
							<Stack direction="row" spacing={1} alignItems="center">
								<Clock size={18} />
								<Typography variant="h6" fontWeight={700}>Nearby departures</Typography>
							</Stack>
							<Stack spacing={1.5}>
								{liveDepartures.map(dep => (
									<Paper key={dep.id} variant="outlined" sx={{ p: 1.5 }}>
										<Stack direction="row" alignItems="center" justifyContent="space-between" spacing={2}>
											<Stack direction="row" spacing={1.5} alignItems="center">
												{dep.type === "bus" ? <Bus size={18} color="#1976d2" /> : <Train size={18} color="#2e7d32" />}
												<Box>
													<Typography fontWeight={700}>{dep.route}</Typography>
													<Typography variant="body2" color="text.secondary">{dep.destination}</Typography>
												</Box>
											</Stack>
											<Stack alignItems="flex-end">
												<Typography color="primary" fontWeight={700}>{dep.time}</Typography>
												<Typography variant="caption" color={dep.status.includes("Delayed") ? "error" : "success"}>{dep.status}</Typography>
											</Stack>
										</Stack>
									</Paper>
								))}
							</Stack>
						</Stack>
					</Paper>
				</Grid>
			</Grid>

			<Paper elevation={1} sx={{ p: 3 }}>
				<Stack spacing={2}>
					<Typography variant="h6" fontWeight={700}>Suggested routes</Typography>
					<Stack spacing={2}>
						{routes.map(route => (
							<Paper key={route.id} variant="outlined" sx={{ p: 2 }}>
								<Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1} mb={1}>
									<Typography fontWeight={700} color="primary">{route.duration}</Typography>
									<Typography fontWeight={700} color="success.main" textAlign={{ xs: "left", sm: "right" }}>{route.price}</Typography>
								</Stack>
								<Divider sx={{ mb: 1.5 }} />
								<Stack spacing={1.25}>
									{route.steps.map((step, idx) => (
										<Stack key={idx} direction="row" spacing={1.5} alignItems="flex-start">
											{step.type === "walk" && <MapPin size={18} color="#6b7280" />}
											{step.type === "bus" && <Bus size={18} color="#1976d2" />}
											{step.type === "train" && <Train size={18} color="#2e7d32" />}
											<Box>
												<Typography fontWeight={700} textTransform="capitalize">{step.type}</Typography>
												<Typography variant="body2" color="text.secondary">
													{step.route ? `${step.route} - ` : ""}
													{step.duration}
													{step.from ? ` from ${step.from}` : ""}
													{step.to ? ` to ${step.to}` : ""}
												</Typography>
											</Box>
										</Stack>
									))}
								</Stack>
							</Paper>
						))}
					</Stack>
				</Stack>
			</Paper>
		</Stack>
	);
}