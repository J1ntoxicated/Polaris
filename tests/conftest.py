"""Global test configuration — pytest conftest (Phase 27.4).

Protects test isolation against .env overrides that affect module-level constants.

Problem: realtime_runner.py calls load_dotenv() at import time, which sets
POLARIS_COLD_START_MAX_USD=0 in os.environ. When dynamic_sizing.py is first
imported after this, COLD_START_MAX_USD is set to 0.0 instead of 300.0.
This causes cold-start cap tests to fail when run together with realtime_runner tests.

Solution:
  1. Remove POLARIS_COLD_START_MAX_USD from os.environ before tests run (session-scoped).
  2. autouse function fixture patches the module attribute for each test.
Tests that explicitly need COLD_START_MAX_USD=0 (production mode) mock it directly.
"""
from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    """Remove production env override before any test module is imported."""
    os.environ.pop("POLARIS_COLD_START_MAX_USD", None)


@pytest.fixture(autouse=True)
def _guard_cold_start_max_usd(monkeypatch):
    """Ensure COLD_START_MAX_USD=300.0 for all tests.

    Production .env sets POLARIS_COLD_START_MAX_USD=0 (no cap). Tests must
    see the default 300.0 unless they explicitly override it. monkeypatch
    ensures the value is restored after each test.
    """
    import src.risk.dynamic_sizing as _ds
    monkeypatch.setattr(_ds, "COLD_START_MAX_USD", 300.0)
