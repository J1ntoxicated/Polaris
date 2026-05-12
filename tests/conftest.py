"""Pytest fixtures: in-memory SQLite with full Polaris schema, ts helpers."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator

import pytest

from polaris.storage.schema import ALL_DDL


@pytest.fixture
def memdb() -> Iterator[sqlite3.Connection]:
    """Fresh in-memory SQLite with all DDL applied."""
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys=ON;")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def now_ts() -> int:
    return int(time.time())
