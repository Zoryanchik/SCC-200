# Minimal image for running the FastAPI backend. Works with Podman or Docker.
FROM python:3.11-slim

WORKDIR /app

# Copy and install requirements first for better layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy project files
COPY . /app

EXPOSE 5050

# Run uvicorn to serve the FastAPI app on port 5050
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "5050"]
