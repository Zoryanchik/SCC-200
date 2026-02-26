"""Runner for the Transport Backend API.

Usage:
    python -m transport-backend
    # or
    cd transport-backend && python __main__.py
"""
import uvicorn

from api import app  # noqa: F401

if __name__ == "__main__":
    import os as _os
    _port = int(_os.environ.get("BACKEND_PORT", _os.environ.get("PORT", "5050")))
    uvicorn.run("api:app", host="0.0.0.0", port=_port, reload=True)
