"""Pytest configuration for transport-backend tests.

Adds the transport-backend directory to sys.path so that modules
like api, bus_live, main, etc. can be imported directly.
"""
import sys
import os

# Add the transport-backend directory to the Python path
backend_dir = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(backend_dir))
