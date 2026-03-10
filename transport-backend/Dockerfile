# Minimal image for running the FastAPI backend. Works with Podman or Docker.
FROM python:3.11-slim

WORKDIR /app

# Ensure common utilities are available (curl is used at runtime).
# Also install native build/development libraries needed to build lxml
# when a prebuilt wheel isn't available on the platform.
RUN apt-get update \
	 && apt-get install -y --no-install-recommends \
		 curl \
		 build-essential \
		 python3-dev \
		 libxml2-dev \
		 libxslt1-dev \
		 zlib1g-dev \
		 pkg-config \
	 && rm -rf /var/lib/apt/lists/*

# Copy and install requirements first for better layer caching
COPY requirements.txt /app/requirements.txt
# Upgrade pip inside the image to avoid runtime "new release available" notices
RUN python3 -m pip install --upgrade pip
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy project files

# Copy project files early so we can build Cython extensions in the image
COPY . /app

EXPOSE 5050

# Run uvicorn to serve the FastAPI app on port 5050
## Build Cython extensions at image build time so the runtime container
# doesn't need to compile on every startup. This requires the build deps
# installed above. If setup scripts are present they will be executed; if
# not, the commands will harmlessly fail.
RUN if [ -f setup_raptor.py ]; then python3 setup_raptor.py build_ext --inplace || true; fi
RUN if [ -f setup_walking.py ]; then python3 setup_walking.py build_ext --inplace || true; fi

# Optionally remove build tools to shrink the final image. Keep them if
# you prefer to allow runtime compilation.
RUN apt-get purge -y --auto-remove build-essential python3-dev || true && rm -rf /var/lib/apt/lists/*

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "5050"]
