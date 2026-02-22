FROM python:3.11-slim
WORKDIR /app

# Install runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY ./transport-backend /app
RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "transport-backend.api:app", "--host", "0.0.0.0", "--port", "8000"]
