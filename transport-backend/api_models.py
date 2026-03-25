"""Pydantic request/response models used by the API layer."""

from typing import Optional

from pydantic import BaseModel


class RouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    date: str
    time: str  # HH:MM:SS
    max_transfers: int = 3
    mode: str = "both"  # "bus", "train", or "both"


class RouteResponse(BaseModel):
    success: bool
    route: Optional[dict] = None
    error: Optional[str] = None


class AddressRouteRequest(BaseModel):
    start: str
    end: str
    date: str
    time: str  # HH:MM:SS
    max_transfers: int = 3
    mode: str = "both"


class StopLocation(BaseModel):
    lat: float
    lon: float


class JourneyPlanRequest(BaseModel):
    fromStop: StopLocation
    toStop: StopLocation
    departureTime: str     # HH:MM:SS
    date: str              # YYYY-MM-DD
    maxTransfers: int = 5
    mode: str = "both"     # bus | train | both
