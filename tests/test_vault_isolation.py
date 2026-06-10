"""Test-suite vault isolation — the recurring real-vault pollution fix.

DEMO/PAPER. ``ignite_p1`` bootstrap stamps appended "[ignite_p1: bootstrap …]"
lines into the REAL ``vault/log.md`` and mutated ``vault/_NOW.md`` whenever a
test exercised ``ignite()`` from the repo-root cwd (3 re-pollutions in one
session, 250+ cumulative lines). The fix routes both writers through
``POLARIS_VAULT_DIR`` (unset = cwd-relative ``vault`` — runtime
byte-identical) and the conftest autouse ``polaris_test_vault`` fixture pins
the env to a per-test tmp vault UNCONDITIONALLY.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from polaris.scripts.ignite_p1 import (
    _now_md_update_implementation_status,
    _vault_append,
    _vault_root,
)

REPO_VAULT = Path(__file__).resolve().parent.parent / "vault"


def test_autouse_fixture_pins_vault_env(polaris_test_vault: Path) -> None:
    # The autouse fixture must be the active vault root for every test.
    assert _vault_root() == polaris_test_vault


def test_vault_writers_hit_only_the_isolated_vault(
    polaris_test_vault: Path,
) -> None:
    """META test: run the exact two polluters; assert the REAL repo vault is
    byte-identical and the isolated tmp vault received the writes."""
    repo_log = REPO_VAULT / "log.md"
    repo_now = REPO_VAULT / "_NOW.md"
    before = tuple(
        p.read_bytes() if p.exists() else b"" for p in (repo_log, repo_now)
    )

    appended = _vault_append("[ignite_p1: bootstrap ISOLATION-PROBE]")
    updated = _now_md_update_implementation_status("ISOLATION-PROBE status")

    after = tuple(
        p.read_bytes() if p.exists() else b"" for p in (repo_log, repo_now)
    )
    assert after == before  # repo vault untouched — the regression channel
    assert appended is not None
    assert Path(appended) == polaris_test_vault / "log.md"
    assert "ISOLATION-PROBE" in (polaris_test_vault / "log.md").read_text()
    assert updated is True
    assert "ISOLATION-PROBE status" in (
        polaris_test_vault / "_NOW.md"
    ).read_text()


def test_env_unset_keeps_runtime_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset env = the cwd-relative ``vault`` — runtime byte-identical.
    Path-resolution assert only; nothing is written."""
    monkeypatch.delenv("POLARIS_VAULT_DIR", raising=False)
    assert _vault_root() == Path("vault")


def test_env_empty_string_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_VAULT_DIR", "")
    assert _vault_root() == Path("vault")


def test_vault_append_missing_log_returns_none(
    polaris_test_vault: Path,
) -> None:
    (polaris_test_vault / "log.md").unlink()
    assert _vault_append("x") is None
