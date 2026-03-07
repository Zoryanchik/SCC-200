// Determine API base URL. When the page is opened via file:// the
// browser will treat leading '/' paths as file URLs (which fail when
// the backend is running on http). Detect that case and fall back to
// the local backend address used in development.
const API_BASE = (location.protocol === 'file:') ? 'http://127.0.0.1:5050' : '';

// Ensure date/time inputs for the address route form have sensible defaults so
// browser validation doesn't prevent the JS handler from running when the user
// clicks "Find Route by Address".
try {
    const now = new Date();
    const today = now.toISOString().slice(0, 10); // YYYY-MM-DD
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    const ss = String(now.getSeconds()).padStart(2, '0');
    const timeHMS = `${hh}:${mm}:${ss}`;

    const addrDate = document.getElementById('route_addr_date');
    if (addrDate && !addrDate.value) addrDate.value = today;
    const addrTime = document.getElementById('route_addr_time');
    if (addrTime && !addrTime.value) addrTime.value = timeHMS;

    // Also set defaults for the numeric route form if empty
    const mainDate = document.getElementById('route_date');
    if (mainDate && !mainDate.value) mainDate.value = today;
    const mainTime = document.getElementById('route_time');
    if (mainTime && !mainTime.value) mainTime.value = `${hh}:${mm}`; // input[type=time] may accept HH:MM
} catch (err) {
    // non-fatal; if DOM elements are missing just continue
}

document.getElementById('busLiveForm').addEventListener('submit', async function(e) {
    e.preventDefault();

    const submitButton = this.querySelector('button[type="submit"]');
    const loadingDiv = document.getElementById('bus-loading');
    const resultDiv = document.getElementById('bus-result');

    // Show loading
    submitButton.disabled = true;
    loadingDiv.style.display = 'block';
    resultDiv.style.display = 'none';

    // Get form data
    const formData = {
        lat: parseFloat(document.getElementById('bus_lat').value),
        lon: parseFloat(document.getElementById('bus_lon').value),
        lat_tol: parseFloat(document.getElementById('lat_tol').value),
        lon_tol: parseFloat(document.getElementById('lon_tol').value)
    };

    try {
        const params = new URLSearchParams({
            lat: formData.lat,
            lon: formData.lon,
            latTol: formData.lat_tol,
            lonTol: formData.lon_tol
        });
        const response = await fetch(`${API_BASE}/bus/live/all?${params}`);
        const data = await response.json();

        resultDiv.style.display = 'block';

        if (Array.isArray(data)) {
            resultDiv.className = 'result success';
            if (data.length > 0) {
                let resultText = `Found ${data.length} live bus(es):\n\n`;
                data.forEach((bus, index) => {
                    resultText += `${index + 1}. Line: ${bus.line}\n`;
                    resultText += `   Destination: ${bus.destination}\n`;
                    resultText += `   Operator: ${bus.operator}\n`;
                    resultText += `   Location: ${bus.lat.toFixed(6)}, ${bus.lon.toFixed(6)}\n`;
                    // Show delay/status when provided by the API
                    if (bus.delay_minutes !== undefined && bus.delay_minutes !== null) {
                        resultText += `   Delay: ${bus.delay_minutes} min (${bus.status})\n\n`;
                    } else if (bus.status) {
                        resultText += `   Status: ${bus.status}\n\n`;
                    } else {
                        resultText += `\n`;
                    }
                });
                resultDiv.textContent = resultText;
            } else {
                resultDiv.textContent = 'No live buses found in the specified area.';
            }
        } else if (data.error) {
            resultDiv.className = 'result error';
            resultDiv.textContent = `Error: ${data.error}`;
        }

    } catch (error) {
        resultDiv.style.display = 'block';
        resultDiv.className = 'result error';
        resultDiv.textContent = `Network error: ${error.message}`;
    } finally {
        submitButton.disabled = false;
        loadingDiv.style.display = 'none';
    }
});

// Routing form logic
const routeForm = document.getElementById('routeForm');
if (routeForm) {
    routeForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        const submitButton = this.querySelector('button[type="submit"]');
        const loadingDiv = document.getElementById('route-loading');
        const resultDiv = document.getElementById('route-result');

        submitButton.disabled = true;
        loadingDiv.style.display = 'block';
        resultDiv.style.display = 'none';

        // Get form data
        const formData = {
            start_lat: parseFloat(document.getElementById('start_lat').value),
            start_lon: parseFloat(document.getElementById('start_lon').value),
            end_lat: parseFloat(document.getElementById('end_lat').value),
            end_lon: parseFloat(document.getElementById('end_lon').value),
            date: document.getElementById('route_date').value,
            time: document.getElementById('route_time').value + ':00', // ensure HH:MM:SS
            max_transfers: parseInt(document.getElementById('max_transfers').value),
            mode: document.getElementById('mode').value
        };

        try {
            const response = await fetch(`${API_BASE}/api/route`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(formData)
            });
            const data = await response.json();
            resultDiv.style.display = 'block';
            if (data.success) {
                resultDiv.className = 'result success';
                if (data.route_text) {
                    resultDiv.textContent = data.route_text;
                } else {
                    resultDiv.textContent = formatRouteResult(data.route);
                }
            } else {
                resultDiv.className = 'result error';
                resultDiv.textContent = `Error: ${data.error}`;
            }
        } catch (error) {
            resultDiv.style.display = 'block';
            resultDiv.className = 'result error';
            resultDiv.textContent = `Network error: ${error.message}`;
        } finally {
            submitButton.disabled = false;
            loadingDiv.style.display = 'none';
        }
    });
}

function formatRouteResult(route) {
    if (!route) return 'No route found.';
    let out = '';
    if (route._meta) {
        out += `Start walk: ${route._meta.start_walk_seconds || 0} sec\n`;
        out += `End walk: ${route._meta.end_walk_seconds || 0} sec\n`;
        out += `Total arrival: ${route._meta.total_arrival || ''} sec\n`;
        out += `Start: ${route._meta.start_point || ''}\n`;
        out += `Destination: ${route._meta.destination || ''}\n\n`;
    }
    let legs = Object.entries(route)
        .filter(([k, v]) => k !== '_meta')
        .map(([stop, info], idx) => {
            let s = `Leg ${idx + 1}:\n`;
            s += `  Stop: ${stop}\n`;
            if (info.stop_name) s += `  Name: ${info.stop_name}\n`;
            if (info.prev_stop_name) s += `  From: ${info.prev_stop_name}\n`;
            if (info.type) s += `  Type: ${info.type}\n`;
            if (info.journey_info) s += `  Journey: ${JSON.stringify(info.journey_info)}\n`;
            if (info.journey_origin) s += `  Origin: ${info.journey_origin}\n`;
            if (info.journey_destination) s += `  Destination: ${info.journey_destination}\n`;
            if (info.board_departure) s += `  Board Departure: ${info.board_departure}\n`;
            if (info.arrival_time) s += `  Arrival: ${info.arrival_time}\n`;
            return s;
        });
    out += legs.join('\n');
    return out;
}

// Route by address handler
const routeAddrForm = document.getElementById('routeAddressForm');
if (routeAddrForm) {
    routeAddrForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        const submitButton = this.querySelector('button[type="submit"]');
        const loadingDiv = document.getElementById('route-addr-loading');
        const resultDiv = document.getElementById('route-addr-result');

        submitButton.disabled = true;
        loadingDiv.style.display = 'block';
        resultDiv.style.display = 'none';

        const formData = {
            start: document.getElementById('start_addr').value,
            end: document.getElementById('end_addr').value,
            date: document.getElementById('route_addr_date').value,
            time: document.getElementById('route_addr_time').value + ':00',
            max_transfers: parseInt(document.getElementById('max_transfers_addr').value),
            mode: document.getElementById('mode_addr').value
        };

        try {
            // First fetch geocode candidates for both start and end
            const sq = encodeURIComponent(formData.start);
            const eq = encodeURIComponent(formData.end);
            // Request only Lancashire candidates to avoid results from other countries
            // Match the real frontend: call /search/stops (no county or
            // limit) so the same fuzzy-correction and filtering logic is
            // used — the backend default limit (10) matches what the real
            // frontend receives.
            const sResp = await fetch(`${API_BASE}/search/stops?q=${sq}`);
            const eResp = await fetch(`${API_BASE}/search/stops?q=${eq}`);
            if (!sResp.ok || !eResp.ok) {
                throw new Error('Geocoding failed for one or both addresses');
            }
            const sJson = await sResp.json();
            const eJson = await eResp.json();

            // /search/stops returns a plain JSON array of candidates
            const startCandidates = Array.isArray(sJson) ? sJson : (sJson.candidates || []);
            const endCandidates = Array.isArray(eJson) ? eJson : (eJson.candidates || []);

            if (startCandidates.length === 0 && endCandidates.length === 0) {
                throw new Error('No location candidates found for the given addresses');
            }

            // Populate selects
            const startSel = document.getElementById('start_candidates');
            const endSel = document.getElementById('end_candidates');
            startSel.innerHTML = '';
            endSel.innerHTML = '';

            startCandidates.forEach((c, i) => {
                const opt = document.createElement('option');
                opt.value = `${c.lat},${c.lon}`;
                opt.textContent = `${c.name} (${c.lat.toFixed(5)}, ${c.lon.toFixed(5)})`;
                startSel.appendChild(opt);
            });
            endCandidates.forEach((c, i) => {
                const opt = document.createElement('option');
                opt.value = `${c.lat},${c.lon}`;
                opt.textContent = `${c.name} (${c.lat.toFixed(5)}, ${c.lon.toFixed(5)})`;
                endSel.appendChild(opt);
            });

            // Show candidate area and keep original form displayed so user can change
            document.getElementById('route-addr-candidates').style.display = 'block';
            resultDiv.style.display = 'none';
        } catch (err) {
            resultDiv.style.display = 'block';
            resultDiv.className = 'result error';
            resultDiv.textContent = `Network error: ${err.message}`;
        } finally {
            submitButton.disabled = false;
            loadingDiv.style.display = 'none';
        }
    });
}

// Compute route from selected candidates
const computeBtn = document.getElementById('compute_route_from_candidates');
if (computeBtn) {
    computeBtn.addEventListener('click', async function() {
        const startSel = document.getElementById('start_candidates');
        const endSel = document.getElementById('end_candidates');
        const resultDiv = document.getElementById('route-addr-result');
        const loadingDiv = document.getElementById('route-addr-loading');

        if (!startSel.value || !endSel.value) {
            resultDiv.style.display = 'block';
            resultDiv.className = 'result error';
            resultDiv.textContent = 'Please pick both a start and end candidate.';
            return;
        }

        const [s_lat, s_lon] = startSel.value.split(',').map(Number);
        const [e_lat, e_lon] = endSel.value.split(',').map(Number);

        // Read other params from the form
        const date = document.getElementById('route_addr_date').value;
        const time = document.getElementById('route_addr_time').value + ':00';
        const max_transfers = parseInt(document.getElementById('max_transfers_addr').value);
        const mode = document.getElementById('mode_addr').value;

        loadingDiv.style.display = 'block';
        resultDiv.style.display = 'none';

        try {
            const payload = {
                start_lat: s_lat,
                start_lon: s_lon,
                end_lat: e_lat,
                end_lon: e_lon,
                date: date,
                time: time,
                max_transfers: max_transfers,
                mode: mode
            };
            const resp = await fetch(`${API_BASE}/api/route`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await resp.json();
            resultDiv.style.display = 'block';
            if (data.success) {
                resultDiv.className = 'result success';
                if (data.route_text) {
                    resultDiv.textContent = data.route_text;
                } else {
                    resultDiv.textContent = formatRouteResult(data.route);
                }
            } else {
                resultDiv.className = 'result error';
                resultDiv.textContent = `Error: ${data.error}`;
            }
        } catch (err) {
            resultDiv.style.display = 'block';
            resultDiv.className = 'result error';
            resultDiv.textContent = `Network error: ${err.message}`;
        } finally {
            loadingDiv.style.display = 'none';
        }
    });
}