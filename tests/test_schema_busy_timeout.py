"""busy_timeout env override read-back — writer-migration-completion design
(``vault/50_research/writer-migration-completion_2026-07-09.md``).

DEMO/PAPER only. temp DB fixtures only — never touches a live DB.

``schema.py`` itself is UNCHANGED by that design (the env knob already
existed) — these tests pin the structural-arbiter claim: one env var,
``POLARIS_DB_BUSY_TIMEOUT_MS``, feeds the SQLite-native ``busy_handler`` wait
for BOTH ``connect()`` and ``connect_ro()``, uniformly covering every victim
conn (loop, focus, db_writer) that goes through these two functions.
"""

from __future__ import annotations

import pytest

from polaris.storage.schema import connect, connect_ro


def test_connect_busy_timeout_default(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLARIS_DB_BUSY_TIMEOUT_MS", raising=False)
    db = tmp_path / "default.sqlite"  # type: ignore[operator]
    conn = connect(db)  # type: ignore[arg-type]
    value = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert value == 5000


def test_connect_busy_timeout_env_override(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_DB_BUSY_TIMEOUT_MS", "15000")
    db = tmp_path / "override.sqlite"  # type: ignore[operator]
    conn = connect(db)  # type: ignore[arg-type]
    value = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert value == 15000


def test_connect_ro_busy_timeout_env_override(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "ro.sqlite"  # type: ignore[operator]
    connect(db).close()  # type: ignore[arg-type] — create the file first (mode=ro can't).
    monkeypatch.setenv("POLARIS_DB_BUSY_TIMEOUT_MS", "9000")
    ro = connect_ro(db)  # type: ignore[arg-type]
    value = ro.execute("PRAGMA busy_timeout").fetchone()[0]
    ro.close()
    assert value == 9000


def test_connect_busy_timeout_invalid_env_falls_back_to_default(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_DB_BUSY_TIMEOUT_MS", "not-an-int")
    db = tmp_path / "invalid.sqlite"  # type: ignore[operator]
    conn = connect(db)  # type: ignore[arg-type]
    value = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert value == 5000
