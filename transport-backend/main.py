"""
Transport Backend - FastAPI Entry Point
Exposes a proxy API for the frontend to consume.
"""

import os
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from api_utils import search_stops as search_stops_db
from planner import plan_journey as plan_journey_local
from ws_manager import WebSocketManager
from ws_relay import WebSocketRelay


API_BASE_URL = os.getenv("SCC_API_BASE_URL", "https://transport.scc.lancs.ac.uk")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")


def create_app() -> FastAPI:
    app = FastAPI(title="SCC Transport Backend")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[FRONTEND_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    manager = WebSocketManager()
    relay = WebSocketRelay(manager)

    async def proxy_get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{API_BASE_URL}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, params=params)
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        return response.json()

    async def proxy_post(path: str, payload: Dict[str, Any]) -> Any:
        url = f"{API_BASE_URL}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload)
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        return response.json()

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/alerts")
    async def get_alerts() -> Any:
        return await proxy_get("/alerts")

    @app.get("/bus/live/{operator_code}")
    async def get_bus_live(operator_code: str) -> Any:
        return await proxy_get(f"/bus/live/{operator_code}")

    @app.get("/bus/times/{stop_code}")
    async def get_bus_times(stop_code: str) -> Any:
        return await proxy_get(f"/bus/times/{stop_code}")

    @app.get("/bus/arrivals/{stop_code}")
    async def get_bus_arrivals(stop_code: str) -> Any:
        return await proxy_get(f"/bus/arrivals/{stop_code}")

    @app.get("/rail/departures/{station_code}")
    async def get_rail_departures(station_code: str) -> Any:
        return await proxy_get(f"/rail/departures/{station_code}")

    @app.post("/journey/plan")
    async def plan_journey(payload: Dict[str, Any]) -> Any:
        from_stop = payload.get("fromStop")
        to_stop = payload.get("toStop")
        departure_time = payload.get("departureTime")
        max_transfers = payload.get("maxTransfers", 3)
        if not from_stop or not to_stop or not departure_time:
            raise HTTPException(status_code=400, detail="Missing fromStop/toStop/departureTime")
        return plan_journey_local(from_stop, to_stop, departure_time, max_transfers)

    @app.get("/search/stops")
    async def search_stops(q: str = Query("", min_length=1)) -> Any:
        return search_stops_db(q)

    @app.get("/pricing")
    async def get_pricing(from_stop: str, to_stop: str) -> Any:
        return await proxy_get("/pricing", params={"from": from_stop, "to": to_stop})

    @app.get("/weather")
    async def get_weather(lat: float = 54.05, lon: float = -2.80) -> Any:
        return await proxy_get("/weather", params={"lat": lat, "lon": lon})

    @app.websocket("/ws/live")
    async def ws_live(websocket: WebSocket) -> None:
        await manager.connect(websocket)
        relay.start()
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(websocket)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
