"""Shared test fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def start_dns_shutdown_thread():
    """Start the pycares helper thread before the per-test thread snapshot.

    aiohttp resolves through aiodns when it is installed, and pycares keeps a
    daemon thread for channel teardown.  Starting it once up front keeps the
    Home Assistant test harness from reporting it as a leaked thread.
    """
    try:
        import pycares
    except ImportError:
        return
    pycares._shutdown_manager.start()


@pytest.fixture
def custom_integration(hass, enable_custom_integrations):
    """Make the custom integration loadable in tests that need Home Assistant."""
    return


def load_bytes(name: str) -> bytes:
    """Read a fixture file as bytes."""
    return (FIXTURES / name).read_bytes()


def load_text(name: str) -> str:
    """Read a fixture file as text."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_json(name: str):
    """Read a fixture file as JSON."""
    return json.loads(load_text(name))
