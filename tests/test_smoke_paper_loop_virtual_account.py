"""VIRTUAL ACCOUNT — smoke_paper_loop.run_smoke's independent real-order
probe flags (real_okx / real_capital / real_roundtrip) must all be forced
off under POLARIS_VIRTUAL_ACCOUNT=1 (Jin 2026-07-07). DEMO/PAPER only.

``run_smoke`` is a standalone Day-5/6 legacy smoke CLI, separate from the
SSOT ignite_p1 -> run_production_paper_loop path, but it is an independently
launchable real-order-capable entry point (real_okx_probe/real_capital_probe
place real demo orders directly, gated by their OWN flags — not threaded
through real_roundtrip). Covered here for completeness per the mandate:
"grep every real-order call site and gate it."
"""

from __future__ import annotations

from pathlib import Path

import pytest

import polaris.scripts.smoke_paper_loop as mod


@pytest.mark.asyncio
async def test_run_smoke_virtual_mode_skips_real_okx_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", "1")

    async def _fail_probe(*_a: object, **_k: object) -> dict[str, object]:
        raise AssertionError("real_okx_probe touched under virtual mode")

    monkeypatch.setattr(mod, "real_okx_probe", _fail_probe)
    monkeypatch.setattr(mod, "real_capital_probe", _fail_probe)

    db_path = tmp_path / "polaris.sqlite"
    rc = await mod.run_smoke(
        duration_sec=0.0, tick_sec=0.0, real_okx=True, real_capital=True,
        db_path=db_path,
    )
    assert rc in (0, 1)


@pytest.mark.asyncio
async def test_run_smoke_virtual_mode_forces_real_roundtrip_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", "1")

    async def _fail_round_trip(*_a: object, **_k: object) -> dict[str, object]:
        raise AssertionError("round-trip touched under virtual mode")

    monkeypatch.setattr(mod, "run_okx_round_trip", _fail_round_trip)
    monkeypatch.setattr(mod, "run_capital_round_trip", _fail_round_trip)

    db_path = tmp_path / "polaris.sqlite"
    rc = await mod.run_smoke(
        duration_sec=0.0, tick_sec=0.0, real_roundtrip=True, db_path=db_path,
    )
    assert rc in (0, 1)
