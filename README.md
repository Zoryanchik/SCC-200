# SCC-200: Regional Transport System

An advanced integrated transport application for the Preston, Lancaster, Blackpool, and Wyre coast region, providing unified multi-operator route planning and live arrival/departure boards.

## Overview

This project addresses fragmented information across multiple transport operators (Archway Travel, Stagecoach, Blackpool Transport, etc.) by building a scalable, resilient multi-platform application that integrates:

- **Live Bus Data:** REST API feeds with route information and GPS tracking
- **Rail Data:** Network Rail TRUST and TD messages via STOMP protocol
- **Static Infrastructure:** NaPTAN/NPTG database and BPLAN rail planning data
- **Advanced Features:** Multi-leg route planning, historical delay prediction, and real-time tracking

## Tech Stack

- **Backend:** Python (FastAPI/Flask) or Node.js
- **Frontend:** React/Vue with Leaflet.js for OpenStreetMap visualisations
- **Database:** PostgreSQL/MySQL
- **Container Engine:** Podman
- **Real-time Protocol:** STOMP (`transport.scc.lancs.ac.uk:61613`)

## Key Data Sources

- **Bus Feeds:** REST API (XML/JSON) - `/bus/times/` and `/bus/live/`
- **Rail (TRUST):** JSON batch messages (Activation, Cancellation, Movement)
- **Rail (TD):** Train Describer movement data between signalled track sections
- **Static Data:** NaPTAN/NPTG and BPLAN databases

## Important Notes

- Location codes require translation between TIPLOC, STANOX, and CRS formats
- CI/CD pipeline mandatory
- All AI-generated code must be clearly identified
- Security audits against NCSC Software Security Code of Practice required
- User testing requires SCC Ethics Committee approval
- The backend expects an OSRM routed server at `http://localhost:5012` by default
	(override with the `OSRM_URL` environment variable). For local testing the
repository includes a Lancashire extract and helper scripts that default to
that PBF. The canonical local data directory is `transport-backend/osrm/data`.

	A helper script is available at `transport-backend/osrm/run_osrm.sh` which
	downloads a PBF, prepares the OSRM files, and starts `osrm-routed`.

	Notes on runtimes and ports:

	- Helper scripts prefer Podman when available and fall back to Docker if Podman is not usable. On macOS Podman typically runs in a small VM; initialise it with `podman machine init` and `podman machine start`.
	- Default OSRM HTTP API port used by the helpers: `5012` (you can override with `OSRM_URL`).
	- Backend service (FastAPI / uvicorn) listens on port `5050` by default when started by the helper; the helper maps the container port to host `5050`.

	Example: build data and run OSRM (detached) and then start the backend helper:

	```sh
	# prepare and run OSRM (detached)
	./transport-backend/osrm/run_osrm.sh -d

	# start backend (attaches to same user network so backend can reach osrm by name)
	./transport-backend/run_backend.sh
	```

For detailed project specifications, see [PROJECT_BRAIN.md](PROJECT_BRAIN.md).
