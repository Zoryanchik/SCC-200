# Minimal image for running the FastAPI backend. Works with Podman or Docker.
FROM python:3.11-slim

WORKDIR /app

# Ensure common utilities are available (curl is used at runtime)
RUN apt-get update \
	&& apt-get install -y --no-install-recommends curl \
	&& rm -rf /var/lib/apt/lists/*

# Copy and install requirements first for better layer caching
COPY requirements.txt /app/requirements.txt
# Upgrade pip inside the image to avoid runtime "new release available" notices
RUN python3 -m pip install --upgrade pip
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy project files
COPY . /app

EXPOSE 5050

# Run uvicorn to serve the FastAPI app on port 5050
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "5050"]
