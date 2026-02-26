# Minimal image for running the FastAPI backend. Works with Podman or Docker.
FROM python:3.11-slim

WORKDIR /app

# Copy and install requirements first for better layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy project files
COPY . /app

EXPOSE 5050

# Default cache dir inside the container. The start_container.sh helper also
# exports CACHE_DIR=/app/cache and mounts the host cache to this path. Setting
# the ENV here makes the behavior consistent when running the image directly.
ENV CACHE_DIR=/app/cache

# Ensure the directory exists in the image so mounts and permission fixes work
# consistently across runtimes.
RUN mkdir -p /app/cache

# Default backend port (can be overridden at runtime by BACKEND_PORT)
ENV BACKEND_PORT=5050

# Run uvicorn to serve the FastAPI app on port 5050
CMD ["sh", "-lc", "exec uvicorn api:app --host 0.0.0.0 --port ${BACKEND_PORT:-5050}"]
