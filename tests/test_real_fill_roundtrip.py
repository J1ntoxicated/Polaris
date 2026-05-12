"""Day 6 — real fill round-trip helpers (dry-run path; live path is opt-in).

The live path requires OKX_DEMO_* / CAP_* creds; CI uses ``dry_run=True``
which writes synthetic Fill rows so we still exercise persist_fill.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from polaris.core.data.fills_persist import read_recent_fills
from polaris.scripts._smoke_real_roundtrip import (
    MIN_CAPITAL_LOT,
    MIN_OKX_NOTIONAL_USD,
    run_capital_round_trip,
    run_okx_round_trip,
)
from polaris.storage.schema import init_db


@pytest.mark.asyncio
async def test_okx_round_trip_dry_run_persists_two_fills(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        result = await run_okx_round_trip(conn=conn, dry_run=True)
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["open_fill_id"] != result["close_fill_id"]
        rows = read_recent_fills(conn, limit=10)
        assert len(rows) == 2
        venues = {r["venue"] for r in rows}
        assert venues == {"okx"}
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_capital_round_trip_dry_run_persists_two_fills(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        result = await run_capital_round_trip(conn=conn, dry_run=True)
        assert result["ok"] is True
        assert result["dry_run"] is True
        rows = read_recent_fills(conn, limit=10)
        assert len(rows) == 2
        assert {r["venue"] for r in rows} == {"capital"}
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_round_trip_dry_run_pnl_is_signed(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        result = await run_okx_round_trip(conn=conn, dry_run=True)
        assert result["pnl_usd"] != 0.0  # Synthetic round-trip with 0.2 px diff
    finally:
        conn.close()


def test_min_unit_constants_are_safe() -> None:
    assert MIN_OKX_NOTIONAL_USD >= 5.0  # OKX SPOT minSz × px floor
    assert MIN_CAPITAL_LOT >= 1.0


@pytest.mark.asyncio
async def test_okx_round_trip_missing_creds_returns_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Live path with missing creds → ok=False (no exception)."""
    monkeypatch.delenv("OKX_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("OKX_DEMO_SECRET", raising=False)
    monkeypatch.delenv("OKX_DEMO_PASSPHRASE", raising=False)
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        result = await run_okx_round_trip(conn=conn, dry_run=False)
        assert result["ok"] is False
        assert "missing" in result.get("error", "").lower()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_capital_round_trip_missing_creds_returns_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CAP_API_KEY", raising=False)
    monkeypatch.delenv("CAP_EMAIL", raising=False)
    monkeypatch.delenv("CAP_PASSWORD", raising=False)
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        result = await run_capital_round_trip(conn=conn, dry_run=False)
        assert result["ok"] is False
    finally:
        conn.close()
