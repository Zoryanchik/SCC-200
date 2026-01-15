# Detailed Project Brain: SCC-200 Regional Transport System

## 1. Core Mission & Regional Scope

**The Client:** Local Authority for the Preston, Lancaster, Blackpool, and Wyre coast region.

**Problem:** Fragmented information across multiple operators (Archway Travel, Stagecoach, Blackpool Transport, etc.) makes travel complex.

**Goal:** Build an "advanced" integrated application that handles multi-operator route planning and live arrival/departure boards.

**Advanced Features Required:** Scalability, resilience, multi-platform support, multi-leg route planning, and use of historical data for delay prediction.

---

## 2. The Tech Stack (Mandated & Recommended)

- **Environment:** Podman is the required container engine for University lab machines.
- **Backend:** Python (FastAPI/Flask) or Node.js to handle high-frequency data streams.
- **Frontend:** React/Vue with Leaflet.js or Folium for OpenStreetMap-based map visualisations.
- **Database:** PostgreSQL/MySQL for NaPTAN/BPLAN storage.
- **Live Connection:** Must use STOMP protocol to connect to `transport.scc.lancs.ac.uk:61613`.

---

## 3. Data Sources & Protocol Specifics

### Bus Feeds
- REST API providing XML/JSON
- Contains route info (`/bus/times/`) and live GPS tracking (`/bus/live/`)

### Rail Feeds (Network Rail)

**TRUST Messages:** JSON-based batch messages
- Focus on type 0001 (Activation), 0002 (Cancellation), and 0003 (Movement)

**TD Messages (Train Describer):** Detailed movement between "berths" (signalled track sections)

**STOMP Topics:**
- `/topic/TRAIN_MVT_ALL_TOC` (TRUST)
- `/topic/TD_ALL_SIG_AREA` (TD)

### Static Infrastructure
- **NaPTAN/NPTG:** UK-wide transport access point database (large XML files)
- **BPLAN:** Rail planning data including location (LOC) and timing link (TLK) records

---

## 4. Specific "Real-World" Complexities

- **Location Inconsistency:** Rail sites use schematics; geographic lat/long is often more accurate in the NaPTAN database than rail-specific datasets.
- **Location Codes:** Be ready to translate between:
  - TIPLOC (Timing Point)
  - STANOX (Station Number)
  - CRS (Station Alpha codes like LAN or PRE)
- **Rail Directions:** "UP" generally means toward London; "DOWN" is away.
- **Message Granularity:** TRUST reports have a limited granularity of +/- one minute.

---

## 5. Development Norms

- **CI/CD:** Mandatory automated integration and delivery process.
- **AI Disclosure:** All code or libraries developed by Agentic AI must be clearly identified to avoid plagiarism.
- **Security:** Developers are personally responsible for auditing AI-generated code against the NCSC Software Security Code of Practice.
- **Ethics:** No user testing without prior SCC Ethics Committee approval and signed consent forms.

---

*Last Updated: January 15, 2026*
