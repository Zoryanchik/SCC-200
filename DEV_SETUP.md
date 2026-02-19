# Development: run locally with Docker Compose

This project can be run locally using Docker Compose to bring up the backend (FastAPI), the frontend (Vite), and an optional OSRM service for walking routing.

Prerequisites
- Docker & Docker Compose (v2+)
- (optional) git, node, Python if you prefer local installs

Quick start (Docker)

1. Copy the example env file and edit if necessary:

```powershell
copy .env.example .env
```

2. Build and start services:

```powershell
docker compose up --build
```

3. Open the frontend at http://localhost:5173 and the backend at http://localhost:8000. Health check: http://localhost:8000/health

Notes
- Frontend reads `VITE_API_BASE_URL` at build/dev time. The compose setup points it at `http://backend:8000` inside the compose network; the `.env` example points to `http://localhost:8000` for direct local usage.
- The `osrm` service in `docker-compose.yml` is a placeholder; OSRM requires preprocessed `.osrm` files. If you need walking/OSRM functionality, run OSRM with a prepared dataset or remove the service.
- If you prefer not to use Docker, follow the instructions in `transport-backend/README.md` and the frontend `package.json` scripts:

Backend (local):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install fastapi uvicorn pydantic
python -m uvicorn transport-backend.api:app --reload --host 0.0.0.0 --port 8000
```

Frontend (local):

```powershell
cd transport-frontend
npm install --legacy-peer-deps
echo VITE_API_BASE_URL=http://localhost:8000 > .env
npm run dev
```

Testing
- Frontend unit tests: `cd transport-frontend && npm run test`
- Backend tests: use `pytest` in the `transport-backend` folder.
