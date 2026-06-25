"""Pytest fixtures: in-memory SQLite with full Polaris schema, ts helpers."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from polaris.storage.schema import ALL_DDL


@pytest.fixture(autouse=True)
def polaris_test_vault(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Isolate EVERY test from the real repo vault (recurring pollution fix).

    ``ignite_p1`` bootstrap appends to ``vault/log.md`` and mutates
    ``vault/_NOW.md`` via cwd-relative paths — test runs from the repo root
    repeatedly polluted the real vault (250+ lines). This autouse fixture
    UNCONDITIONALLY points ``POLARIS_VAULT_DIR`` at a seeded per-test tmp
    vault (an ambient shell value must not silently disable isolation). A
    test that needs its own vault overrides the env AFTER depending on this
    fixture (see ``isolated_vault`` in test_day7) — never by unsetting it.
    Lives OUTSIDE the test's own ``tmp_path`` (via ``tmp_path_factory``) so
    tests asserting on their tmp tree never see the seeded vault files.
    """
    vault = tmp_path_factory.mktemp("polaris_test_vault")
    (vault / "log.md").write_text("# test vault log\n", encoding="utf-8")
    (vault / "_NOW.md").write_text(
        "# tmp NOW\n\n## Implementation status\n- placeholder\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("POLARIS_VAULT_DIR", str(vault))
    return vault


@pytest.fixture(autouse=True)
def _reset_log_dedup_caches() -> Iterator[None]:
    """Clear the logging dedup caches before each test.

    ``_production_bars._RECENCY_STALE_FLAGGED`` and
    ``_production_asset_class._FALLBACK_WARNED`` are module-level sets that
    suppress repeat WARNINGs (logging-only). They persist across tests in the
    same process, so a test asserting "the WARN fired" could see a false
    negative if an earlier test already warned for the same key. Clearing them
    per-test keeps the dedup behaviour deterministic without affecting any
    decision path (these sets gate log emission only).
    """
    from polaris.scripts import _production_asset_class, _production_bars

    _production_bars._RECENCY_STALE_FLAGGED.clear()
    _production_asset_class._FALLBACK_WARNED.clear()
    yield


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
