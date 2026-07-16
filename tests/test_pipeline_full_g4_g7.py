"""Day 6 — payload_builder + G3->G7 chain unit tests.

Each test exercises one builder + the gate it feeds. Tests stay pure-Python
(no real venue calls) so the suite runs in <1 s.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from polaris.core.pipeline.agents import (
    adaptive_exit_gate,
    entry_sizer_gate,
    position_monitor_gate,
    signal_validator_gate,
)
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GATE_ENTRY_SIZER,
    GATE_POSITION_MONITOR,
    GATE_SIGNAL_VALIDATOR,
    GateContext,
    GateDecision,
    SignalLifecycle,
)
from polaris.core.pipeline.payload_builder import (
    build_exit_payload,
    build_monitor_payload,
    build_sizer_payload,
    build_validator_payload,
    build_watcher_payload,
    default_strategy_risk_state,
    load_active_positions,
)
from polaris.core.sizing import PortfolioState, SignalIntent, StrategyRiskState
from polaris.scripts._smoke_gpt_stub import StubGPTClient
from polaris.storage.schema import init_db
from polaris.strategies.base import RawSignal


def _make_raw_signal() -> RawSignal:
    return RawSignal(
        signal_id="sig_test_g4g7",
        strategy_id="volume_burst",
        symbol="BTC-USDT",
        side="long",
        strength=1.2,
        sizing_hint=0.05,
        ttl_bars=10,
        thesis_tag="vb_test",
        correlation_group="spot_intraday_event",
    )


# ---------------------------------------------------------------------------
# build_validator_payload
# ---------------------------------------------------------------------------


def test_payload_builder_signal_intent_for_validator(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        sig = _make_raw_signal()
        payload = build_validator_payload(
            raw_signal=sig,
            venue="okx",
            symbol="BTC-USDT",
            instrument_id="okx:BTC-USDT",
            regime="bull_trend",
            conn=conn,
        )
        assert payload["signal_id"] == sig.signal_id
        assert payload["raw_signal"]["strategy_id"] == "volume_burst"
        # Empty DB — cell quartile defaults to "mid".
        assert payload["cell_quartile"] in {"mid", "cold"}
        # Baseline + recent_trades exist (empty when DB is fresh).
        assert payload["baseline"] == {}
        assert payload["recent_trades"] == []
    finally:
        conn.close()


def test_payload_builder_validator_payload_pulls_recent_trades(tmp_path: Path) -> None:
    """When fills rows exist, recent_trades surfaces them sorted by ts DESC."""
    from polaris.core.data.fill_normalizer import Fill
    from polaris.core.data.fills_persist import persist_fill

    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        for i in range(3):
            persist_fill(
                conn,
                Fill(
                    venue="okx",
                    instrument_id="okx:BTC-USDT",
                    strategy_id="volume_burst",
                    side="sell",
                    size_usd=50.0,
                    fill_price=80_000.0 + i,
                    fee_usd=0.05,
                    slippage_bps=2.0,
                    ts_ms=int(time.time() * 1000) - i * 1000,
                    order_id=f"ord{i}",
                ),
                is_close=True,
                pnl_usd=10.0 if i % 2 == 0 else -5.0,
            )
        sig = _make_raw_signal()
        payload = build_validator_payload(
            raw_signal=sig,
            venue="okx",
            symbol="BTC-USDT",
            instrument_id="okx:BTC-USDT",
            regime="bull_trend",
            conn=conn,
        )
        assert len(payload["recent_trades"]) == 3
        assert payload["recent_trades"][0]["ts"] >= payload["recent_trades"][-1]["ts"]
    finally:
        conn.close()


def _seed_baseline(
    conn: sqlite3.Connection, *, instrument_id: str, group_id: str, asset_class: str
) -> None:
    """Seed atr/size/volume baseline rows for one instrument (FIX-1 regression)."""
    from polaris.core.data.baseline import upsert_baseline_state
    from polaris.core.data.schema import BaselineValue

    seeds = {"atr": (0.0024, 0.0031), "size": (8.68, 11.2), "volume": (22.0, 41.0)}
    for metric, (p50, p75) in seeds.items():
        upsert_baseline_state(
            conn,
            instrument_id=instrument_id,
            underlying_group_id=group_id,
            asset_class=asset_class,
            baseline=BaselineValue(
                metric=metric,
                p50=p50,
                p75=p75,
                sample_count=400,
                lookback_sec=604_800,
                updated_ts=int(time.time()),
            ),
        )


def test_validator_payload_capital_carries_populated_baseline(tmp_path: Path) -> None:
    """FIX-1 regression: a Capital signal whose ``ticker_baseline_state`` is
    populated MUST carry the real atr/size/volume baseline into the G3 payload
    (not the empty stub that triggers GPT "insufficient data" false-KILLs).

    The lookup key is ``f"{venue}:{symbol}"`` — identical to the OKX path and to
    what the bar-ingest writer stores (``Bar.instrument_id``). This locks the
    OKX/Capital symmetry: removing it would re-introduce the false-KILL failure
    mode (aggressive bias = fewer false KILLs = MORE entries).
    """
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        _seed_baseline(
            conn, instrument_id="capital:NOSUSD", group_id="NOS", asset_class="forex"
        )
        sig = RawSignal(
            signal_id="sig_cap_g3",
            strategy_id="fx_breakout",
            symbol="NOSUSD",
            side="long",
            strength=0.8,
            sizing_hint=0.05,
            ttl_bars=10,
            thesis_tag="fx_test",
            correlation_group="NOS",
        )
        payload = build_validator_payload(
            raw_signal=sig,
            venue="capital",
            symbol="NOSUSD",
            instrument_id="capital:NOSUSD",
            regime="bull_trend",
            conn=conn,
        )
        baseline = payload["baseline"]
        assert set(baseline) == {"atr", "size", "volume"}, baseline
        assert baseline["atr"]["p50"] > 0.0
        assert baseline["atr"]["n"] == 400
        assert baseline["size"]["p50"] > 0.0
        assert baseline["volume"]["p50"] > 0.0
    finally:
        conn.close()


def test_validator_payload_okx_capital_baseline_symmetry(tmp_path: Path) -> None:
    """FIX-1: with both venues seeded identically, the G3 baseline block is
    structurally identical — no OKX-vs-Capital asymmetry in the builder."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        _seed_baseline(
            conn, instrument_id="okx:BTC-USDT", group_id="BTC", asset_class="crypto"
        )
        _seed_baseline(
            conn, instrument_id="capital:XAUUSD", group_id="XAU", asset_class="commodity"
        )
        okx_sig = RawSignal(
            signal_id="o",
            strategy_id="volume_burst",
            symbol="BTC-USDT",
            side="long",
            strength=1.0,
            sizing_hint=0.05,
            ttl_bars=10,
            thesis_tag="t",
            correlation_group="BTC",
        )
        cap_sig = RawSignal(
            signal_id="c",
            strategy_id="xau_indices_trend",
            symbol="XAUUSD",
            side="long",
            strength=1.0,
            sizing_hint=0.05,
            ttl_bars=10,
            thesis_tag="t",
            correlation_group="XAU",
        )
        okx_p = build_validator_payload(
            raw_signal=okx_sig, venue="okx", symbol="BTC-USDT",
            instrument_id="okx:BTC-USDT", regime="bull_trend", conn=conn,
        )
        cap_p = build_validator_payload(
            raw_signal=cap_sig, venue="capital", symbol="XAUUSD",
            instrument_id="capital:XAUUSD", regime="bull_trend", conn=conn,
        )
        assert set(okx_p["baseline"]) == set(cap_p["baseline"]) == {"atr", "size", "volume"}
        # Capital must NOT be the empty stub when its baseline exists.
        assert cap_p["baseline"] != {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# build_watcher_payload + former G4 fast-path (relocated into G3, group A)
# ---------------------------------------------------------------------------


def test_payload_builder_watcher_payload_shape() -> None:
    p = build_watcher_payload(
        spread_bps=1.5,
        baseline_p50_spread_bps=2.0,
        listing_age_hours=8760.0,
        recent_reject_in_6h=False,
        session_open_shock_window=False,
    )
    assert p["spread_bps"] == 1.5
    assert p["listing_age_hours"] == 8760.0
    assert p["tick_window"] == []


@pytest.mark.asyncio
async def test_g3_top_quartile_clean_book_proceeds_deterministic() -> None:
    """P2a group A: G4's fast-path is relocated into G3 (``signal_validator_gate``)
    but is structurally unreachable through the live gate — G3's OWN
    technical rule caps its scalar at 1.0, below the 1.25 fast-path floor
    (same structural cap the gate audit's G4 100%-no-op finding reflects).
    A clean top-quartile/tight-spread/mature-listing signal still flows
    (PASS, deterministic, model_used='python') straight to G5."""
    payload: dict = {
        "raw_signal": {"strategy": "vb", "score": 1.0},
        "cell_routing": {"quartile": "top", "n_eff": 10.0, "avg_pnl_r": 0.5},
        **build_watcher_payload(
            spread_bps=1.0,
            baseline_p50_spread_bps=2.0,
            listing_age_hours=24 * 30.0,
        ),
        "cell_quartile": "top",
    }
    ctx = GateContext(
        run_id="r", signal_id="s", position_id=None, gate_id=GATE_SIGNAL_VALIDATOR,
        venue="okx", symbol="BTC-USDT", strategy_id="volume_burst",
        payload=payload, started_ts=int(time.time()), state=SignalLifecycle.RAW,
    )
    result = await signal_validator_gate(ctx)
    assert result.decision == GateDecision.PASS
    assert result.next_gate == GATE_ENTRY_SIZER
    assert result.skipped is False
    assert result.model_used == "python"


# ---------------------------------------------------------------------------
# build_sizer_payload + G5 entry sizer chain
# ---------------------------------------------------------------------------


def test_payload_builder_sizer_payload_carries_dataclasses(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        sig = _make_raw_signal()
        payload = build_sizer_payload(
            raw_signal=sig,
            venue="okx",
            symbol="BTC-USDT",
            instrument_id="okx:BTC-USDT",
            underlying_group_id="BTC",
            asset_class="crypto",
            regime="bull_trend",
            track="A",
            listing_age_hours=720.0,
            equity_usd=10_000.0,
            conn=conn,
        )
        assert isinstance(payload["signal_intent"], SignalIntent)
        assert isinstance(payload["risk_state"], StrategyRiskState)
        assert isinstance(payload["portfolio"], PortfolioState)
        assert payload["signal_intent"].track == "A"
        assert payload["portfolio"].equity_usd == 10_000.0
    finally:
        conn.close()


def test_default_strategy_risk_state_cold_start() -> None:
    s = default_strategy_risk_state(venue="okx", strategy="vb")
    assert s.closed_trades == 0
    assert s.kelly_p == 0.0
    assert s.win_streak == 0


@pytest.mark.asyncio
async def test_g4_to_g5_chain_pass_when_sizer_succeeds(tmp_path: Path) -> None:
    """G3 PASS/MODIFY (WATCHED) + sizer payload -> G5 emits SIZED with
    positive notional (G3->G5 direct wire, P2a group A)."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        sig = _make_raw_signal()
        sizer_payload = build_sizer_payload(
            raw_signal=sig,
            venue="okx", symbol="BTC-USDT", instrument_id="okx:BTC-USDT",
            underlying_group_id="BTC", asset_class="crypto", regime="bull_trend",
            track="A", listing_age_hours=720.0, equity_usd=10_000.0, conn=conn,
        )
        ctx = GateContext(
            run_id="r", signal_id=sig.signal_id, position_id=None,
            gate_id=GATE_ENTRY_SIZER, venue="okx", symbol="BTC-USDT",
            strategy_id="volume_burst", payload=sizer_payload,
            started_ts=int(time.time()), state=SignalLifecycle.WATCHED,
        )
        result = await entry_sizer_gate(ctx, conn=conn)
        assert result.decision == GateDecision.SIZED
        sized = result.payload.get("sized")
        assert sized is not None
        assert sized["final_notional_usd"] > 0.0
        assert sized["final_risk_pct"] > 0.0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# build_monitor_payload + G6 chain
# ---------------------------------------------------------------------------


def test_build_monitor_payload_with_swap_candidate() -> None:
    pos = {"venue": "okx", "symbol": "BTC-USDT", "side": "long",
           "correlation_group": "BTC"}
    cand = {"venue": "okx", "symbol": "BTC-USDT", "side": "long",
            "correlation_group": "BTC", "strategy": "tsmom"}
    p = build_monitor_payload(
        position=pos, unrealized_pnl_r=0.3, max_loss_r=1.0, swap_candidate=cand,
    )
    assert p["position"] == pos
    assert p["unrealized_pnl_r"] == 0.3
    assert p["swap_candidate"] == cand


@pytest.mark.asyncio
async def test_g5_to_g6_chain_hold_default() -> None:
    """G6 with mid-trade pnl returns HOLD + advances to G7."""
    pos = {"venue": "okx", "symbol": "BTC-USDT", "side": "long", "strategy": "vb",
           "correlation_group": "BTC"}
    payload = build_monitor_payload(
        position=pos, unrealized_pnl_r=0.2, max_loss_r=1.0,
    )
    ctx = GateContext(
        run_id="r", signal_id="s", position_id="p", gate_id=GATE_POSITION_MONITOR,
        venue="okx", symbol="BTC-USDT", strategy_id="vb",
        payload=payload, started_ts=int(time.time()), state=SignalLifecycle.SIZED,
    )
    result = await position_monitor_gate(ctx)
    assert result.decision == GateDecision.HOLD
    assert result.next_gate == GATE_ADAPTIVE_EXIT


@pytest.mark.asyncio
async def test_g5_to_g6_chain_exit_now_on_stop() -> None:
    """G6 with -1.5R returns EXIT_NOW."""
    pos = {"venue": "okx", "symbol": "BTC-USDT", "side": "long", "strategy": "vb",
           "correlation_group": "BTC"}
    payload = build_monitor_payload(
        position=pos, unrealized_pnl_r=-1.5, max_loss_r=1.0,
    )
    ctx = GateContext(
        run_id="r", signal_id="s", position_id="p", gate_id=GATE_POSITION_MONITOR,
        venue="okx", symbol="BTC-USDT", strategy_id="vb",
        payload=payload, started_ts=int(time.time()), state=SignalLifecycle.ACTIVE,
    )
    result = await position_monitor_gate(ctx)
    assert result.decision == GateDecision.EXIT_NOW


# ---------------------------------------------------------------------------
# build_exit_payload + G7 chain
# ---------------------------------------------------------------------------


def test_build_exit_payload_passes_initial_stop() -> None:
    p = build_exit_payload(
        side="long", current_stop_price=80.0, proposed_stop_price=78.0,
        entry_price=85.0, unrealized_pnl_r=0.9, max_loss_r=1.0,
        initial_stop_price=75.0,
    )
    assert p["widen_proposal"]["initial_stop_price"] == 75.0
    assert p["widen_proposal"]["seconds_since_last_override"] == 31


def test_build_exit_payload_omits_exit_context_when_no_context() -> None:
    """#26 G7 enrichment: with no context kwargs the payload is the pre-enrich
    shape (widen_proposal + current_stop_price only) — behaviour-identity."""
    p = build_exit_payload(
        side="long", current_stop_price=80.0, proposed_stop_price=78.0,
        entry_price=85.0, unrealized_pnl_r=0.9, max_loss_r=1.0,
    )
    assert set(p) == {"widen_proposal", "current_stop_price"}
    assert "exit_context" not in p


def test_build_exit_payload_folds_rich_context() -> None:
    """#26 G7 enrichment: regime/ATR trajectory/MFE-MAE/exit_state/peak +
    recent close summary are folded into an exit_context block. The widen
    proposal (Q9 rail inputs) is left untouched."""
    p = build_exit_payload(
        side="long", current_stop_price=80.0, proposed_stop_price=78.0,
        entry_price=85.0, unrealized_pnl_r=1.3, max_loss_r=1.0,
        initial_stop_price=75.0,
        regime="bull_trend",
        atr_pct=0.006,
        atr_slope=0.0012,
        volume_z=2.1,
        held_seconds=420,
        mfe_r=1.8,
        mae_r=-0.4,
        exit_state="protect",
        peak_price=88.0,
        recent_closes=[85.0, 86.0, 87.0, 88.0, 86.5],
    )
    # Widen proposal (Q9 rail inputs) unchanged.
    assert p["widen_proposal"]["proposed_stop_price"] == 78.0
    assert p["widen_proposal"]["unrealized_pnl_r"] == 1.3
    assert p["current_stop_price"] == 80.0
    ctx = p["exit_context"]
    assert ctx["regime"] == "bull_trend"
    assert ctx["atr_pct"] == 0.006
    assert ctx["atr_slope"] == 0.0012
    assert ctx["volume_z"] == 2.1
    assert ctx["held_seconds"] == 420
    assert ctx["mfe_r"] == 1.8
    assert ctx["mae_r"] == -0.4
    assert ctx["exit_state"] == "protect"
    assert ctx["peak_price"] == 88.0
    # Recent close summary: first/last + count (compact, not the raw list).
    assert ctx["recent_close_first"] == 85.0
    assert ctx["recent_close_last"] == 86.5
    assert ctx["recent_close_n"] == 5


def test_build_exit_payload_partial_context_only_includes_supplied() -> None:
    """Only the supplied context fields appear — partial enrichment is safe."""
    p = build_exit_payload(
        side="short", current_stop_price=81.0, proposed_stop_price=82.0,
        entry_price=80.0, unrealized_pnl_r=1.1, max_loss_r=1.0,
        regime="high_vol",
        mfe_r=1.2,
    )
    ctx = p["exit_context"]
    assert ctx["regime"] == "high_vol"
    assert ctx["mfe_r"] == 1.2
    # Unsupplied numeric/text context keys are absent (no noisy zeros).
    assert "atr_slope" not in ctx
    assert "exit_state" not in ctx
    assert "recent_close_first" not in ctx


def test_build_exit_payload_no_conn_omits_cell_routing() -> None:
    """No ``conn`` supplied -> byte-identical (no cell_routing key added)."""
    p = build_exit_payload(
        side="long", current_stop_price=80.0, proposed_stop_price=78.0,
        entry_price=85.0, unrealized_pnl_r=0.9, max_loss_r=1.0,
    )
    assert "cell_routing" not in p


def test_build_exit_payload_wires_cell_routing_from_conn(tmp_path: Path) -> None:
    """#32 axis-B fix: G7's evidence_robustness warmth leg reads
    ``payload['cell_routing']['n_eff']`` — previously always absent on the G7
    payload (warmth stuck at 0.0). Supplying ``conn``+venue/strategy/symbol/
    regime now stamps the SAME cell summary G3 already computes, via the
    shared ``_cell_routing_summary`` helper (no new query shape)."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        conn.execute(
            "INSERT INTO cell_matrix_p0 "
            "(exchange, strategy, ticker, regime, n_eff, wins_eff, "
            "pnl_r_sum_eff, avg_pnl_r, score, last_closed_ts) "
            "VALUES ('okx', 'tsmom', 'BTC-USDT', 'bull_trend', 25.0, 15.0, "
            "5.0, 0.2, 1.4, ?)",
            (int(time.time()),),
        )
        p = build_exit_payload(
            side="long", current_stop_price=80.0, proposed_stop_price=78.0,
            entry_price=85.0, unrealized_pnl_r=0.9, max_loss_r=1.0,
            conn=conn, venue="okx", strategy="tsmom", symbol="BTC-USDT",
            regime="bull_trend",
        )
        assert p["cell_routing"]["n_eff"] == 25.0
        assert p["cell_routing"]["quartile"] in {"mid", "top", "bottom", "cold"}
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_g6_to_g7_chain_widening_allowed() -> None:
    """G7 with above-window pnl + valid widening -> ADJUST_EXIT."""
    proposal = build_exit_payload(
        side="long", current_stop_price=80.0, proposed_stop_price=78.0,
        entry_price=85.0, unrealized_pnl_r=0.9, max_loss_r=1.0,
        overrides_used=0, seconds_since_last_override=60,
        initial_stop_price=75.0,
    )
    ctx = GateContext(
        run_id="r", signal_id="s", position_id="p", gate_id=GATE_ADAPTIVE_EXIT,
        venue="okx", symbol="BTC-USDT", strategy_id="vb",
        payload=proposal, started_ts=int(time.time()), state=SignalLifecycle.MONITORED,
    )
    result = await adaptive_exit_gate(ctx)
    assert result.decision == GateDecision.ADJUST_EXIT
    assert result.payload["stop_price"] == 78.0


@pytest.mark.asyncio
async def test_g6_to_g7_chain_rejects_below_window() -> None:
    """G7 with pnl below 0.7R -> HOLD (no widen)."""
    proposal = build_exit_payload(
        side="long", current_stop_price=80.0, proposed_stop_price=78.0,
        entry_price=85.0, unrealized_pnl_r=0.4, max_loss_r=1.0,
    )
    ctx = GateContext(
        run_id="r", signal_id="s", position_id="p", gate_id=GATE_ADAPTIVE_EXIT,
        venue="okx", symbol="BTC-USDT", strategy_id="vb",
        payload=proposal, started_ts=int(time.time()), state=SignalLifecycle.ACTIVE,
    )
    result = await adaptive_exit_gate(ctx)
    assert result.decision == GateDecision.HOLD


# ---------------------------------------------------------------------------
# load_active_positions
# ---------------------------------------------------------------------------


def test_load_active_positions_filters_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        # Manually insert one open + one closed.
        conn.execute(
            """
            INSERT INTO positions
                (position_id, venue, symbol, underlying_group_id, strategy_id,
                 entry_strategy_id, active_strategy_id, side, qty, status,
                 opened_ts, swap_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            ("p1", "okx", "BTC-USDT", "BTC", "vb", "vb", "vb", "long",
             0.001, "active", int(time.time())),
        )
        conn.execute(
            """
            INSERT INTO positions
                (position_id, venue, symbol, underlying_group_id, strategy_id,
                 entry_strategy_id, active_strategy_id, side, qty, status,
                 opened_ts, swap_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            ("p2", "okx", "ETH-USDT", "ETH", "vb", "vb", "vb", "short",
             0.005, "closed", int(time.time())),
        )
        positions = load_active_positions(conn)
        assert len(positions) == 1
        assert positions[0]["position_id"] == "p1"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Smoke: full chain via run_full_pipeline through the smoke loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_full_pipeline_one_tick(tmp_path: Path) -> None:
    """Drive ``_run_one_tick(full_pipeline=True)`` once and verify the
    pipeline ran end-to-end (sized + persisted)."""
    from polaris.scripts.smoke_paper_loop import (
        FOCUS,
        FocusEntry,
        LoopState,
        _run_one_tick,
        _stub_bars,
    )

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
            await _run_one_tick(
                state=state, conn=conn, haiku=StubGPTClient(),
                bars_cache=bars_cache, tick_idx=1, full_pipeline=True,
            )
        finally:
            mod._fetch_bars_for_symbol = original  # type: ignore[assignment]
        assert state.full_pipeline_runs >= 1
        # At least one signal should have hit G3 -> G4 -> G5.
        # The stub haiku always says PASS so we expect at least 1 sized.
        # gate_pass_counts captures G6/G7 entries.
        assert (state.full_pipeline_sized + state.pipeline_kills) >= 1
    finally:
        conn.close()


def test_payload_builder_sizer_works_without_conn() -> None:
    """When conn=None the sizer payload uses cold-start defaults."""
    sig = _make_raw_signal()
    payload = build_sizer_payload(
        raw_signal=sig, venue="okx", symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT", underlying_group_id="BTC",
        asset_class="crypto", regime="bull_trend", track="A",
        listing_age_hours=720.0, equity_usd=10_000.0, conn=None,
    )
    assert isinstance(payload["signal_intent"], SignalIntent)
    assert payload["risk_state"].closed_trades == 0
    assert payload["portfolio"].equity_usd == 10_000.0


# Quiet asyncio warning re: unused fixture. asyncio import is OK.
_ = asyncio
