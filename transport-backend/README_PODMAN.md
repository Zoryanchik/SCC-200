# Build & run (Podman)

This file explains how to build and run the transport backend image with Podman (or Docker).

Build the image:

```bash
cd transport-backend
podman build -t transport-backend:local .
```

Run (foreground):

```bash
# run with the renamed container name
podman run --rm -p 5050:5050 --name transport-backend-edillocnon transport-backend:local
# visit http://localhost:5050/health
```

Run (detached) with persistent cache directory:

```bash
podman run -d --name transport-backend-edillocnon -p 5050:5050 -v "$(pwd)/cache":/app/cache transport-backend:local
```

Mac users: start the podman VM once if using Podman Machine:

```bash
podman machine init
podman machine start
```

If Podman is not available, the same commands work with `docker` (replace `podman` with `docker`).
