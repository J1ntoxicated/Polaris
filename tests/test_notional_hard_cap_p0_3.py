"""P0-3 + P1-7 — venue notional hard cap + venue-aware equity.

DEMO/PAPER ONLY (virtual funds). Pins the fix that replaces the SILENT
post-hoc ``max(10.0, min(x, 5_000.0))`` clamp formerly applied in
``_production_run_signal.py`` AFTER T4 had already computed a correct
``final_notional_usd`` (unlogged, collapsed every signal above $5k to the
identical $5,000 regardless of the continuous scalar / tier amplifier —
2026-05-29 ee82437). The replacement is a venue-aware notional cap folded
INTO the single ``headroom_min()`` clip (``notional_ceiling_pct``) with a
structured ``binding=`` log when it clips — no new T4 multiplier, 9-stack
ban + -1.0R rail intact (aggressive bias preserved, flow_not_block).

Also pins P1-7: ``equity_usd_for_venue`` sizes a Capital signal against its
own $51,000 demo balance instead of the OKX $79,000 figure.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import pytest

import polaris.scripts._production_run_signal as run_signal_mod
from polaris.core.pipeline.gate_state import GateDecision, GateResult
from polaris.core.sizing import PortfolioState, SignalIntent, StrategyRiskState, compute_size
from polaris.core.sizing.constants import (
    CAPITAL_DEMO_STARTING_EQUITY_USD,
    OKX_DEMO_STARTING_EQUITY_USD,
    equity_usd_for_venue,
)
from polaris.core.sizing.engine import notional_ceiling_pct, notional_hard_cap_usd
from polaris.storage.schema import ALL_DDL
from polaris.strategies import RawSignal
from polaris.strategies.spot_donchian import SpotDonchianStrategy

NOW = 1_780_000_000


def _intent(
    *, venue: str = "okx", signal_strength: float = 1.0, leverage: float = 1.0,
    base_risk_pct: float = 0.02,
) -> SignalIntent:
    return SignalIntent(
        signal_id="sig-notional",
        venue=venue,
        symbol="BTC-USDT",
        instrument_id=f"{venue}:BTC-USDT",
        underlying_group_id="crypto:BTC",
        asset_class="crypto",
        strategy="volume_burst",
        track="A" if venue == "okx" else "B",
        regime="bull_trend",
        direction="long",
        signal_strength=signal_strength,
        listing_age_hours=72.0,
        leverage=leverage,
        base_risk_pct=base_risk_pct,
    )


def _risk_state(venue: str = "okx") -> StrategyRiskState:
    # Cold-start (n<20) is irrelevant here — n=25 puts Kelly/tier on the
    # non-cold path so single_trade_cap is governed by the absolute ceiling /
    # notional cap, not CS-3.
    return StrategyRiskState(
        venue=venue, strategy="volume_burst",
        closed_trades=25, kelly_p=0.55, kelly_q=0.45,
        kelly_fraction=0.05, win_streak=0, hit_rate_10=0.55,
        updated_ts=NOW,
    )


def _portfolio(equity_usd: float, *, track: str = "A") -> PortfolioState:
    return PortfolioState(
        equity_usd=equity_usd,
        venue_daily_used_pct=0.0,
        total_daily_used_pct=0.0,
        track_used_pct={track: 0.0},
        open_positions=[],
        fill_rate_active_cut=False,
    )


# ---------------------------------------------------------------------------
# (a) cont 0.75 vs 1.50 produce DIFFERENT fill notional (the old clamp
# silently converged both to the same $5,000 above the ceiling).
# ---------------------------------------------------------------------------


def test_low_vs_high_continuous_scalar_yield_different_notional(
    memdb: sqlite3.Connection,
) -> None:
    # base_risk_pct=0.043 mirrors the audit scenario (re-tuned for the
    # 2026-07-07 $100k uniform virtual seed, was 0.055 @ $79k): weak
    # (cont=0.75) prices BELOW the old $5,000 clamp, strong (cont=1.50)
    # prices ABOVE it (and below the 0.09×equity absolute ceiling, so
    # neither hits a DIFFERENT cap — the divergence is purely the
    # continuous scalar doing its job).
    weak = compute_size(
        memdb, intent=_intent(signal_strength=0.5, base_risk_pct=0.043),  # cont floor 0.75
        risk_state=_risk_state(), portfolio=_portfolio(OKX_DEMO_STARTING_EQUITY_USD),
        now_ts=NOW,
    )
    strong = compute_size(
        memdb, intent=_intent(signal_strength=1.5, base_risk_pct=0.043),  # cont ceiling 1.50
        risk_state=_risk_state(), portfolio=_portfolio(OKX_DEMO_STARTING_EQUITY_USD),
        now_ts=NOW,
    )
    assert strong.final_notional_usd > weak.final_notional_usd
    # The whole point of the fix: a signal correctly priced ABOVE the old
    # $5,000 clamp must actually FILL above $5,000, not collapse to it.
    assert strong.final_notional_usd > 5_000.0
    assert weak.final_notional_usd < 5_000.0
    # And they must NOT have converged to an identical clipped value.
    assert strong.final_notional_usd != pytest.approx(weak.final_notional_usd)


# ---------------------------------------------------------------------------
# (b) binding log exists when the venue notional hard cap is the tightest
# single-trade sub-term.
# ---------------------------------------------------------------------------


def test_notional_hard_cap_binding_log_emitted(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Force a tiny per-venue notional cap so it is the tightest sub-term (well
    # below the absolute-ceiling-derived dollar amount at this equity).
    monkeypatch.setenv("POLARIS_NOTIONAL_CAP_OKX_USD", "100.0")
    with caplog.at_level(logging.INFO, logger="polaris.core.sizing.engine"):
        sized = compute_size(
            memdb, intent=_intent(signal_strength=1.5),
            risk_state=_risk_state(), portfolio=_portfolio(OKX_DEMO_STARTING_EQUITY_USD),
            now_ts=NOW,
        )
    assert sized.binding_cap == "single_trade"
    assert sized.final_notional_usd == pytest.approx(100.0, rel=1e-6)
    binding_logs = [
        r.getMessage() for r in caplog.records
        if "[T4/single-trade-clip]" in r.getMessage()
    ]
    assert len(binding_logs) == 1
    assert "binding=notional_hard_cap" in binding_logs[0]


def test_notional_hard_cap_pct_zero_when_leverage_or_equity_non_positive() -> None:
    assert notional_ceiling_pct(venue="okx", equity_usd=0.0, leverage=1.0) == 0.0
    assert notional_ceiling_pct(venue="okx", equity_usd=100.0, leverage=0.0) == 0.0


def test_notional_hard_cap_usd_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_NOTIONAL_CAP_CAPITAL_USD", "12345.0")
    assert notional_hard_cap_usd("capital") == pytest.approx(12345.0)
    assert notional_hard_cap_usd("okx") != pytest.approx(12345.0)


# ---------------------------------------------------------------------------
# (c) capital notional = pct × 51,000 × leverage (P1-7 venue-aware equity).
# ---------------------------------------------------------------------------


def test_equity_usd_for_venue_dispatches_per_venue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLARIS_EQUITY_USD", raising=False)
    assert equity_usd_for_venue("okx") == pytest.approx(OKX_DEMO_STARTING_EQUITY_USD)
    assert equity_usd_for_venue("capital") == pytest.approx(CAPITAL_DEMO_STARTING_EQUITY_USD)
    # Unknown venue falls back to OKX (never a silent zero).
    assert equity_usd_for_venue("fakevenue") == pytest.approx(OKX_DEMO_STARTING_EQUITY_USD)


def test_capital_notional_uses_capital_equity_not_okx(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OKX and Capital are both $100k by default (uniform virtual seed, Jin
    # 2026-07-07) — force them apart via env so this stays a real dispatch
    # test (Capital sizes off ITS OWN equity, not a coincidentally-equal
    # OKX figure) rather than a vacuous "not equal" check.
    monkeypatch.delenv("POLARIS_EQUITY_USD", raising=False)
    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_OKX", "79000")
    leverage = 20.0  # Capital index/commodity fallback leverage tier
    capital_equity = equity_usd_for_venue("capital")
    assert capital_equity == pytest.approx(CAPITAL_DEMO_STARTING_EQUITY_USD)
    sized = compute_size(
        memdb,
        intent=_intent(venue="capital", signal_strength=1.0, leverage=leverage),
        risk_state=_risk_state(venue="capital"),
        portfolio=_portfolio(capital_equity, track="B"),
        now_ts=NOW,
    )
    assert sized.final_notional_usd == pytest.approx(
        sized.final_risk_pct * CAPITAL_DEMO_STARTING_EQUITY_USD * leverage
    )
    # Sanity: NOT sized against the (deliberately different, env-forced) OKX figure.
    assert sized.final_notional_usd != pytest.approx(
        sized.final_risk_pct * 79_000.0 * leverage
    )


# ---------------------------------------------------------------------------
# End-to-end pipeline: ``run_pipeline_for_signal`` no longer clamps the fill
# notional to $5,000 and threads venue-aware equity into ``build_sizer_payload``.
# ---------------------------------------------------------------------------

PIPE_NOW = 1_780_000_000


def _pipe_sig(*, symbol: str = "BTC-USDT") -> RawSignal:
    return RawSignal(
        signal_id="sig-p03", strategy_id="tsmom", symbol=symbol, side="long",
        strength=0.9, sizing_hint=0.5, ttl_bars=24, thesis_tag="t",
        correlation_group="spot_cross_sectional_momo", created_at_bar=5000,
    )


def _pipe_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        c.execute(stmt)
    return c


def _patch_pipeline_sized(
    monkeypatch: pytest.MonkeyPatch, *, final_notional_usd: float,
) -> None:
    class _FakeOrch:
        def __init__(self, **_: object) -> None:
            pass

        async def run(self, _ctx: object, *, start_gate: int) -> list[GateResult]:
            return [
                GateResult(
                    decision=GateDecision.SIZED, next_gate=None,
                    payload={"sized": {"final_notional_usd": final_notional_usd}},
                )
            ]

    async def _fake_monitor(_ctx: object, *, client: object) -> GateResult:
        return GateResult(decision=GateDecision.HOLD, next_gate=None)

    async def _fake_exit(_ctx: object, *, client: object) -> GateResult:
        return GateResult(decision=GateDecision.PROCEED, next_gate=None)

    monkeypatch.setattr(run_signal_mod, "GateOrchestrator", _FakeOrch)
    monkeypatch.setattr(run_signal_mod, "position_monitor_gate", _fake_monitor)
    monkeypatch.setattr(run_signal_mod, "adaptive_exit_gate", _fake_exit)
    monkeypatch.setattr(run_signal_mod, "log_gate_event", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_reserve_and_submit_notional_not_clamped_to_5000(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A T4-sized $23,700 notional (e.g. cont=1.50 large-equity signal) must
    reach ``reserve_and_submit`` UNCLAMPED — the old clamp silently collapsed
    anything above $5,000 to exactly $5,000."""
    conn = _pipe_conn()
    _patch_pipeline_sized(monkeypatch, final_notional_usd=23_700.0)
    from polaris.scripts.production_paper_loop import ProdLoopState

    state = ProdLoopState()
    captured: dict[str, Any] = {}

    async def _reserve_and_submit(**kwargs: object) -> None:
        captured["notional_usd"] = kwargs["notional_usd"]
        return None

    await run_signal_mod.run_pipeline_for_signal(
        conn=conn, haiku=None, state=state, strategy=SpotDonchianStrategy(),
        sig=_pipe_sig(), venue="okx", symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", regime="bull_trend",
        bars_atr_pct=0.01, last_price=100.0, universe_rows=[], now_ts=PIPE_NOW,
        reserve_and_submit=_reserve_and_submit, phase="P0",
    )
    assert captured["notional_usd"] == pytest.approx(23_700.0)


@pytest.mark.asyncio
async def test_pipeline_threads_venue_aware_equity_into_sizer_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Capital-venue signal must size against the Capital demo balance,
    not the OKX figure (P1-7). OKX and Capital are both $100k by default
    (uniform virtual seed, Jin 2026-07-07) — force OKX apart via env so
    this stays a real dispatch test rather than a vacuous "not equal"
    check on two coincidentally-equal constants."""
    conn = _pipe_conn()
    monkeypatch.delenv("POLARIS_EQUITY_USD", raising=False)
    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_OKX", "79000")
    captured: dict[str, Any] = {}
    real_build_sizer_payload = run_signal_mod.build_sizer_payload

    def _capturing_build_sizer_payload(**kwargs: Any) -> dict[str, Any]:
        captured["equity_usd"] = kwargs["equity_usd"]
        return real_build_sizer_payload(**kwargs)

    monkeypatch.setattr(
        run_signal_mod, "build_sizer_payload", _capturing_build_sizer_payload,
    )
    _patch_pipeline_sized(monkeypatch, final_notional_usd=1_000.0)
    from polaris.scripts.production_paper_loop import ProdLoopState

    state = ProdLoopState()

    async def _reserve_and_submit(**_: object) -> None:
        return None

    await run_signal_mod.run_pipeline_for_signal(
        conn=conn, haiku=None, state=state, strategy=SpotDonchianStrategy(),
        sig=_pipe_sig(symbol="EURUSD"), venue="capital", symbol="EURUSD",
        asset_class="forex", underlying_group_id="forex:EURUSD",
        regime="bull_trend", bars_atr_pct=0.01, last_price=1.10,
        universe_rows=[], now_ts=PIPE_NOW, reserve_and_submit=_reserve_and_submit,
        phase="P0",
    )
    assert captured["equity_usd"] == pytest.approx(CAPITAL_DEMO_STARTING_EQUITY_USD)
    assert captured["equity_usd"] != pytest.approx(79_000.0)
