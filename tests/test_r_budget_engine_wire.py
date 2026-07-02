"""TDD — Wave B agenda ① engine wire (compute_size → r_budget_sizer shadow/active).

DEMO/PAPER only. Aggressive bias preserved (flow_not_block). 9-stack ban:
the active-mode swap replaces the %-equity notional/risk_pct OUTPUT with the
R-budget qty result at a SINGLE point — it does not stack a new multiplier
onto the existing T4 chain (the pre-cap ``proposal`` audit trail is untouched).

Spec source: vault/50_research/debates/waveB_sizing_params_2026-07-02.md
Wire spec: engine.py compute_size T4 output → r_budget_sizer shadow wire.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace

import pytest

from polaris.core.sizing.engine import SignalIntent, compute_size
from polaris.core.sizing.r_budget_sizer import read_shadow_state
from polaris.core.sizing.schema import PortfolioState, SizingFinal, StrategyRiskState

NOW = 1_780_000_000


def _intent(
    *, entry_price: float = 100.0, atr_pct: float = 0.02, strategy: str = "volume_burst",
    signal_id: str = "sig-wire", stop_atr_mult: float = 0.0,
) -> SignalIntent:
    return SignalIntent(
        signal_id=signal_id,
        venue="okx",
        symbol="PL24-USDT",
        instrument_id="okx:PL24-USDT",
        underlying_group_id="crypto:PL",
        asset_class="crypto",
        strategy=strategy,
        track="A",
        regime="bull_trend",
        direction="long",
        signal_strength=1.0,
        listing_age_hours=72.0,
        leverage=1.0,
        base_risk_pct=0.02,
        entry_price=entry_price,
        atr_pct=atr_pct,
        stop_atr_mult=stop_atr_mult,
    )


def _risk_state() -> StrategyRiskState:
    return StrategyRiskState(
        venue="okx", strategy="volume_burst",
        closed_trades=25, kelly_p=0.55, kelly_q=0.45,
        kelly_fraction=0.05, win_streak=0, hit_rate_10=0.55,
        updated_ts=NOW,
    )


def _portfolio(equity_usd: float = 10_000.0) -> PortfolioState:
    return PortfolioState(
        equity_usd=equity_usd,
        venue_daily_used_pct=0.0,
        total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0},
        open_positions=[],
        fill_rate_active_cut=False,
    )


# ---------------------------------------------------------------------------
# (e) firing guard — compute_size actually CALLS compute_r_budget_qty
# ---------------------------------------------------------------------------


def test_engine_source_imports_compute_r_budget_qty() -> None:
    """String-level guard: engine.py must import the PRIMARY compute function,
    not just fold_strength_scalar (the pre-wire bug this task fixes)."""
    import inspect

    from polaris.core.sizing import engine

    src = inspect.getsource(engine)
    assert "compute_r_budget_qty" in src
    assert "record_shadow_observation" in src


def test_engine_actually_invokes_compute_r_budget_qty_at_runtime(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execution-level guard (not just a string match): monkeypatch the real
    function and assert it gets called on a normal SIZED decision."""
    import polaris.core.sizing.engine as engine_mod

    calls: list[bool] = []
    original = engine_mod.compute_r_budget_qty

    def _spy(**kwargs: object) -> object:
        calls.append(True)
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(engine_mod, "compute_r_budget_qty", _spy)
    compute_size(memdb, intent=_intent(), risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW)
    assert calls, "compute_r_budget_qty was never invoked by compute_size"


# ---------------------------------------------------------------------------
# (a) SIZED decision -> shadow log + counter increments
# ---------------------------------------------------------------------------


def test_shadow_log_line_emitted_on_sized_decision(
    memdb: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="polaris.core.sizing.engine")
    compute_size(memdb, intent=_intent(), risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW)
    assert any("[T4/rbudget-shadow]" in r.message for r in caplog.records)


def test_shadow_counter_increments_on_sized_decision(memdb: sqlite3.Connection) -> None:
    before = read_shadow_state(memdb)
    assert before.n_observed == 0
    compute_size(memdb, intent=_intent(), risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW)
    after = read_shadow_state(memdb)
    assert after.n_observed == 1


def test_shadow_counter_does_not_increment_when_notional_is_zero(
    memdb: sqlite3.Connection,
) -> None:
    """A KILL'd/zeroed signal (base_risk_pct=0 -> proposed=0 -> final=0) never
    reaches the shadow compute — nothing to observe."""
    intent = _intent()
    zero_intent = replace(intent, base_risk_pct=0.0)
    compute_size(memdb, intent=zero_intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW)
    state = read_shadow_state(memdb)
    assert state.n_observed == 0


def test_shadow_skips_and_logs_fallback_when_entry_price_absent(
    memdb: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """Callers that haven't threaded entry_price/atr_pct yet (default 0.0) get
    the data-integrity fallback log, not a crash, and no observation recorded."""
    caplog.set_level(logging.INFO, logger="polaris.core.sizing.engine")
    intent = _intent(entry_price=0.0, atr_pct=0.0)
    result = compute_size(memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW)
    assert isinstance(result, SizingFinal)
    assert result.final_risk_pct > 0.0  # %-equity path still produced a real size
    assert any("SKIP data-integrity-fallback" in r.message for r in caplog.records)
    state = read_shadow_state(memdb)
    assert state.n_observed == 0


def test_shadow_skips_on_zero_atr_stale_data(memdb: sqlite3.Connection, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="polaris.core.sizing.engine")
    intent = _intent(entry_price=100.0, atr_pct=0.0)
    compute_size(memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW)
    state = read_shadow_state(memdb)
    assert state.n_observed == 0


# ---------------------------------------------------------------------------
# (b) N reached -> mode flips to active (persisted, restart-safe)
# ---------------------------------------------------------------------------


def test_mode_flips_to_active_after_n_observations(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POLARIS_RBUDGET_SHADOW_N", "3")
    monkeypatch.setenv("POLARIS_RBUDGET_SHADOW_HOURS", "999999")
    for i in range(3):
        compute_size(
            memdb, intent=_intent(signal_id=f"sig-{i}"), risk_state=_risk_state(),
            portfolio=_portfolio(), now_ts=NOW + i,
        )
    state = read_shadow_state(memdb)
    assert state.flipped_active is True
    assert state.n_observed == 3


def test_mode_stays_shadow_before_n_reached(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POLARIS_RBUDGET_SHADOW_N", "5")
    monkeypatch.setenv("POLARIS_RBUDGET_SHADOW_HOURS", "999999")
    for i in range(2):
        compute_size(
            memdb, intent=_intent(signal_id=f"sig-{i}"), risk_state=_risk_state(),
            portfolio=_portfolio(), now_ts=NOW + i,
        )
    state = read_shadow_state(memdb)
    assert state.flipped_active is False


# ---------------------------------------------------------------------------
# (c) active mode: unclipped full-stop pnl ~= -R_budget*T4_mult +-10%; clipped
# case logs binding cap and result stays SizingFinal-shaped.
# ---------------------------------------------------------------------------


def test_active_mode_unclipped_full_stop_pnl_matches_r_budget_within_10pct(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Property (c), unclipped half: reproduce the SAME compute_r_budget_qty
    call the engine wire performs (with caps opened to +inf, isolating the
    PRIMARY formula from the real single-trade/margin caps that legitimately
    bind at these inputs) and assert the pure-function identity — this is the
    engine-level analog of test_r_budget_sizer.py's pure property test, using
    the SSOT venue R_budget (risk_unit.r_budget_for_venue) the wire reads at
    runtime, and the fee-floored stop_atr_mult threaded via SignalIntent (the
    scripts-layer caller's job — core never imports exit_strategy_config)."""
    from polaris.core.metrics.risk_unit import r_budget_for_venue
    from polaris.core.sizing.r_budget_sizer import CapQtyInputs, compute_r_budget_qty
    from polaris.scripts.exit_strategy_config import _stop_atr_mult_for_strategy

    monkeypatch.setenv("POLARIS_RBUDGET_SIZING_MODE", "active")
    stop_mult = _stop_atr_mult_for_strategy("volume_burst", atr_pct=0.02)
    intent = _intent(entry_price=100.0, atr_pct=0.02, stop_atr_mult=stop_mult)
    r_budget_usd = r_budget_for_venue("okx")
    venue_equity = r_budget_usd / 0.02  # BASE_RISK_PCT inverse — the SSOT venue equity
    result = compute_size(
        memdb, intent=intent, risk_state=_risk_state(),
        portfolio=_portfolio(equity_usd=venue_equity), now_ts=NOW,
    )
    assert result.binding_cap == "r_budget_active"

    t4_mult = result.proposed.proposed_risk_pct / result.proposed.base_risk_pct
    unclipped = compute_r_budget_qty(
        r_budget_usd=r_budget_usd, t4_mult=t4_mult, entry_price=intent.entry_price,
        atr_pct=intent.atr_pct, stop_atr_mult=stop_mult,
        caps=CapQtyInputs(single_trade_qty=float("inf"), per_symbol_qty=float("inf"), margin_qty=float("inf")),
    )
    assert unclipped is not None
    assert not unclipped.cap_dominated
    full_stop_pnl = unclipped.qty_unclipped * (intent.entry_price * intent.atr_pct * stop_mult)
    assert full_stop_pnl == pytest.approx(r_budget_usd * t4_mult, rel=0.10)
    # The ENGINE result (with the real caps) must be <= the unclipped notional
    # — the single min() only ever shrinks, never inflates.
    assert result.final_notional_usd <= unclipped.qty_unclipped * intent.entry_price + 1e-6


def test_active_mode_clipped_case_logs_binding_cap(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("POLARIS_RBUDGET_SIZING_MODE", "active")
    caplog.set_level(logging.INFO, logger="polaris.core.sizing.engine")
    # Tiny equity -> single_trade_cap_pct * equity * lev / price becomes the
    # binding qty ceiling well below the unclipped R-budget qty.
    intent = _intent(entry_price=100.0, atr_pct=0.02)
    result = compute_size(
        memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(equity_usd=1.0), now_ts=NOW,
    )
    assert result.binding_cap == "r_budget_active"
    assert any("binding=" in r.message for r in caplog.records if "[T4/rbudget-shadow]" in r.message)


def test_active_mode_never_produces_negative_or_nonfinite_output(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    import math

    monkeypatch.setenv("POLARIS_RBUDGET_SIZING_MODE", "active")
    result = compute_size(memdb, intent=_intent(), risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW)
    assert result.final_notional_usd >= 0.0
    assert math.isfinite(result.final_notional_usd)
    assert result.final_risk_pct >= 0.0
    assert math.isfinite(result.final_risk_pct)


# ---------------------------------------------------------------------------
# (d) env override 3-mode behavior threaded through compute_size
# ---------------------------------------------------------------------------


def test_env_mode_off_never_swaps_notional_and_never_records(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POLARIS_RBUDGET_SIZING_MODE", "off")
    intent = _intent()
    result = compute_size(memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW)
    assert result.binding_cap != "r_budget_active"
    state = read_shadow_state(memdb)
    assert state.n_observed == 0


def test_env_mode_shadow_records_but_never_swaps_notional(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POLARIS_RBUDGET_SIZING_MODE", "shadow")
    intent = _intent()
    baseline = compute_size(memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW)
    assert baseline.binding_cap != "r_budget_active"
    state = read_shadow_state(memdb)
    assert state.n_observed == 1


def test_env_mode_active_forces_swap_even_below_n_threshold(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POLARIS_RBUDGET_SIZING_MODE", "active")
    monkeypatch.setenv("POLARIS_RBUDGET_SHADOW_N", "999999")
    result = compute_size(memdb, intent=_intent(), risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW)
    assert result.binding_cap == "r_budget_active"


# ---------------------------------------------------------------------------
# Regression: shadow mode is a pure observer — final_risk_pct/notional
# byte-identical to the pre-wire %-equity computation.
# ---------------------------------------------------------------------------


def test_shadow_mode_notional_byte_identical_to_pct_equity_path(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("POLARIS_RBUDGET_SIZING_MODE", raising=False)
    intent_no_shadow_inputs = _intent(entry_price=0.0, atr_pct=0.0)
    intent_with_shadow_inputs = _intent(entry_price=100.0, atr_pct=0.02)
    r1 = compute_size(
        memdb, intent=intent_no_shadow_inputs, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW,
    )
    r2 = compute_size(
        memdb, intent=intent_with_shadow_inputs, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 1,
    )
    assert r1.final_notional_usd == pytest.approx(r2.final_notional_usd)
    assert r1.final_risk_pct == pytest.approx(r2.final_risk_pct)


# ---------------------------------------------------------------------------
# Regression (fresh-fixer blocker fix): every existing exposure cap — not just
# single_trade/per_symbol — must be threaded into CapQtyInputs and able to
# bind in ACTIVE mode. Before this fix, track/venue_daily/total_daily/cluster/
# underlying were left at the CapQtyInputs dataclass ``None`` default, which
# qty_headroom_min treats as "not configured" and silently excludes from
# min() — these caps were unenforced on the R-budget-active qty path.
# ---------------------------------------------------------------------------


def test_active_mode_track_gross_cap_binds_when_track_nearly_exhausted(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leave a tiny sliver of track-A headroom (legacy %-path still > 0, so
    the shadow wire actually runs) — the R-budget qty side must clip to that
    sliver, proving track_qty is threaded (pre-fix: always None -> never
    binding, so an exhausted track cap had ZERO effect on active-mode qty)."""
    from polaris.core.sizing.schema import track_a_gross_pct

    monkeypatch.setenv("POLARIS_RBUDGET_SIZING_MODE", "active")
    sliver = 1e-5
    portfolio = replace(
        _portfolio(equity_usd=1_000_000.0),
        track_used_pct={"A": track_a_gross_pct() - sliver, "B": 0.0},
    )
    result = compute_size(
        memdb, intent=_intent(entry_price=100.0, atr_pct=0.02), risk_state=_risk_state(),
        portfolio=portfolio, now_ts=NOW,
    )
    assert result.binding_cap == "r_budget_active"
    # qty-equivalent of the sliver: sliver × equity × leverage / price
    expected_notional_ceiling = sliver * portfolio.equity_usd * 1.0
    assert result.final_notional_usd <= expected_notional_ceiling + 1e-6


def test_active_mode_total_daily_ceiling_binds_when_daily_budget_nearly_exhausted(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leave a tiny sliver of total-daily headroom -> R-budget active qty
    must clip to that sliver. Pre-fix this cap was never threaded
    (CapQtyInputs.total_daily_qty stayed None) so an exhausted daily risk
    ceiling had ZERO effect on the active R-budget qty output."""
    from polaris.core.sizing.schema import total_daily_risk_ceiling_pct

    monkeypatch.setenv("POLARIS_RBUDGET_SIZING_MODE", "active")
    sliver = 1e-5
    portfolio = replace(
        _portfolio(equity_usd=1_000_000.0),
        total_daily_used_pct=total_daily_risk_ceiling_pct() - sliver,
    )
    result = compute_size(
        memdb, intent=_intent(entry_price=100.0, atr_pct=0.02), risk_state=_risk_state(),
        portfolio=portfolio, now_ts=NOW,
    )
    assert result.binding_cap == "r_budget_active"
    expected_notional_ceiling = sliver * portfolio.equity_usd * 1.0
    assert result.final_notional_usd <= expected_notional_ceiling + 1e-6


def test_cap_qty_inputs_populates_all_seven_cap_fields() -> None:
    """Direct unit check on the converter: every CapQtyInputs field the debate
    spec named (symbol/cluster/track/margin/env-ceiling, plus underlying/
    venue_daily/total_daily) must be non-None after conversion — the exact
    gap the blocker flagged (only single_trade/per_symbol/margin were wired)."""
    from polaris.core.sizing.engine import _cap_qty_inputs_from_pcts

    caps = _cap_qty_inputs_from_pcts(
        single_trade_cap_pct=0.05,
        per_symbol_remaining_pct=0.10,
        margin_qty_basis_pct=1.0,
        underlying_remaining_pct=0.08,
        cluster_remaining_pct=0.20,
        track_remaining_pct=0.30,
        venue_daily_remaining_pct=0.40,
        total_daily_remaining_pct=0.50,
        env_ceiling_pct=0.60,
        equity_usd=10_000.0,
        leverage=1.0,
        entry_price=100.0,
    )
    assert caps.single_trade_qty > 0.0
    assert caps.per_symbol_qty > 0.0
    assert caps.margin_qty > 0.0
    assert caps.underlying_qty is not None and caps.underlying_qty > 0.0
    assert caps.cluster_qty is not None and caps.cluster_qty > 0.0
    assert caps.track_qty is not None and caps.track_qty > 0.0
    assert caps.venue_daily_qty is not None and caps.venue_daily_qty > 0.0
    assert caps.total_daily_qty is not None and caps.total_daily_qty > 0.0
    assert caps.env_ceiling_qty is not None and caps.env_ceiling_qty > 0.0
