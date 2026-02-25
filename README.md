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
 - The backend expects an OSRM routed server at http://localhost:5321 by default (override with the OSRM_URL environment variable). When running OSRM in a container map host port 5321 to container port 5000 (e.g. `-p 5321:5000`).

For detailed project specifications, see [PROJECT_BRAIN.md](PROJECT_BRAIN.md).
