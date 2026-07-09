"""Day 6 codex review regression tests (P0 + P1 fixes)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from polaris.core.data.fills_persist import read_recent_fills
from polaris.core.data.ingest import update_baseline_from_bars
from polaris.core.data.schema import Bar
from polaris.storage.schema import init_db

# ---------------------------------------------------------------------------
# P0-1 — ignite paper-mode honours db_path (codex)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ignite_paper_persists_to_caller_db_path(tmp_path: Path) -> None:
    """``ignite(db_path=tmp, paper=True)`` must trade against ``tmp``, not the
    default ``data/polaris.sqlite``. Codex P0-1 regression."""
    from unittest.mock import AsyncMock

    custom_db = tmp_path / "ignite_custom.sqlite"
    init_db(custom_db).close()

    import time as _time

    import polaris.scripts._production_bars as prod_bars
    import polaris.scripts._production_layers as prod_layers
    import polaris.scripts.smoke_paper_loop as mod
    from polaris.core.universe.schema import UniverseInstrument
    from polaris.scripts._smoke_gpt_stub import StubGPTClient
    from polaris.scripts.ignite_p1 import ignite
    from polaris.scripts.smoke_paper_loop import FocusEntry, _stub_bars

    async def _fake_fetch(entry: FocusEntry, *, limit: int = 200):
        return _stub_bars(60)

    original = mod._fetch_bars_for_symbol
    mod._fetch_bars_for_symbol = _fake_fetch  # type: ignore[assignment]

    # Day 8: production loop owns the bar fetch — patch it too. Seed an OKX
    # universe row so the focus path has data and the bars fetcher returns
    # canned bars.
    sample_universe = [
        UniverseInstrument(
            venue="okx", symbol="BTC-USDT", instrument_id="okx:BTC-USDT",
            underlying_group_id="crypto:BTC", asset_class="crypto",
            quote_ccy="USDT", state="live", vol_24h_usd=2e9, spread_bps=2.0,
            atr_24h_pct=4.0, depth_10bps_usd=2e6, signal_density_7d=0.0,
            listing_ts=None, last_seen_ts=int(_time.time()),
        )
    ]
    from polaris.core.data.schema import Bar as CanonicalBar

    # Bars must trigger okx_donchian_55_breakout (OKX SPOT, 1D timeframe) so the
    # full pipeline persists a fill. (Vehicle was spot_donchian, the 1H Donchian-40
    # breakout, un-registered 2026-06-27 in the #56 stop-bleeders KILL — the
    # surviving registered+dispatched OKX Donchian breakout is the 1D donchian-55.)
    # Trigger: ``close > donchian_high_55`` (prior-55-bar high, excl. last) AND
    # ``ROC_20 > 0``. A steady 1D uptrend across 80 bars (> the 76-bar warmup)
    # yields a positive ROC, and the final bar's +2500 breakout pushes close above
    # the prior-55-bar high. ``bar_interval`` is "1D" so the per-timeframe bar fetch
    # persists/reads them in the donchian-55 1D bucket. Volume varies bar-to-bar
    # and is always > 0 so no bar is dropped as a synthetic/flat forward-fill.
    # Anchor at NOW so the newest bar is fresh — the recency guard (Jin
    # 2026-06-22 dead-feed gate) skips a symbol whose newest bar is stale, so a
    # fixed-2023-epoch fixture would read as a dead feed → 0 opens → 0 fills.
    _bars_base_ts = int(_time.time()) - 86400 * 80
    fake_bars: list[CanonicalBar] = []
    for i in range(80):
        is_breakout = i == 79
        base = 60_000.0 + i * 60.0
        close = base + (2_500.0 if is_breakout else 0.0)
        high = close + 80.0
        low = base - 80.0
        volume = 1_000.0 + (i % 5) * 50.0
        fake_bars.append(
            CanonicalBar(
                instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
                venue="okx", symbol="BTC-USDT", bar_interval="1D",
                ts=_bars_base_ts + i * 86400,
                open=base, high=high, low=low,
                close=close, volume=volume,
                notional_usd=close * volume,
            )
        )
    original_okx_inst = prod_layers.fetch_okx_instruments
    original_cap_inst = prod_layers.fetch_capital_instruments
    original_okx_bars = prod_bars.fetch_okx_bars
    # Yahoo is now the PRIMARY bar-history source; stub it empty so this test's
    # injected OKX exchange bars are reached (the exchange path is the fallback).
    original_yahoo_bars = prod_bars.fetch_yahoo_bars
    prod_layers.fetch_okx_instruments = AsyncMock(return_value=sample_universe)
    prod_layers.fetch_capital_instruments = AsyncMock(return_value=[])
    prod_bars.fetch_okx_bars = AsyncMock(return_value=list(reversed(fake_bars)))
    prod_bars.fetch_yahoo_bars = AsyncMock(return_value=[])
    try:
        await ignite(
            duration_sec=0.5,
            tick_sec=0.1,
            db_path=custom_db,
            paper=True,
            full_pipeline=True,
            haiku=StubGPTClient(),  # avoid real OpenAI call in tests
        )
    finally:
        mod._fetch_bars_for_symbol = original  # type: ignore[assignment]
        prod_layers.fetch_okx_instruments = original_okx_inst
        prod_layers.fetch_capital_instruments = original_cap_inst
        prod_bars.fetch_okx_bars = original_okx_bars
        prod_bars.fetch_yahoo_bars = original_yahoo_bars
    # The custom DB should have at least one fills row (full pipeline path).
    conn = sqlite3.connect(f"file:{custom_db}?mode=ro", uri=True)
    try:
        n_custom = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    finally:
        conn.close()
    assert n_custom >= 1, "ignite(paper=True) failed to persist to caller's db_path"


# ---------------------------------------------------------------------------
# P0-3 — round-trip helpers fail closed when close leg fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_okx_round_trip_returns_ok_false_when_creds_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No creds → ok=False with no fill rows persisted."""
    from polaris.scripts._smoke_real_roundtrip import run_okx_round_trip

    for env_var in ("OKX_DEMO_API_KEY", "OKX_DEMO_SECRET", "OKX_DEMO_PASSPHRASE"):
        monkeypatch.delenv(env_var, raising=False)
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        result = await run_okx_round_trip(conn=conn, dry_run=False)
        assert result["ok"] is False
        n = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
        assert n == 0, "no fills should persist when round-trip fails"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# P1-4 — baseline recompute uses batch max_ts
# ---------------------------------------------------------------------------


def test_baseline_recompute_uses_batch_max_ts(tmp_path: Path) -> None:
    """The baseline window query is anchored at max(b.ts) per instrument so
    out-of-order bars do not anchor recompute against an early ts.
    Codex P1-4 regression: drive 6 bars in *reverse* order and verify that
    ticker_baseline_state.updated_ts == max(b.ts)."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        bars: list[Bar] = []
        for i in range(6):
            bars.append(
                Bar(
                    instrument_id="okx:BTC-USDT",
                    underlying_group_id="BTC",
                    venue="okx",
                    symbol="BTC-USDT",
                    bar_interval="1m",
                    ts=1_700_000_000 + i * 60,
                    open=80_000.0 + i,
                    high=80_010.0 + i,
                    low=79_990.0 + i,
                    close=80_005.0 + i,
                    volume=10.0 + i,
                    notional_usd=80_000.0 * (10.0 + i),
                    trade_count=100,
                    vwap=80_000.0 + i,
                    bid_close=0.0,
                    ask_close=0.0,
                    spread_bps_close=0.0,
                    source="rest",
                )
            )

        def _generator() -> Iterator[Bar]:
            # Reverse-iterate to simulate out-of-order ingestion.
            yield from reversed(bars)

        update_baseline_from_bars(conn, _generator(), asset_class="crypto")
        max_ts = max(b.ts for b in bars)
        rows = conn.execute(
            "SELECT updated_ts FROM ticker_baseline_state WHERE instrument_id = ?",
            ("okx:BTC-USDT",),
        ).fetchall()
        assert rows
        for (ts,) in rows:
            assert int(ts) == max_ts, (
                f"updated_ts={ts} should equal batch max_ts={max_ts}"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Round-2 P1 — close-leg exceptions surface ``step`` + ``open_fill_id``
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_okx_close_leg_exception_returns_step_and_open_fill_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exception during the close leg must surface ``step`` + ``open_fill_id``
    so the caller can resume / dispatch the orphaned position. Codex round-2 P1."""
    import polaris.scripts._smoke_real_roundtrip as mod
    from polaris.scripts._smoke_real_roundtrip import run_okx_round_trip

    monkeypatch.setenv("OKX_DEMO_API_KEY", "fake")
    monkeypatch.setenv("OKX_DEMO_SECRET", "fake")
    monkeypatch.setenv("OKX_DEMO_PASSPHRASE", "fake")

    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        # Stub OKXAdapter so we control the open leg + raise on close.
        class _FakeAdapter:
            def __init__(self, *args: object, **kwargs: object) -> None: ...

            async def __aenter__(self) -> _FakeAdapter:
                return self

            async def __aexit__(self, *args: object) -> None: ...

            _calls = {"place_count": 0}

            async def place_market_order(self, *, side: str, **kwargs: object) -> object:
                self._calls["place_count"] += 1
                if self._calls["place_count"] >= 2:
                    raise RuntimeError("simulated close-leg socket reset")

                class _Resp:
                    ok = True
                    venue_order_id = "open_oid"
                    code = "0"
                    msg = "ok"

                return _Resp()

            async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict:
                return {
                    "data": [
                        {
                            "ordId": ord_id, "clOrdId": "cl",
                            "instId": inst_id, "side": "buy",
                            "tdMode": "cash", "tgtCcy": "quote_ccy",
                            "fillSz": "0.0001", "accFillSz": "0.0001",
                            "avgPx": "80000", "fee": "-0.05",
                            "feeCcy": "USDT", "state": "filled",
                            "uTime": "1700000000000",
                        }
                    ]
                }

            async def fetch_ticker(self, *args: object, **kwargs: object) -> dict:
                return {"last": "80000", "bidPx": "79999", "askPx": "80001"}

        monkeypatch.setattr(mod, "OKXAdapter", _FakeAdapter)
        result = await run_okx_round_trip(conn=conn, dry_run=False)
        assert result["ok"] is False
        # The exception was raised during the close leg.
        assert "step" in result
        assert "open_fill_id" in result, (
            "open_fill_id must be surfaced so caller can resume the orphan."
        )
        assert result["step"] in {"close_place", "close_query"}
        # The open Fill was persisted before the exception.
        n_open = conn.execute(
            "SELECT COUNT(*) FROM fills WHERE is_close = 0"
        ).fetchone()[0]
        assert n_open == 1
        # No close was persisted because the close leg raised.
        n_close = conn.execute(
            "SELECT COUNT(*) FROM fills WHERE is_close = 1"
        ).fetchone()[0]
        assert n_close == 0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_dry_run_roundtrip_produces_close_fill_id(tmp_path: Path) -> None:
    from polaris.scripts._smoke_real_roundtrip import run_okx_round_trip

    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        result = await run_okx_round_trip(conn=conn, dry_run=True)
        assert result["ok"] is True
        assert result["close_fill_id"]  # non-empty
        # Both legs persisted.
        rows = read_recent_fills(conn)
        assert len(rows) == 2
        assert any(r["is_close"] for r in rows)
        assert any(not r["is_close"] for r in rows)
    finally:
        conn.close()
