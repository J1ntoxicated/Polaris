"""pts-classes (group D) — compute_size EARN/PROVE/BENCH class-aware routing.

Spec source: MEMORY.md group-D task header. Scope: ``compute_size`` (~engine.py
642) branches on ``SignalIntent.strategy_class``:

  * EARN — the EXISTING %-of-equity T4 chain, byte-identical (regression).
  * PROVE — admission gate (``stop_dist_pct > 3x round-trip fee_rate``); a
    signal that fails admission OR hits a shadow trigger (bottom-suppression
    cell / learner anti-edge) is SHADOW-routed (notional forced to 0, still
    fully computed for learning); an admitted, non-shadow-triggered PROVE
    signal is sized at the fixed ``probe_notional_usd`` floor instead of the
    %-equity chain.
  * BENCH — always shadow-routed (notional 0) — the signal still flows
    through the WHOLE T4 compute (proposal/cell/tier/etc. all still resolved)
    for shadow-pricing/learning, it is simply never placed at full size
    (aggressive_always_profit / no_block_filter_architecture: BENCH is a
    capital-ROUTING decision, never a block/reject).

DEMO/PAPER only. Aggressive bias preserved. 9-stack ban: the class branch is
the LAST step before ``SizingFinal`` construction — it never adds a T4
multiplier, only overrides the FINAL notional/risk_pct/binding_cap output for
PROVE/BENCH (EARN takes the branch not-at-all -> byte-identical chain).
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.cell_matrix import CellContext, CellKeyP0, TradeClose, update_on_trade_close
from polaris.core.sizing import PortfolioState, SignalIntent, StrategyRiskState, compute_size
from polaris.core.sizing.probe_notional import prove_stop_dist_floor_pct, venue_min_notional_usd

NOW = 1_780_000_000


def _ctx() -> CellContext:
    return CellContext(group="spot_intraday", session="asia", direction="long", liquidity_tier="high")


def _risk_state(n: int = 25) -> StrategyRiskState:
    return StrategyRiskState(
        venue="okx", strategy="volume_burst",
        closed_trades=n, kelly_p=0.55, kelly_q=0.45,
        kelly_fraction=0.05, win_streak=2, hit_rate_10=0.55,
        updated_ts=NOW,
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity_usd=10_000.0,
        venue_daily_used_pct=0.0,
        total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0},
        open_positions=[],
        fill_rate_active_cut=False,
    )


def _intent(
    *,
    strategy_class: str = "EARN",
    symbol: str = "PL24-USDT",
    atr_pct: float = 0.0,
    stop_atr_mult: float = 0.0,
) -> SignalIntent:
    return SignalIntent(
        signal_id="sig-1", venue="okx", symbol=symbol,
        instrument_id=f"okx:{symbol}", underlying_group_id="crypto:PL",
        asset_class="crypto", strategy="volume_burst", track="A",
        regime="bull_trend", direction="long", signal_strength=1.2,
        listing_age_hours=72.0, leverage=1.0, base_risk_pct=0.02,
        strategy_class=strategy_class, atr_pct=atr_pct, stop_atr_mult=stop_atr_mult,
    )


def _seed_mid_cell(conn: sqlite3.Connection, symbol: str = "PL24-USDT") -> None:
    """A single cell with no strong quartile signal -> neutral (1.0) cell_mult
    (below CELL_MIN_POOL_SIZE=20 eligible-pool gate, so routing stays neutral —
    the SAME shape test_layer3_sizing_full.py's ungated tests rely on)."""
    tr = TradeClose(
        key=CellKeyP0("okx", "volume_burst", symbol, "bull_trend"),
        context=_ctx(), pnl_r=0.1, won=True, closed_ts=NOW,
    )
    update_on_trade_close(conn, tr)


def _seed_bottom_quartile_cell(conn: sqlite3.Connection, symbol: str = "PL24-USDT") -> None:
    """25-cell pool with ``symbol`` in the bottom quartile (mirrors
    test_layer3_sizing_full._seed_top_quartile_cell, inverted)."""
    for i in range(25):
        for j in range(25):
            tick = symbol if i == 0 else f"OTHER{i:02d}-USDT"
            tr = TradeClose(
                key=CellKeyP0("okx", "volume_burst", tick, "bull_trend"),
                context=_ctx(),
                pnl_r=(i - 12) * 0.1,
                won=(i - 12) * 0.1 > 0,
                closed_ts=NOW + j,
            )
            update_on_trade_close(conn, tr)


def _seed_anti_edge_posterior(conn: sqlite3.Connection, symbol: str = "PL24-USDT") -> None:
    conn.execute(
        "INSERT INTO learner_posterior (exchange, strategy, ticker, regime, p_pos, "
        "n_samples, updated_ts) VALUES ('okx', 'volume_burst', ?, 'bull_trend', 0.10, 25, 0)",
        (symbol,),
    )


# ---------------------------------------------------------------------------
# EARN — byte-identical regression
# ---------------------------------------------------------------------------


def test_earn_class_is_byte_identical_to_default_chain(memdb: sqlite3.Connection) -> None:
    _seed_mid_cell(memdb)
    earn = compute_size(
        memdb, intent=_intent(strategy_class="EARN"), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    default = compute_size(
        memdb, intent=_intent(), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert earn.final_risk_pct == default.final_risk_pct
    assert earn.final_notional_usd == default.final_notional_usd
    assert earn.binding_cap == default.binding_cap
    assert earn.final_notional_usd > 0.0


def test_earn_class_never_touches_probe_notional_floor(memdb: sqlite3.Connection) -> None:
    """A tiny-equity EARN signal still runs the ordinary %-equity chain (not
    the probe floor) -- EARN takes NONE of the class-branch logic."""
    _seed_mid_cell(memdb)
    tiny_portfolio = PortfolioState(
        equity_usd=1.0, venue_daily_used_pct=0.0, total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0}, open_positions=[], fill_rate_active_cut=False,
    )
    sized = compute_size(
        memdb, intent=_intent(strategy_class="EARN"), risk_state=_risk_state(),
        portfolio=tiny_portfolio, now_ts=NOW + 100,
    )
    assert sized.final_notional_usd != pytest.approx(venue_min_notional_usd("okx"))


# ---------------------------------------------------------------------------
# PROVE — admission gate: pass / fail
# ---------------------------------------------------------------------------


def test_prove_admitted_sizes_at_probe_notional_floor(memdb: sqlite3.Connection) -> None:
    _seed_mid_cell(memdb)
    floor = prove_stop_dist_floor_pct("okx")
    intent = _intent(strategy_class="PROVE", atr_pct=floor * 10.0, stop_atr_mult=1.0)
    sized = compute_size(
        memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.final_notional_usd == pytest.approx(venue_min_notional_usd("okx"))
    assert sized.binding_cap == "prove_probe_notional"


def test_prove_admission_fail_shadow_routes_to_zero(memdb: sqlite3.Connection) -> None:
    """stop_dist_pct far below the 3x round-trip fee floor -> SHADOW (never a
    hard reject -- the whole chain still computed above, only the final
    output is shadowed)."""
    _seed_mid_cell(memdb)
    intent = _intent(strategy_class="PROVE", atr_pct=0.0001, stop_atr_mult=1.0)
    sized = compute_size(
        memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.final_notional_usd == 0.0
    assert sized.final_risk_pct == 0.0
    assert sized.binding_cap == "prove_shadow"
    # Still fully computed (audit trail intact -- flow_not_block, not a KILL).
    assert sized.proposed.proposed_risk_pct > 0.0


def test_prove_no_atr_data_shadow_routes(memdb: sqlite3.Connection) -> None:
    """atr_pct<=0 (not supplied / data-integrity gap) -> admission can never
    pass -> shadow, never a crash."""
    _seed_mid_cell(memdb)
    sized = compute_size(
        memdb, intent=_intent(strategy_class="PROVE"), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.final_notional_usd == 0.0
    assert sized.binding_cap == "prove_shadow"


# ---------------------------------------------------------------------------
# PROVE — anti-edge routing (bottom-suppression cell / learner anti-edge)
#
# B (2026-07-08): under the default POLARIS_PROVE_PROBE_ON_ANTI_EDGE=1, an
# anti-edge cell no longer FORCES shadow — a PROVE probe fires a small REAL size
# so the track accrues real fill/dwell evidence (breaks the shadow catch-22).
# The legacy shadow-on-anti-edge routing is preserved behind the flag=0.
# ---------------------------------------------------------------------------


def test_prove_bottom_cell_probes_real_size_under_default(memdb: sqlite3.Connection) -> None:
    """Bottom-suppression cell + admitted -> probe fires a REAL min-notional
    size (was shadow pre-B). The cell mult (0.5) is informational on the
    proposal; the probe is a fixed floor, not cell-mult-shrunk."""
    _seed_bottom_quartile_cell(memdb, symbol="BOT-USDT")
    floor = prove_stop_dist_floor_pct("okx")
    intent = _intent(
        strategy_class="PROVE", symbol="BOT-USDT", atr_pct=floor * 10.0, stop_atr_mult=1.0,
    )
    sized = compute_size(
        memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.proposed.cell_routing_mult == 0.5
    assert sized.final_notional_usd == pytest.approx(venue_min_notional_usd("okx"))
    assert sized.binding_cap == "prove_probe_notional"


def test_prove_anti_edge_learner_probes_real_size_under_default(
    memdb: sqlite3.Connection,
) -> None:
    """Learner anti-edge posterior + admitted -> probe fires REAL size (was
    shadow pre-B). This is the catch-22 fix: the losing history that marked the
    cell anti-edge is exactly what PROVE re-tests, so it must trade to earn
    fresh evidence."""
    _seed_mid_cell(memdb, symbol="AE-USDT")
    _seed_anti_edge_posterior(memdb, symbol="AE-USDT")
    floor = prove_stop_dist_floor_pct("okx")
    intent = _intent(
        strategy_class="PROVE", symbol="AE-USDT", atr_pct=floor * 10.0, stop_atr_mult=1.0,
    )
    sized = compute_size(
        memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.final_notional_usd == pytest.approx(venue_min_notional_usd("okx"))
    assert sized.binding_cap == "prove_probe_notional"


def test_prove_anti_edge_shadow_under_legacy_flag(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POLARIS_PROVE_PROBE_ON_ANTI_EDGE=0 restores the pre-B routing: an
    anti-edge cell forces shadow (size 0). Reversibility guarantee."""
    monkeypatch.setenv("POLARIS_PROVE_PROBE_ON_ANTI_EDGE", "0")
    _seed_mid_cell(memdb, symbol="AE2-USDT")
    _seed_anti_edge_posterior(memdb, symbol="AE2-USDT")
    floor = prove_stop_dist_floor_pct("okx")
    intent = _intent(
        strategy_class="PROVE", symbol="AE2-USDT", atr_pct=floor * 10.0, stop_atr_mult=1.0,
    )
    sized = compute_size(
        memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.final_notional_usd == 0.0
    assert sized.binding_cap == "prove_shadow"
    # Still fully computed (flow_not_block audit trail intact).
    assert sized.proposed.proposed_risk_pct > 0.0


# ---------------------------------------------------------------------------
# PROVE — group F followup: probe cap consumption (slots + 24h fee)
# ---------------------------------------------------------------------------


def _admitted_prove_intent(symbol: str = "CAP-USDT") -> SignalIntent:
    floor = prove_stop_dist_floor_pct("okx")
    return _intent(
        strategy_class="PROVE", symbol=symbol, atr_pct=floor * 10.0, stop_atr_mult=1.0,
    )


def test_prove_within_slot_and_fee_cap_fires_probe(memdb: sqlite3.Connection) -> None:
    """Fresh snapshot says slot_active=True, no fee history -> probe fires
    (byte-identical to the pre-cap-consumer PROVE admitted path)."""
    _seed_mid_cell(memdb, symbol="CAP1-USDT")
    memdb.execute(
        "INSERT INTO probe_slot_assignment (run_ts, track, venue, strategy_id, rank, "
        "slot_active, reason) VALUES (?, 'A', 'okx', 'volume_burst', 1, 1, 'SLOT_ACTIVE')",
        (NOW,),
    )
    sized = compute_size(
        memdb, intent=_admitted_prove_intent("CAP1-USDT"), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.final_notional_usd == pytest.approx(venue_min_notional_usd("okx"))
    assert sized.binding_cap == "prove_probe_notional"


def test_prove_slot_exhausted_shadow_routes(memdb: sqlite3.Connection) -> None:
    """Latest snapshot says slot_active=False (concurrency cap exhausted for
    this track) -> shadow-routed even though admission + cell/anti-edge would
    otherwise pass."""
    _seed_mid_cell(memdb, symbol="CAP2-USDT")
    memdb.execute(
        "INSERT INTO probe_slot_assignment (run_ts, track, venue, strategy_id, rank, "
        "slot_active, reason) VALUES (?, 'A', 'okx', 'volume_burst', 4, 0, "
        "'CONCURRENCY_CAP_EXHAUSTED')",
        (NOW,),
    )
    sized = compute_size(
        memdb, intent=_admitted_prove_intent("CAP2-USDT"), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.final_notional_usd == 0.0
    assert sized.binding_cap == "prove_shadow"


def test_prove_fee_cap_exhausted_shadow_routes(memdb: sqlite3.Connection) -> None:
    """slot_active=True but this strategy's own probe_fee_24h already exceeds
    6xF_track_cap (track A) -> shadow-routed, not fired."""
    _seed_mid_cell(memdb, symbol="CAP3-USDT")
    memdb.execute(
        "INSERT INTO probe_slot_assignment (run_ts, track, venue, strategy_id, rank, "
        "slot_active, reason) VALUES (?, 'A', 'okx', 'volume_burst', 1, 1, 'SLOT_ACTIVE')",
        (NOW,),
    )
    memdb.execute(
        "INSERT INTO strategy_class (venue, strategy_id, strategy_class, probe_fee_24h) "
        "VALUES ('okx', 'volume_burst', 'PROVE', 999999.0) "
        "ON CONFLICT(venue, strategy_id) DO UPDATE SET probe_fee_24h = excluded.probe_fee_24h",
    )
    sized = compute_size(
        memdb, intent=_admitted_prove_intent("CAP3-USDT"), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.final_notional_usd == 0.0
    assert sized.binding_cap == "prove_shadow"


def test_prove_no_snapshot_fallback_still_admits_under_open_position_cap(
    memdb: sqlite3.Connection,
) -> None:
    """No reranker snapshot has EVER run for this track -> fallback counts
    currently-open PROVE positions (zero here) against the track cap -> still
    admits (the cap must stay alive before the reranker's first run, it must
    not silently go inert)."""
    _seed_mid_cell(memdb, symbol="CAP4-USDT")
    sized = compute_size(
        memdb, intent=_admitted_prove_intent("CAP4-USDT"), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.final_notional_usd == pytest.approx(venue_min_notional_usd("okx"))
    assert sized.binding_cap == "prove_probe_notional"


# ---------------------------------------------------------------------------
# BENCH — always shadow
# ---------------------------------------------------------------------------


def test_bench_always_shadow_routes_regardless_of_admission(memdb: sqlite3.Connection) -> None:
    _seed_mid_cell(memdb)
    floor = prove_stop_dist_floor_pct("okx")
    intent = _intent(strategy_class="BENCH", atr_pct=floor * 10.0, stop_atr_mult=1.0)
    sized = compute_size(
        memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.final_notional_usd == 0.0
    assert sized.final_risk_pct == 0.0
    assert sized.binding_cap == "bench_shadow"
    # Still fully computed for learning (aggressive_always_profit /
    # no_block_filter_architecture -- BENCH is routing, not a block).
    assert sized.proposed.proposed_risk_pct > 0.0


def test_bench_signal_still_computes_cell_and_proposal(memdb: sqlite3.Connection) -> None:
    _seed_bottom_quartile_cell(memdb, symbol="BENCH-USDT")
    intent = _intent(strategy_class="BENCH", symbol="BENCH-USDT")
    sized = compute_size(
        memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.proposed.cell_routing_mult == 0.5
    assert sized.binding_cap == "bench_shadow"


# ---------------------------------------------------------------------------
# Unknown / KILL class -> fail-safe to BENCH-style shadow (never silently EARN)
# ---------------------------------------------------------------------------


def test_unknown_strategy_class_fails_safe_to_shadow(memdb: sqlite3.Connection) -> None:
    _seed_mid_cell(memdb)
    sized = compute_size(
        memdb, intent=_intent(strategy_class="KILL"), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.final_notional_usd == 0.0
    assert sized.binding_cap == "bench_shadow"
