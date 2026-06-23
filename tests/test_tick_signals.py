"""P5 tick signals — synthetic windows fire the right signal + regime gating.

Each test builds a tick window that *encodes a clear microstructure event*
(a burst / a bid-pressure imbalance / a stretched overshoot with exhausting
flow) and asserts the matching signal fires with the right side + a monotone
conviction — and that ``regime_gate`` disables the wrong signals for a regime.

Spec SSOT: .claude/plans/p5_tick_decision_engine_2026-06-03.md §"신규 모듈".
"""

from __future__ import annotations

from polaris.core.ticks.config import (
    TickEngineConfig,
    regime_aware_confirm_cfg,
)
from polaris.core.ticks.features import compute_tick_features
from polaris.core.ticks.regime_gate import active_signals, direction_bias, normalize_regime
from polaris.core.ticks.signals import (
    TickIntent,
    burst_rider,
    flow_pressure,
    micro_reversion,
)
from polaris.core.ticks.types import TickSample

CFG = TickEngineConfig()
# Realistic epoch-seconds "now" (the real OKX/Capital feed stamps ticks in epoch
# seconds). Windows below end a few seconds before NOW so the newest tick is
# fresh (age within fresh_sec) and the per-tick Δt are the real 1s spacing the
# EWMA decays against.
NOW = 1_780_451_113.0
VENUE = "okx"
SYMBOL = "BTC-USDT"


def _tick(
    ts: int,
    mid: float,
    *,
    bid_size: float = 10.0,
    ask_size: float = 10.0,
    last_trade_price: float | None = None,
    last_trade_size: float = 1.0,
    spread: float = 0.02,
) -> TickSample:
    half = spread / 2.0
    bid = mid - half
    ask = mid + half
    spread_bps = (ask - bid) / mid * 1e4 if mid > 0 else 0.0
    return TickSample(
        ts=ts,
        bid=bid,
        ask=ask,
        mid=mid,
        bid_size=bid_size,
        ask_size=ask_size,
        last_trade_price=last_trade_price if last_trade_price is not None else mid,
        last_trade_size=last_trade_size,
        spread_bps=spread_bps,
    )


# Window origin: 20 ticks 1s apart end at BASE+19, i.e. ~6s before NOW → fresh.
_BASE = int(NOW - 25)


# ---------------------------------------------------------------------------
# burst_rider — a calm baseline then a sharp up-tick with buy aggressor flow.
# ---------------------------------------------------------------------------


def _burst_window(direction: int, *, final_jump: float = 0.30) -> list[TickSample]:
    """16 near-flat ticks (tiny noise) then a ramp into a sharp final spike.

    Ticks are 1s apart (epoch seconds). ``direction`` +1 = up burst
    (buyer-aggressor), -1 = down burst (seller). Trades print on the aggressor
    side so aggr_flow agrees with the burst. The last step is the sharp velocity
    spike (``final_jump``) that stands out as a burst_z z-score above the trailing
    per-step speed baseline; a larger ``final_jump`` → strictly larger burst_z
    (so the monotone-conviction test is honest, not z-score scale-invariant).
    """
    ticks: list[TickSample] = []
    mid = 100.0
    # Calm baseline: alternating ±0.001 noise (small, near-zero velocity).
    for i in range(16):
        mid += 0.001 if i % 2 == 0 else -0.001
        # Tiny trades on the same passive level (no strong aggressor).
        ticks.append(_tick(_BASE + i, mid, last_trade_price=mid, last_trade_size=0.5))
    # The burst: a short ramp then a sharp final-step velocity spike.
    jumps = [0.08, 0.10, 0.12, final_jump]
    for j, step in enumerate(jumps):
        mid += direction * step
        trade_px = mid + direction * 0.05  # aggressor lifts/hits in the burst dir
        ticks.append(
            _tick(
                _BASE + 16 + j,
                mid,
                last_trade_price=trade_px,
                last_trade_size=8.0,
            )
        )
    return ticks


def test_burst_rider_fires_long_on_up_burst() -> None:
    feat = compute_tick_features(_burst_window(+1), NOW, CFG)
    assert feat.burst_z is not None and feat.burst_z > CFG.theta_burst
    intent = burst_rider(feat, "bull_trend", venue=VENUE, symbol=SYMBOL, ref_price=101.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "long"
    assert intent.signal_id == "burst_rider"
    assert intent.signal_family == "momentum"
    assert 0.0 < intent.conviction <= CFG.conviction_cap
    assert intent.venue == VENUE and intent.symbol == SYMBOL and intent.ref_price == 101.0


def test_burst_rider_fires_short_on_down_burst() -> None:
    feat = compute_tick_features(_burst_window(-1), NOW, CFG)
    intent = burst_rider(feat, "bear_trend", venue=VENUE, symbol=SYMBOL, ref_price=99.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "short"


def test_burst_rider_blocked_by_wide_spread() -> None:
    # Same burst but the latest spread is far above θ_s → no entry crossing it.
    w = _burst_window(+1)
    last = w[-1]
    wide = TickSample(
        ts=last.ts,
        bid=last.mid - 1.0,
        ask=last.mid + 1.0,
        mid=last.mid,
        bid_size=last.bid_size,
        ask_size=last.ask_size,
        last_trade_price=last.last_trade_price,
        last_trade_size=last.last_trade_size,
        spread_bps=(2.0 / last.mid) * 1e4,  # ~200 bps ≫ θ_s
    )
    w[-1] = wide
    feat = compute_tick_features(w, NOW, CFG)
    assert feat.spread_bps is not None and feat.spread_bps >= CFG.theta_spread
    assert burst_rider(feat, "bull_trend", venue=VENUE, symbol=SYMBOL, ref_price=101.0, cfg=CFG) is None


def test_burst_rider_conviction_monotone_in_burst() -> None:
    # A bigger final-spike burst → strictly higher burst_z → higher conviction.
    small = compute_tick_features(_burst_window(+1, final_jump=0.30), NOW, CFG)
    big = compute_tick_features(_burst_window(+1, final_jump=0.60), NOW, CFG)
    assert small.burst_z is not None and big.burst_z is not None
    assert big.burst_z > small.burst_z  # a real, not scale-invariant, increase
    i_small = burst_rider(small, "trend", venue=VENUE, symbol=SYMBOL, ref_price=1.0, cfg=CFG)
    i_big = burst_rider(big, "trend", venue=VENUE, symbol=SYMBOL, ref_price=1.0, cfg=CFG)
    assert i_small is not None and i_big is not None
    assert i_big.conviction >= i_small.conviction


# ---------------------------------------------------------------------------
# flow_pressure — a sustained bid-heavy book with buyer aggressor flow.
# ---------------------------------------------------------------------------


def _imbalance_window(side_sign: int) -> list[TickSample]:
    """20 ticks with a persistent book imbalance + agreeing aggressor trades.

    ``side_sign`` +1 = bid-heavy (buy pressure), -1 = ask-heavy (sell pressure).
    """
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(20):
        mid += side_sign * 0.002  # gentle drift in the pressure direction
        if side_sign > 0:
            bid_size, ask_size = 120.0, 8.0
            trade_px = mid + 0.05  # buyer lifts the offer
        else:
            bid_size, ask_size = 8.0, 120.0
            trade_px = mid - 0.05  # seller hits the bid
        ticks.append(
            _tick(
                _BASE + i,
                mid,
                bid_size=bid_size,
                ask_size=ask_size,
                last_trade_price=trade_px,
                last_trade_size=4.0,
            )
        )
    return ticks


def test_flow_pressure_fires_long_on_bid_imbalance() -> None:
    feat = compute_tick_features(_imbalance_window(+1), NOW, CFG)
    assert feat.ofi is not None and feat.ofi > CFG.theta_ofi
    intent = flow_pressure(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "long"
    assert intent.signal_id == "flow_pressure"
    assert intent.signal_family == "momentum"
    assert 0.0 < intent.conviction <= CFG.conviction_cap


def test_flow_pressure_fires_short_on_ask_imbalance() -> None:
    feat = compute_tick_features(_imbalance_window(-1), NOW, CFG)
    intent = flow_pressure(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "short"


def test_flow_pressure_silent_on_balanced_book() -> None:
    # Symmetric book, trades at mid → |ofi| ≈ 0 → no signal.
    balanced = [
        _tick(_BASE + i, 100.0, bid_size=50.0, ask_size=50.0, last_trade_price=100.0)
        for i in range(20)
    ]
    feat = compute_tick_features(balanced, NOW, CFG)
    assert flow_pressure(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG) is None


# ---------------------------------------------------------------------------
# Seam2 — regime-aware ENTRY confirmation (bad fit tightens; strong still fires)
# ---------------------------------------------------------------------------


def _decaying_imbalance_window(tail_bid: float, tail_ask: float) -> list[TickSample]:
    """A strong early bid imbalance that DECAYS to ``(tail_bid, tail_ask)`` over
    the confirm tail — the exhaustion shape. The EWMA ofi (time-weighted) stays
    above θ_o so flow_pressure ARMS, but the tail follow-through is only marginal:
    it clears the BASE confirm floor yet fails the chop-TIGHTENED floor."""
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(20):
        mid += 0.002
        if i < 18:
            bid_size, ask_size = 140.0, 4.0  # strong early → pumps the EWMA ofi
        else:
            bid_size, ask_size = tail_bid, tail_ask  # decaying tail
        ticks.append(
            _tick(_BASE + i, mid, bid_size=bid_size, ask_size=ask_size,
                  last_trade_price=mid + 0.05, last_trade_size=4.0)
        )
    return ticks


def test_flow_pressure_badfit_tightens_confirmation() -> None:
    """A MARGINAL follow-through fires under a neutral regime but is DELAYED in
    chop (momentum × chop = -1 raises the confirm bar). This is TIMING precision,
    not a veto — the same imbalance simply re-arms on a later confirmed tick."""
    window = _decaying_imbalance_window(tail_bid=66.0, tail_ask=34.0)
    base_cfg = regime_aware_confirm_cfg(CFG, "unknown")  # neutral → unchanged
    chop_cfg = regime_aware_confirm_cfg(CFG, "chop")  # bad momentum fit → tighter
    feat_base = compute_tick_features(window, NOW, base_cfg)
    feat_chop = compute_tick_features(window, NOW, chop_cfg)
    # The imbalance ARMS (EWMA ofi past θ_o) in both — the bar is on confirmation.
    assert feat_base.ofi is not None and feat_base.ofi > CFG.theta_ofi
    # Neutral regime: the marginal follow-through clears → fires.
    assert flow_pressure(feat_base, "unknown", venue=VENUE, symbol=SYMBOL,
                         ref_price=100.0, cfg=CFG) is not None
    # Chop (bad fit): the raised bar DELAYS this marginal spike (None this tick).
    assert flow_pressure(feat_chop, "chop", venue=VENUE, symbol=SYMBOL,
                         ref_price=100.0, cfg=CFG) is None


def test_flow_pressure_strong_still_fires_in_badfit_regime() -> None:
    """flow_not_block: a GENUINE strong imbalance clears even the chop-tightened
    bar and fires BIDIRECTIONALLY — the bad fit shapes timing, never blocks."""
    chop_cfg = regime_aware_confirm_cfg(CFG, "chop")
    for sign, side in ((+1, "long"), (-1, "short")):
        feat = compute_tick_features(_imbalance_window(sign), NOW, chop_cfg)
        intent = flow_pressure(feat, "chop", venue=VENUE, symbol=SYMBOL,
                               ref_price=100.0, cfg=CFG)
        assert isinstance(intent, TickIntent)
        assert intent.side == side


def test_flow_pressure_goodfit_relaxes_confirmation() -> None:
    """A GOOD momentum fit (bull_trend, +1) LOWERS the confirm bar — the same
    marginal follow-through that chop delayed fires sooner (bidirectional lean)."""
    window = _decaying_imbalance_window(tail_bid=66.0, tail_ask=34.0)
    good_cfg = regime_aware_confirm_cfg(CFG, "bull_trend")
    assert good_cfg.confirm_ofi_frac < CFG.confirm_ofi_frac  # bar relaxed
    feat_good = compute_tick_features(window, NOW, good_cfg)
    assert flow_pressure(feat_good, "bull_trend", venue=VENUE, symbol=SYMBOL,
                         ref_price=100.0, cfg=CFG) is not None


# ---------------------------------------------------------------------------
# flow_pressure RE-AIM (honest fee-net retune): the OFI firing bar is raised
# 0.20 → 0.32 → 0.40 to the cost-clearing fat tail (at 0.32 net/trade was still
# fee-negative: gross 0.861 < fee 1.143). The signal must:
#   (1) STILL fire on a FAT imbalance (|ofi| past the new 0.40 bar) — long AND
#       short (bidirectional preserved — NOT a defensive dampen / one-way block),
#   (2) NOT fire on the sub-cost NOISE band (0.20 < |ofi| < 0.40) the old bar
#       would have over-traded into a net loss.
# This is a RE-AIM that keeps firing on every real large imbalance both ways; it
# is never a size-cut, an entry-block, or a directional fade.
# ---------------------------------------------------------------------------


def _ofi_window(side_sign: int, *, bid_size: float, ask_size: float) -> list[TickSample]:
    """20 ticks with a fixed book imbalance (bid/ask size) + agreeing aggressor.

    ``side_sign`` +1 = the book is supplied bid-heavy (buy pressure), -1 =
    ask-heavy. ``bid_size``/``ask_size`` are passed as the larger-on-the-pressure
    side already; ``side_sign`` only sets the drift + aggressor print direction so
    aggr_flow agrees with the imbalance (real pressure, not passive size).
    """
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(20):
        mid += side_sign * 0.002
        trade_px = mid + side_sign * 0.05  # aggressor agrees with the imbalance
        ticks.append(
            _tick(
                _BASE + i, mid,
                bid_size=bid_size, ask_size=ask_size,
                last_trade_price=trade_px, last_trade_size=4.0,
            )
        )
    return ticks


def test_flow_pressure_fires_on_fat_imbalance_above_new_bar() -> None:
    # bid=80/ask=20 → |ofi| ≈ 0.60 > θ_o=0.40 (cost-clearing fat tail) → fires.
    feat = compute_tick_features(_ofi_window(+1, bid_size=80.0, ask_size=20.0), NOW, CFG)
    assert feat.ofi is not None and feat.ofi > CFG.theta_ofi
    intent = flow_pressure(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "long"
    assert 0.0 < intent.conviction <= CFG.conviction_cap


def test_flow_pressure_still_fires_short_on_opposite_fat_imbalance() -> None:
    # Bidirectional preserved: the mirror ask-heavy fat imbalance STILL fires a
    # SHORT (the re-aim is symmetric — never a one-way / fade block).
    feat = compute_tick_features(_ofi_window(-1, bid_size=20.0, ask_size=80.0), NOW, CFG)
    assert feat.ofi is not None and feat.ofi < -CFG.theta_ofi
    intent = flow_pressure(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "short"
    assert 0.0 < intent.conviction <= CFG.conviction_cap


def test_flow_pressure_silent_on_sub_cost_noise_band() -> None:
    # bid=68/ask=32 → |ofi| ≈ 0.36: inside the 0.32–0.40 band the OLD 0.32 bar
    # would have fired into the net loss (gross 0.861 < fee 1.143), but BELOW the
    # new 0.40 bar → must NOT fire. Both directions.
    long_band = compute_tick_features(_ofi_window(+1, bid_size=68.0, ask_size=32.0), NOW, CFG)
    short_band = compute_tick_features(_ofi_window(-1, bid_size=32.0, ask_size=68.0), NOW, CFG)
    assert long_band.ofi is not None and 0.32 < long_band.ofi < CFG.theta_ofi
    assert short_band.ofi is not None and -CFG.theta_ofi < short_band.ofi < -0.32
    assert flow_pressure(long_band, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG) is None
    assert flow_pressure(short_band, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG) is None


def test_flow_pressure_theta_ofi_is_env_tunable() -> None:
    # The bar is a NAMED env-tunable so the honest-slate re-measure can re-aim it
    # without a redeploy. A lower env θ_o re-arms the sub-cost band; a default
    # (unset) keeps the re-aimed 0.40 coded default.
    import os

    from polaris.core.ticks.config import TickEngineConfig

    assert TickEngineConfig().theta_ofi == 0.40
    prev = os.environ.get("POLARIS_TICK_THETA_OFI")
    os.environ["POLARIS_TICK_THETA_OFI"] = "0.20"
    try:
        assert TickEngineConfig().theta_ofi == 0.20
    finally:
        if prev is None:
            del os.environ["POLARIS_TICK_THETA_OFI"]
        else:
            os.environ["POLARIS_TICK_THETA_OFI"] = prev


# ---------------------------------------------------------------------------
# flow_pressure ENTRY follow-through confirmation ([[flow_pressure_calibration_
# ai_2026-06-23]]): the raw-spike entry was buying the TOP of an OFI-positive
# spike that immediately reversed (59% never reached +0.2R — exhaustion mistaken
# for continuation). The signal now requires POST-SPIKE follow-through:
#   (1) a one-tick / exhausting spike (book bid-heavy but the PRICE reverses /
#       bid evaporates) → flow_confirmed False → NO entry (delay, not veto),
#   (2) a genuine sustained follow-through → flow_confirmed True → ENTRY.
# This is TIMING precision — a delayed entry, never a size-cut / block.
# ---------------------------------------------------------------------------


def _exhaustion_spike_window(side_sign: int) -> list[TickSample]:
    """A bid-heavy (long) / ask-heavy (short) book whose PRICE immediately reverses.

    The classic 59% top-buy: the order book stays imbalanced (|ofi| EWMA clears
    θ_o) and the aggressor agrees, BUT the spike exhausts — the latest mids
    reverse back THROUGH the spike midpoint (microprice fails) and the near-touch
    size evaporates on the final ticks. Follow-through must read this as
    ``flow_confirmed == False`` → flow_pressure returns None (no top-buy).
    """
    ticks: list[TickSample] = []
    mid = 100.0
    # Spike up (long) / down (short) for the first stretch — book imbalanced, the
    # aggressor lifting/hitting, price runs in the imbalance direction.
    for i in range(16):
        mid += side_sign * 0.03
        bid_size, ask_size = (120.0, 8.0) if side_sign > 0 else (8.0, 120.0)
        trade_px = mid + side_sign * 0.05
        ticks.append(
            _tick(_BASE + i, mid, bid_size=bid_size, ask_size=ask_size,
                  last_trade_price=trade_px, last_trade_size=4.0)
        )
    # Exhaustion tail: price REVERSES hard back through the spike midpoint and the
    # near-touch size collapses (bid evaporation on a long), while the book sign
    # still reads imbalanced enough to keep |ofi| EWMA > θ_o.
    for j in range(4):
        mid -= side_sign * 0.10  # reverse against the spike
        bid_size, ask_size = (60.0, 8.0) if side_sign > 0 else (8.0, 60.0)
        trade_px = mid - side_sign * 0.05  # aggressor now opposing
        ticks.append(
            _tick(_BASE + 16 + j, mid, bid_size=bid_size, ask_size=ask_size,
                  last_trade_price=trade_px, last_trade_size=4.0)
        )
    return ticks


def test_flow_pressure_no_entry_on_exhausting_spike() -> None:
    # One-tick / exhausting OFI spike that reverses → follow-through fails → the
    # entry is DELAYED (None), not a top-buy. Both directions.
    up = compute_tick_features(_exhaustion_spike_window(+1), NOW, CFG)
    assert up.flow_confirmed is False
    assert flow_pressure(up, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG) is None
    down = compute_tick_features(_exhaustion_spike_window(-1), NOW, CFG)
    assert down.flow_confirmed is False
    assert flow_pressure(down, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG) is None


def test_flow_pressure_enters_on_genuine_followthrough() -> None:
    # A sustained imbalance whose price FOLLOWS THROUGH (OFI stays elevated, bid
    # follows up, microprice holds, spread stable) → flow_confirmed True → ENTRY.
    feat = compute_tick_features(_imbalance_window(+1), NOW, CFG)
    assert feat.flow_confirmed is True
    intent = flow_pressure(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "long"


# ---------------------------------------------------------------------------
# micro_reversion — a sharp overshoot up, then flow turns over (exhaustion).
# ---------------------------------------------------------------------------


def _overshoot_window(direction: int) -> list[TickSample]:
    """A long calm base then a ramp into a sharp final ``direction`` spike that
    flow no longer supports (last trades print against the spike = exhaustion).

    Ticks are 1s apart (epoch seconds). A uniform spike would be z-score
    scale-invariant (mid-EWMA and std_mid scale together → overshoot_z pinned ≈
    1.66); the ramp-then-sharp-final-step stretches the latest mid well past its
    recent 3s EWMA anchor → overshoot_z ≈ 2.2 ≫ θ_r, a real overshoot.
    """
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(16):
        mid += 0.0005 if i % 2 == 0 else -0.0005  # near-flat anchor
        ticks.append(_tick(_BASE + i, mid, last_trade_price=mid, last_trade_size=0.5))
    # The overshoot: a short ramp then a sharp final step away from the EWMA
    # anchor, with the aggressor flow OPPOSING it throughout (the push is spent).
    steps = [0.10, 0.14, 0.18, 0.34]
    for j, step in enumerate(steps):
        mid += direction * step
        # Trade prints AGAINST the spike direction → exhausting flow.
        trade_px = mid - direction * 0.05
        ticks.append(
            _tick(
                _BASE + 16 + j,
                mid,
                last_trade_price=trade_px,
                last_trade_size=6.0,
            )
        )
    return ticks


def test_micro_reversion_fades_an_up_overshoot_short() -> None:
    feat = compute_tick_features(_overshoot_window(+1), NOW, CFG)
    assert feat.overshoot_z is not None and feat.overshoot_z > CFG.theta_revert
    assert feat.aggr_flow is not None and feat.aggr_flow <= 0.0  # flow opposes
    intent = micro_reversion(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=101.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "short"  # fade the up overshoot
    assert intent.signal_id == "micro_reversion"
    assert intent.signal_family == "reversion"
    assert 0.0 < intent.conviction <= CFG.conviction_cap


def test_micro_reversion_fades_a_down_overshoot_long() -> None:
    feat = compute_tick_features(_overshoot_window(-1), NOW, CFG)
    intent = micro_reversion(feat, "crisis", venue=VENUE, symbol=SYMBOL, ref_price=99.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "long"  # fade the down overshoot


def test_micro_reversion_silent_when_flow_still_supports_overshoot() -> None:
    # Same up overshoot, but the final trades keep LIFTING (flow still buying)
    # → not exhaustion → reversion must not fire.
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(16):
        mid += 0.0005 if i % 2 == 0 else -0.0005
        ticks.append(_tick(_BASE + i, mid, last_trade_size=0.5))
    steps = [0.10, 0.14, 0.18, 0.34]
    for j, step in enumerate(steps):
        mid += step
        ticks.append(
            _tick(_BASE + 16 + j, mid, last_trade_price=mid + 0.05, last_trade_size=6.0)
        )
    feat = compute_tick_features(ticks, NOW, CFG)
    assert micro_reversion(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=101.0, cfg=CFG) is None


# ---------------------------------------------------------------------------
# micro_reversion RE-AIM (honest fee-net retune): the overshoot firing bar is
# raised 1.5 → 2.0 to the cost-clearing fat tail. micro_reversion's gross edge
# is POSITIVE (+$19.93) but net is NEGATIVE (−$13.54) over 79 closes — the SAME
# fee>edge pattern as flow_pressure: the old 1.5 bar fired on overshoots too
# shallow to clear the ~20bps round-trip cost. The signal must:
#   (1) STILL fade a FAT overshoot (|overshoot_z| past the new 2.0 bar) — up AND
#       down (bidirectional preserved — NOT a defensive dampen / one-way block),
#   (2) NOT fire on the sub-cost NOISE band (1.5 < |overshoot_z| < 2.0) the old
#       bar would have over-traded into a net loss.
# This is a RE-AIM that keeps fading every real large overshoot both ways; it is
# never a size-cut, an entry-block, or a directional throttle.
# ---------------------------------------------------------------------------


def _overshoot_window_steps(direction: int, steps: list[float]) -> list[TickSample]:
    """A long calm base then a ramp of ``steps`` into a ``direction`` overshoot
    that flow OPPOSES throughout (trades print against the spike = exhaustion).

    ``steps`` sets the overshoot magnitude → a STEEP final-vs-ramp jump yields a
    fat overshoot_z (≫ θ_r); a gentler ramp yields a shallow one (in the sub-cost
    band). Mirrors ``_overshoot_window`` but parametrises the step ladder so the
    fat-tail and sub-cost-band fixtures share one builder.
    """
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(16):
        mid += 0.0005 if i % 2 == 0 else -0.0005
        ticks.append(_tick(_BASE + i, mid, last_trade_price=mid, last_trade_size=0.5))
    for j, step in enumerate(steps):
        mid += direction * step
        trade_px = mid - direction * 0.05  # aggressor opposes → exhaustion
        ticks.append(
            _tick(_BASE + 16 + j, mid, last_trade_price=trade_px, last_trade_size=6.0)
        )
    return ticks


# A steep ramp → overshoot_z ≈ 2.2 (past the new 2.0 bar = cost-clearing fat tail).
_FAT_OVERSHOOT_STEPS = [0.10, 0.14, 0.18, 0.34]
# A gentle ramp → overshoot_z ≈ 1.82 (inside the 1.5–2.0 sub-cost noise band).
_SUBCOST_OVERSHOOT_STEPS = [0.12, 0.14, 0.16, 0.18]


def test_micro_reversion_fades_fat_overshoot_above_new_bar() -> None:
    # Steep overshoot → |overshoot_z| ≈ 2.2 > θ_r=2.0 (fat tail) → STILL fades.
    feat = compute_tick_features(
        _overshoot_window_steps(+1, _FAT_OVERSHOOT_STEPS), NOW, CFG
    )
    assert feat.overshoot_z is not None and feat.overshoot_z > CFG.theta_revert
    intent = micro_reversion(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=101.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "short"  # fade the up overshoot
    assert 0.0 < intent.conviction <= CFG.conviction_cap


def test_micro_reversion_still_fades_down_overshoot_bidirectional() -> None:
    # Bidirectional preserved: the mirror DOWN fat overshoot STILL fades LONG (the
    # re-aim is symmetric — never a one-way block).
    feat = compute_tick_features(
        _overshoot_window_steps(-1, _FAT_OVERSHOOT_STEPS), NOW, CFG
    )
    assert feat.overshoot_z is not None and feat.overshoot_z < -CFG.theta_revert
    intent = micro_reversion(feat, "crisis", venue=VENUE, symbol=SYMBOL, ref_price=99.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "long"  # fade the down overshoot
    assert 0.0 < intent.conviction <= CFG.conviction_cap


def test_micro_reversion_silent_on_sub_cost_overshoot_band() -> None:
    # A gentle overshoot → |overshoot_z| ≈ 1.82: ABOVE the OLD 1.5 bar (would have
    # fired into the net loss) but BELOW the new 2.0 bar → must NOT fire. Both
    # directions.
    up_band = compute_tick_features(
        _overshoot_window_steps(+1, _SUBCOST_OVERSHOOT_STEPS), NOW, CFG
    )
    down_band = compute_tick_features(
        _overshoot_window_steps(-1, _SUBCOST_OVERSHOOT_STEPS), NOW, CFG
    )
    assert up_band.overshoot_z is not None and 1.5 < up_band.overshoot_z < CFG.theta_revert
    assert down_band.overshoot_z is not None and -CFG.theta_revert < down_band.overshoot_z < -1.5
    assert micro_reversion(up_band, "chop", venue=VENUE, symbol=SYMBOL, ref_price=101.0, cfg=CFG) is None
    assert micro_reversion(down_band, "chop", venue=VENUE, symbol=SYMBOL, ref_price=99.0, cfg=CFG) is None


def test_micro_reversion_theta_revert_is_env_tunable() -> None:
    # The bar is a NAMED env-tunable so the next re-measure can re-aim it without a
    # redeploy. A lower env θ_r re-arms the sub-cost band; a default (unset) keeps
    # the re-aimed 2.0 coded default.
    import os

    from polaris.core.ticks.config import TickEngineConfig

    assert TickEngineConfig().theta_revert == 2.0
    prev = os.environ.get("POLARIS_TICK_THETA_REVERT")
    os.environ["POLARIS_TICK_THETA_REVERT"] = "1.5"
    try:
        assert TickEngineConfig().theta_revert == 1.5
    finally:
        if prev is None:
            del os.environ["POLARIS_TICK_THETA_REVERT"]
        else:
            os.environ["POLARIS_TICK_THETA_REVERT"] = prev


# ---------------------------------------------------------------------------
# regime gate — the wrong signals are disabled per regime.
# ---------------------------------------------------------------------------


def test_regime_gate_active_sets() -> None:
    assert active_signals("bull_trend") == frozenset({"burst_rider", "flow_pressure"})
    assert active_signals("bear_trend") == frozenset({"burst_rider", "flow_pressure"})
    assert active_signals("chop") == frozenset({"micro_reversion", "flow_pressure"})
    assert active_signals("crisis") == frozenset({"micro_reversion"})
    # Unknown / None never silences the engine — flow_pressure stays on.
    assert active_signals("wat") == frozenset({"flow_pressure"})
    assert active_signals(None) == frozenset({"flow_pressure"})


def test_regime_gate_normalize_and_bias() -> None:
    assert normalize_regime("bull_trend") == "trend"
    assert normalize_regime("CHOP") == "range"
    assert normalize_regime(None) == "unknown"
    assert direction_bias("bull_trend") == 1
    assert direction_bias("bear_trend") == -1
    assert direction_bias("chop") == 0
    assert direction_bias("crisis") == 0


def test_gate_disables_burst_in_chop_and_reversion_in_trend() -> None:
    # A real burst window, but in a CHOP regime burst_rider is gated out, while
    # the reversion signal is gated out in a TREND regime.
    burst_feat = compute_tick_features(_burst_window(+1), NOW, CFG)
    assert burst_rider(burst_feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=1.0, cfg=CFG) is None

    over_feat = compute_tick_features(_overshoot_window(+1), NOW, CFG)
    assert (
        micro_reversion(over_feat, "bull_trend", venue=VENUE, symbol=SYMBOL, ref_price=1.0, cfg=CFG)
        is None
    )


def test_crisis_disables_momentum_signals() -> None:
    burst_feat = compute_tick_features(_burst_window(+1), NOW, CFG)
    imb_feat = compute_tick_features(_imbalance_window(+1), NOW, CFG)
    assert burst_rider(burst_feat, "crisis", venue=VENUE, symbol=SYMBOL, ref_price=1.0, cfg=CFG) is None
    # crisis active set is {micro_reversion} only → flow_pressure also gated out.
    assert flow_pressure(imb_feat, "crisis", venue=VENUE, symbol=SYMBOL, ref_price=1.0, cfg=CFG) is None
