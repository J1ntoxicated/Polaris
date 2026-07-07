"""VIRTUAL ACCOUNT mode switch — DEMO/PAPER only.

Jin 2026-07-07 mandate: a self-contained virtual paper account so the whole
pipeline validates with ZERO dependency on the real demo-venue state. These
tests cover the single override seam (``resolve_real_roundtrip``) plus the
default-off byte-identical guarantee.
"""

from __future__ import annotations

import pytest

from polaris.scripts._virtual_account import (
    resolve_real_roundtrip,
    virtual_account_enabled,
)


def test_virtual_account_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_VIRTUAL_ACCOUNT", raising=False)
    assert virtual_account_enabled() is False


def test_virtual_account_enabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", "1")
    assert virtual_account_enabled() is True


@pytest.mark.parametrize("other", ["0", "false", "yes", "", "2"])
def test_virtual_account_only_literal_1_enables(
    monkeypatch: pytest.MonkeyPatch, other: str,
) -> None:
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", other)
    assert virtual_account_enabled() is False


def test_resolve_real_roundtrip_unset_env_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLARIS_VIRTUAL_ACCOUNT", raising=False)
    assert resolve_real_roundtrip(True) is True
    assert resolve_real_roundtrip(False) is False


def test_resolve_real_roundtrip_virtual_forces_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", "1")
    assert resolve_real_roundtrip(True) is False
    assert resolve_real_roundtrip(False) is False
