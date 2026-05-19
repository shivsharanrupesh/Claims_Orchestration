"""
tests/conftest.py
──────────────────
Shared pytest fixtures available to all tests.

These fixtures mock out the Azure services so tests can run
without any cloud infrastructure.
"""

from __future__ import annotations
import os
import pytest

# ── Set mock environment variables before any imports ─────────────────────────
# This prevents pydantic-settings from failing when Azure vars are absent.
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://mock.openai.azure.com/")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "mock-key-for-testing")
os.environ.setdefault("COSMOS_ENDPOINT", "https://mock.cosmos.azure.com/")
os.environ.setdefault("COSMOS_KEY", "mock-cosmos-key==")
os.environ.setdefault(
    "AZURE_STORAGE_CONNECTION_STRING",
    "DefaultEndpointsProtocol=https;AccountName=mockaccount;"
    "AccountKey=bW9jaw==;EndpointSuffix=core.windows.net"
)
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6380")
os.environ.setdefault("REDIS_PASSWORD", "mock-redis-password")
os.environ.setdefault("REDIS_SSL", "false")
os.environ.setdefault("CHROMA_HOST", "http://localhost")
os.environ.setdefault("CHROMA_PORT", "8000")
os.environ.setdefault("MLFLOW_TRACKING_URI", "./data/test-mlruns")
