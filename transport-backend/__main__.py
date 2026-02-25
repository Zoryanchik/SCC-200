"""Runner for the Transport Backend API.

Usage:
    python -m transport-backend
    # or
    cd transport-backend && python __main__.py
"""
import os
import uvicorn

from api import app  # noqa: F401

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    # Default to 5050 so local/mock and container runs use the same port.
    port = int(os.environ.get("PORT", "5050"))
    uvicorn.run("api:app", host=host, port=port, reload=True)
