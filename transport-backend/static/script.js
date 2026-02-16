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
        const response = await fetch('/api/bus_live', {
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
            if (data.buses && data.buses.length > 0) {
                let resultText = `Found ${data.buses.length} live bus(es):\n\n`;
                data.buses.forEach((bus, index) => {
                    resultText += `${index + 1}. Line: ${bus.line_ref}\n`;
                    resultText += `   Destination: ${bus.destination}\n`;
                    resultText += `   Operator: ${bus.operator}\n`;
                    resultText += `   Location: ${bus.latitude.toFixed(6)}, ${bus.longitude.toFixed(6)}\n\n`;
                });
                resultDiv.textContent = resultText;
            } else {
                resultDiv.textContent = 'No live buses found in the specified area.';
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
            const response = await fetch('/api/route', {
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