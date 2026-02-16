"""Runner for the Transport Backend API.

Usage:
    python -m transport-backend
    # or
    cd transport-backend && python __main__.py
"""
import uvicorn

from api import app  # noqa: F401

if __name__ == "__main__":
    uvicorn.run("api:app", host="localhost", port=8000, reload=True)
