"""3-tier benchmark gate TDD — relative / risk-adjusted / statistical + CIs.

Spec §3-tier: pass = bot real-fee-net Sharpe spread > 0 vs ALL baselines, but
when the lower CI bound is not significant the verdict is "edge present, CI
wide" (NOT pass) — honest framing, never a time gate. The gate is pure: it
consumes a ReplayResult + the same-clock baseline curves and returns a verdict
dict with point estimates + CIs.
"""

from __future__ import annotations

from polaris.core.benchmark.baselines import BaselineCurve
from polaris.core.benchmark.gate import evaluate_gate
from polaris.core.replay.models import ReplayResult, ReplayTrade


def _trade(
    net: float, *, entry_ts: int, exit_ts: int, strategy: str = "tsmom"
) -> ReplayTrade:
    return ReplayTrade(
        entry_ts=entry_ts, exit_ts=exit_ts, symbol="BTC-USDT", strategy=strategy,
        regime="bull_trend", side="long", entry_price=100.0, exit_price=101.0,
        notional=1000.0, gross_pnl_usd=net + 1.0, rt_fee_usd=1.0, net_pnl_usd=net,
        pnl_r=net / 50.0, exit_reason="atr_trail_stop",
    )


def _result(nets: list[float], *, start_ts: int = 1_700_000_000, equity: float = 10_000.0) -> ReplayResult:
    trades = []
    eq = equity
    curve = [(start_ts, equity)]
    for i, net in enumerate(nets):
        ts = start_ts + (i + 1) * 3600
        trades.append(_trade(net, entry_ts=start_ts + i * 3600, exit_ts=ts))
        eq += net
        curve.append((ts, eq))
    # per-trade returns for Sharpe (net / equity_before).
    from polaris.core.replay.equity_curve import summarize_metrics
    metrics = summarize_metrics(trades=trades, curve=curve, starting_equity=equity)
    return ReplayResult(
        trades=tuple(trades), equity_curve_real_fee_net=tuple(curve), metrics=metrics
    )


def _result_multi(
    nets_by_strategy: dict[str, list[float]],
    *,
    start_ts: int = 1_700_000_000,
    equity: float = 10_000.0,
) -> ReplayResult:
    """Replay result whose trades span >1 strategy -> >1 deflation trial."""
    trades = []
    eq = equity
    curve = [(start_ts, equity)]
    i = 0
    for strategy, nets in nets_by_strategy.items():
        for net in nets:
            ts = start_ts + (i + 1) * 3600
            trades.append(
                _trade(net, entry_ts=start_ts + i * 3600, exit_ts=ts, strategy=strategy)
            )
            eq += net
            curve.append((ts, eq))
            i += 1
    trades.sort(key=lambda t: (t.exit_ts, t.entry_ts))
    from polaris.core.replay.equity_curve import summarize_metrics
    metrics = summarize_metrics(trades=trades, curve=curve, starting_equity=equity)
    return ReplayResult(
        trades=tuple(trades), equity_curve_real_fee_net=tuple(curve), metrics=metrics
    )


def _baseline(name: str, rets: list[float]) -> BaselineCurve:
    return BaselineCurve(name=name, equity_curve=((0, 10_000.0),), returns=tuple(rets), total_fee_usd=5.0)


# --- structure --------------------------------------------------------------
def test_gate_returns_three_tiers() -> None:
    res = _result([60.0, -20.0, 80.0, 30.0, -10.0, 50.0])
    baselines = {"bh_btc": _baseline("bh_btc", [0.001, -0.001, 0.0005, 0.0002, -0.0003, 0.0001])}
    gate = evaluate_gate(replay=res, baselines=baselines, trials=1)
    assert set(gate.tiers) == {"relative", "risk_adjusted", "statistical"}


def test_strong_bot_beats_weak_baselines_passes_relative() -> None:
    # Bot strongly positive, baseline flat/negative -> spread > 0 on all.
    res = _result([100.0, 90.0, 110.0, 95.0, 105.0, 88.0, 120.0, 92.0])
    baselines = {
        "bh_btc": _baseline("bh_btc", [-0.001] * 8),
        "tsmom": _baseline("tsmom", [0.0] * 8),
    }
    gate = evaluate_gate(replay=res, baselines=baselines, trials=1)
    assert gate.tiers["relative"].passed
    # Every spread strictly positive.
    assert all(v > 0.0 for v in gate.sharpe_spreads.values())


def test_losing_bot_fails_relative() -> None:
    res = _result([-50.0, -60.0, -40.0, -55.0, -45.0, -70.0])
    baselines = {"bh_btc": _baseline("bh_btc", [0.002] * 6)}
    gate = evaluate_gate(replay=res, baselines=baselines, trials=1)
    assert not gate.tiers["relative"].passed
    assert not gate.passed


def test_wide_ci_is_edge_present_not_pass() -> None:
    # Positive point estimate but tiny / noisy sample -> LCB not significant.
    res = _result([5.0, -4.0, 6.0, -5.0])  # near-zero mean, high variance, n=4
    baselines = {"bh_btc": _baseline("bh_btc", [0.0] * 4)}
    gate = evaluate_gate(replay=res, baselines=baselines, trials=1)
    stat = gate.tiers["statistical"]
    # Honest framing: not a pass, labelled as CI wide.
    assert not gate.passed
    assert "edge present" in stat.note.lower() or not stat.passed


def test_statistical_tier_has_psr_and_deflated() -> None:
    res = _result([40.0, 35.0, 50.0, 30.0, 45.0, 38.0, 42.0, 48.0, 33.0, 41.0])
    baselines = {"bh_btc": _baseline("bh_btc", [0.0] * 10)}
    gate = evaluate_gate(replay=res, baselines=baselines, trials=20)
    assert 0.0 <= gate.psr <= 1.0
    assert 0.0 <= gate.deflated_sharpe <= 1.0
    # More trials deflate: deflated <= psr.
    assert gate.deflated_sharpe <= gate.psr + 1e-9


def test_ci_present_on_net_pnl() -> None:
    res = _result([60.0, -20.0, 80.0, 30.0, -10.0, 50.0])
    baselines = {"bh_btc": _baseline("bh_btc", [0.0] * 6)}
    gate = evaluate_gate(replay=res, baselines=baselines, trials=1)
    lo, hi = gate.net_pnl_r_ci
    assert lo <= gate.net_pnl_r_mean <= hi


def test_empty_replay_does_not_pass() -> None:
    res = ReplayResult(trades=(), equity_curve_real_fee_net=((0, 10_000.0),), metrics={})
    gate = evaluate_gate(replay=res, baselines={}, trials=1)
    assert not gate.passed


# --- FIX 2: deflated-Sharpe is actually wired (DSR < PSR on real returns) ----
def test_multi_strategy_deflation_lowers_dsr_below_psr() -> None:
    """With >1 strategy group (the BLdP trial population derived from trades),
    the deflated Sharpe MUST sit strictly below the raw PSR — proving the gate
    feeds the cross-trial Sharpe variance (not the single-trial per-trade return
    variance, which made deflation a no-op). Uses the gate's OWN bot returns, so
    this exercises the real wiring, not a synthetic fixed-variance call."""
    # Three strategies with clearly different per-trade Sharpes -> Var(SR) > 0.
    res = _result_multi({
        "tsmom": [60.0, 55.0, 70.0, 50.0, 65.0],         # strong, steady
        "rsi_bb_pullback": [40.0, -30.0, 50.0, -20.0, 35.0],  # noisier
        "volume_burst": [20.0, 10.0, 25.0, 5.0, 18.0],   # weak, steady
    })
    baselines = {"bh_btc": _baseline("bh_btc", [0.0] * 15)}
    gate = evaluate_gate(replay=res, baselines=baselines, trials=1)
    # Deflation actually fired (T>1 trial population from the trades).
    assert gate.tiers["statistical"].detail["deflation_trials"] > 1.0
    assert gate.tiers["statistical"].detail["sr_variance"] > 0.0
    # The fix: deflation strictly LOWERS the statistical-tier Sharpe probability.
    assert gate.deflated_sharpe < gate.psr
    assert 0.0 <= gate.deflated_sharpe <= 1.0


def test_single_strategy_is_undeflated_not_a_noop_deflation() -> None:
    """One strategy group = no multiple-testing search to correct: the gate must
    honestly report DSR == PSR labelled 'un-deflated(single trial)', NOT a
    deflation driven by the single-trial per-trade return variance."""
    res = _result_multi({"tsmom": [40.0, 35.0, 50.0, 30.0, 45.0, 38.0]})
    baselines = {"bh_btc": _baseline("bh_btc", [0.0] * 6)}
    gate = evaluate_gate(replay=res, baselines=baselines, trials=99)  # arg ignored
    assert gate.tiers["statistical"].detail["deflation_trials"] <= 1.0
    assert gate.deflated_sharpe == gate.psr  # un-deflated
    assert "un-deflated" in gate.tiers["statistical"].note
