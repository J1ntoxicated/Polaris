"""Paper loop smoke — fast (5s) integration tests.

These don't hit any real venue. The smoke script exposes ``run_smoke`` which
we invoke with ``duration_sec=0`` so the loop body executes once and returns,
then we assert the in-memory state has the expected shape.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from polaris.scripts._smoke_fills import simulate_close, simulate_open_fill
from polaris.scripts.smoke_paper_loop import (
    FOCUS,
    FocusEntry,
    LoopState,
    StubGPTClient,
    _build_market_view,
    _stub_bars,
)
from polaris.storage.schema import init_db
from polaris.strategies.base import RawSignal


def test_focus_universe_is_4_entries() -> None:
    assert len(FOCUS) == 4
    venues = {e.venue for e in FOCUS}
    assert venues == {"okx", "capital"}


def test_market_view_builds_with_boosted_last_bar() -> None:
    bars = _stub_bars(50, base=60_000.0)
    entry = FocusEntry("okx", "BTC-USDT", "1m", "crypto")
    mv = _build_market_view(entry=entry, bars=bars)
    assert mv.symbol == "BTC-USDT"
    assert mv.last_price > bars[-1].close  # boosted
    assert mv.is_session_open_window is True


def test_simulated_open_fill_okx_quote_ccy() -> None:
    sig = RawSignal(
        signal_id="sig1",
        strategy_id="volume_burst",
        symbol="BTC-USDT",
        side="long",
        strength=0.8,
        sizing_hint=0.05,
        ttl_bars=10,
        thesis_tag="vb_burst",
        correlation_group="spot_intraday_event",
    )
    fill, trade = simulate_open_fill(
        signal=sig, venue="okx", last_price=60_000.0, notional_usd=50.0
    )
    assert fill.venue == "okx"
    assert fill.size_usd == pytest.approx(50.0)
    assert fill.side == "buy"
    assert trade.entry_price == 60_000.0
    assert trade.notional_usd == pytest.approx(50.0)


def test_simulated_open_fill_capital() -> None:
    sig = RawSignal(
        signal_id="sig2",
        strategy_id="fx_breakout_basket",
        symbol="EURUSD",
        side="long",
        strength=0.7,
        sizing_hint=0.03,
        ttl_bars=10,
        thesis_tag="fx_break",
        correlation_group="cfd_fx_trend",
    )
    fill, trade = simulate_open_fill(
        signal=sig, venue="capital", last_price=1.0850, notional_usd=10.0
    )
    assert fill.venue == "capital"
    assert fill.side == "buy"
    assert trade.symbol == "EURUSD"


def test_simulated_open_fill_alpaca_routes_through_normalize_alpaca_fill() -> None:
    # ROOT CAUSE this seals: alpaca venue used to fall into the `else` branch
    # (capital handling) — recorded as capital:{epic}@$10 placeholder instead
    # of the real equity qty/price. This pins the 3-way branch so alpaca gets
    # its own normalize_alpaca_fill path with the correct qty = notional/price.
    sig = RawSignal(
        signal_id="sig3",
        strategy_id="equity_52wk_high_breakout",
        symbol="AAPL",
        side="long",
        strength=0.8,
        sizing_hint=0.05,
        ttl_bars=10,
        thesis_tag="eq_52wk",
        correlation_group="equity_52wk_high_breakout",
    )
    fill, trade = simulate_open_fill(
        signal=sig, venue="alpaca", last_price=200.0, notional_usd=1_000.0
    )
    assert fill.venue == "alpaca"
    assert fill.side == "buy"
    assert fill.size_usd == pytest.approx(1_000.0)
    assert fill.fill_price == pytest.approx(200.0)
    assert fill.base_qty == pytest.approx(5.0)  # notional / price = 1000 / 200
    assert fill.instrument_id == "alpaca:AAPL"
    assert trade.venue == "alpaca"
    assert trade.symbol == "AAPL"
    assert trade.entry_price == 200.0
    assert trade.notional_usd == pytest.approx(1_000.0)


def test_simulated_close_alpaca_routes_through_normalize_alpaca_fill() -> None:
    sig = RawSignal(
        signal_id="sig4",
        strategy_id="equity_vol_expansion_pocket_pivot",
        symbol="MSFT",
        side="long",
        strength=0.7,
        sizing_hint=0.05,
        ttl_bars=10,
        thesis_tag="eq_pivot",
        correlation_group="equity_vol_expansion_pocket_pivot",
    )
    _, trade = simulate_open_fill(
        signal=sig, venue="alpaca", last_price=100.0, notional_usd=500.0
    )
    close_fill = simulate_close(trade, exit_price=110.0)
    assert close_fill.venue == "alpaca"
    assert close_fill.side == "sell"  # opposite of original long
    assert close_fill.fill_price == pytest.approx(110.0)
    # simulate_close re-derives qty from the ORIGINAL open notional / exit price
    # (mirrors the existing OKX close-fill formula — trade.notional_usd is the
    # entry-side dollar notional, not a carried share count).
    assert close_fill.base_qty == pytest.approx(500.0 / 110.0)
    assert close_fill.size_usd == pytest.approx(500.0)
    assert close_fill.instrument_id == "alpaca:MSFT"


def test_simulated_close_round_trip() -> None:
    sig = RawSignal(
        signal_id="s",
        strategy_id="volume_burst",
        symbol="BTC-USDT",
        side="long",
        strength=0.8,
        sizing_hint=0.05,
        ttl_bars=10,
        thesis_tag="t",
        correlation_group="spot_intraday_event",
    )
    _, trade = simulate_open_fill(
        signal=sig, venue="okx", last_price=60_000.0, notional_usd=50.0
    )
    close_fill = simulate_close(trade, exit_price=60_300.0)
    assert close_fill.side == "sell"  # opposite of original long
    assert close_fill.fill_price == 60_300.0


@pytest.mark.asyncio
async def test_5s_tick_cycle_smoke(tmp_path: Path) -> None:
    """One tick cycle — verify state flow without hitting any external venue."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        state = LoopState(
            fills_open=[], fills_close=[], open_trades=[], closed_trades=[]
        )
        bars_cache: dict[str, list] = {}
        # Pre-load stubs so no real OKX request is made.
        for entry in FOCUS:
            bars_cache[f"{entry.venue}:{entry.symbol}"] = _stub_bars(120)
        # Patch out real OKX fetch for speed.
        import polaris.scripts.smoke_paper_loop as mod

        async def _fake_fetch(entry: FocusEntry, *, limit: int = 200):
            return bars_cache[f"{entry.venue}:{entry.symbol}"]

        original = mod._fetch_bars_for_symbol
        mod._fetch_bars_for_symbol = _fake_fetch  # type: ignore[assignment]
        try:
            await mod._run_one_tick(
                state=state,
                conn=conn,
                haiku=StubGPTClient(),
                bars_cache=bars_cache,
                tick_idx=1,
            )
        finally:
            mod._fetch_bars_for_symbol = original  # type: ignore[assignment]
        assert state.signals_emitted >= 1
        assert state.pipeline_runs >= 1
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_strategies_isolation_no_cross_pollution(tmp_path: Path) -> None:
    """A failing strategy must not crash its peers — verify by injecting a
    bad market view that only one strategy can handle."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        from polaris.scripts.smoke_paper_loop import (
            _capital_strategies,
            _okx_strategies,
            _run_strategy_for_focus,
        )

        # 80 bars ≥ the daily-breakout warmup (bar_breakout_run = 51): the OKX
        # smoke bundle is now the two REGISTERED + dispatch_eligible bar strategies
        # (bar_breakout_run / okx_donchian_55_breakout) — rsi_bb_pullback (fee-fatal
        # KILL) + the un-registered spot_donchian were dropped from the rig.
        bars = _stub_bars(80, base=60_000.0)
        entry = FocusEntry("okx", "BTC-USDT", "1m", "crypto")
        # Run all OKX strategies → expect at least one to emit something while
        # no exception aborts the gather.
        results = await asyncio.gather(
            *(_run_strategy_for_focus(s, entry, bars) for s in _okx_strategies())
        )
        # Two dispatch_eligible OKX bar strategies in the smoke bundle.
        assert len(results) == 2
        # At least one OKX strat emits with the boosted view.
        assert any(r is not None for r in results)
        # Capital strategies on an OKX entry → all None (asset class skip).
        cap_results = await asyncio.gather(
            *(_run_strategy_for_focus(s, entry, bars) for s in _capital_strategies())
        )
        assert all(r is None for r in cap_results)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_universe_to_focus_to_signal_to_fill(tmp_path: Path) -> None:
    """End-to-end: focus → strategies → at least one fill produced."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        state = LoopState(
            fills_open=[], fills_close=[], open_trades=[], closed_trades=[]
        )
        bars_cache: dict[str, list] = {}
        for entry in FOCUS:
            bars_cache[f"{entry.venue}:{entry.symbol}"] = _stub_bars(120)

        import polaris.scripts.smoke_paper_loop as mod

        async def _fake_fetch(entry: FocusEntry, *, limit: int = 200):
            return bars_cache[f"{entry.venue}:{entry.symbol}"]

        original = mod._fetch_bars_for_symbol
        mod._fetch_bars_for_symbol = _fake_fetch  # type: ignore[assignment]
        try:
            # Two ticks so the close path runs at least once.
            for i in range(2):
                await mod._run_one_tick(
                    state=state,
                    conn=conn,
                    haiku=StubGPTClient(),
                    bars_cache=bars_cache,
                    tick_idx=i + 1,
                )
        finally:
            mod._fetch_bars_for_symbol = original  # type: ignore[assignment]
        assert len(state.fills_open) >= 1
        assert len(state.closed_trades) >= 1
        assert len(state.fills_close) >= 1
    finally:
        conn.close()
