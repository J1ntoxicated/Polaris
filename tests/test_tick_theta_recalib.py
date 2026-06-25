"""P5 tick θ RE-AIM to the MEASURED window-max distribution (volume-now retune).

The live tick engine fired only ~7-12 signals / 30min despite ~85 ticks/sec —
the old firing bars sat ABOVE the body of real flow. 28 thirty-second telemetry
windows (each logging that window's MAX feature magnitude) showed:
  - |overshoot_z|: window-max p50=1.22 — at θ_r=2.0 only 18% of windows even
    REACHED the bar, so ``micro_reversion`` was essentially DEAD.
  - |ofi|: window-max p25=0.63/p50=0.80 are PEAKS; the per-tick body sits far
    lower, so θ_o=0.40 filtered most real ticks. ``flow_pressure`` also gated by
    a strict follow-through (confirm_ofi_frac=0.6 / confirm_ticks=4).

This file pins the re-aimed defaults (θ_r 2.0→1.2, θ_o 0.40→0.25,
confirm_ofi_frac 0.6→0.4, confirm_ticks 4→3) and asserts the new firing
behaviour: the body of genuine overshoot / imbalance now ARMS (flow_not_block),
while sub-bar noise and a genuine EXHAUSTION still return None — the
follow-through STRUCTURE is loosened in magnitude, never turned always-True. The
env knobs still win (the next re-measure can re-aim without a redeploy).

Spec SSOT: .claude/plans/p5_tick_decision_engine_2026-06-03.md §"신규 모듈".
"""

from __future__ import annotations

import math

import pytest

from polaris.core.ticks.config import TickEngineConfig
from polaris.core.ticks.features import _flow_followthrough, compute_tick_features
from polaris.core.ticks.signals import TickIntent, flow_pressure, micro_reversion
from polaris.core.ticks.types import TickSample

CFG = TickEngineConfig()
NOW = 1_780_451_113.0
VENUE = "okx"
SYMBOL = "BTC-USDT"
# 20 ticks 1s apart ending ~6s before NOW → fresh window.
_BASE = int(NOW - 25)


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


# ---------------------------------------------------------------------------
# Re-aimed DEFAULTS (env unset → coded defaults). These are the firing bars the
# engine actually reads; the runtime read path is signals.py (θ_o/θ_r) +
# features.py (confirm knobs), with no shadowing hardcoded literal.
# ---------------------------------------------------------------------------


def test_recalibrated_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_TICK_THETA_OFI", raising=False)
    monkeypatch.delenv("POLARIS_TICK_THETA_REVERT", raising=False)
    cfg = TickEngineConfig()
    assert cfg.theta_revert == 1.2
    assert cfg.theta_ofi == 0.25
    assert cfg.confirm_ofi_frac == 0.4
    assert cfg.confirm_ticks == 3


def test_env_override_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    # The named env knob is intact — the next re-measure re-aims without redeploy.
    monkeypatch.setenv("POLARIS_TICK_THETA_OFI", "0.5")
    assert TickEngineConfig().theta_ofi == 0.5
    monkeypatch.setenv("POLARIS_TICK_THETA_REVERT", "1.8")
    assert TickEngineConfig().theta_revert == 1.8


# ---------------------------------------------------------------------------
# micro_reversion — re-aimed θ_r 2.0 → 1.2 (the MEASURED |overshoot_z| p50).
# A genuine overshoot whose z sits between the OLD (2.0) and NEW (1.2) bar now
# ARMS (was dead); below 1.2 still returns None (no unconditional emit). The
# fade direction (side = -sign(overshoot)) + finite conviction are preserved.
# ---------------------------------------------------------------------------


def _overshoot_window(direction: int, steps: list[float]) -> list[TickSample]:
    """Calm anchor then a ``direction`` overshoot ramp that flow OPPOSES.

    ``steps`` (4 ticks) sets the overshoot magnitude. Trades print AGAINST the
    spike (aggr_flow opposes) so the exhaustion condition micro_reversion needs
    holds. A gentle/settling final step → a shallow overshoot_z in the body
    band; a roll-back final step → below the new bar.
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


# overshoot_z ≈ 1.51 — between the OLD 2.0 bar (dead) and the NEW 1.2 bar (arms).
_BODY_OVERSHOOT_STEPS = [0.10, 0.14, 0.18, 0.06]
# overshoot_z ≈ 1.02 — below the NEW 1.2 bar (still must NOT fire).
_BELOW_BAR_OVERSHOOT_STEPS = [0.10, 0.14, 0.18, -0.06]


def test_micro_reversion_now_fires_on_body_overshoot() -> None:
    # |overshoot_z| ≈ 1.51 sits in the body the old 2.0 bar starved; the re-aimed
    # 1.2 bar now ARMS it. Was None at θ_r=2.0 → fires (flow_not_block).
    feat = compute_tick_features(_overshoot_window(+1, _BODY_OVERSHOOT_STEPS), NOW, CFG)
    assert feat.overshoot_z is not None
    assert 1.2 < feat.overshoot_z < 2.0  # between the new and old bar
    assert feat.aggr_flow is not None and feat.aggr_flow <= 0.0  # flow opposes
    intent = micro_reversion(
        feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=101.0, cfg=CFG
    )
    assert isinstance(intent, TickIntent)
    assert intent.side == "short"  # side = -sign(overshoot) preserved
    assert intent.signal_id == "micro_reversion"
    assert intent.signal_family == "reversion"
    assert math.isfinite(intent.conviction)
    assert 0.0 < intent.conviction <= CFG.conviction_cap


def test_micro_reversion_fires_down_overshoot_long_bidirectional() -> None:
    # Mirror: a body-band DOWN overshoot now fades LONG — bidirectional preserved.
    feat = compute_tick_features(_overshoot_window(-1, _BODY_OVERSHOOT_STEPS), NOW, CFG)
    assert feat.overshoot_z is not None and -2.0 < feat.overshoot_z < -1.2
    intent = micro_reversion(
        feat, "crisis", venue=VENUE, symbol=SYMBOL, ref_price=99.0, cfg=CFG
    )
    assert isinstance(intent, TickIntent)
    assert intent.side == "long"


def test_micro_reversion_silent_below_new_bar() -> None:
    # |overshoot_z| ≈ 1.02 < new 1.2 bar → still None (no unconditional emit).
    feat = compute_tick_features(
        _overshoot_window(+1, _BELOW_BAR_OVERSHOOT_STEPS), NOW, CFG
    )
    assert feat.overshoot_z is not None and abs(feat.overshoot_z) < 1.2
    assert (
        micro_reversion(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=101.0, cfg=CFG)
        is None
    )


# ---------------------------------------------------------------------------
# flow_pressure — re-aimed θ_o 0.40 → 0.25 (admit the body of the imbalance
# distribution). An |ofi| between the OLD (0.40) and NEW (0.25) bar, WITH
# flow_confirmed True + aggr_flow agreeing, now fires (was None at 0.40); below
# 0.25 still None. side = sign(ofi) preserved.
# ---------------------------------------------------------------------------


def _extending_imbalance_window(
    bid_size: float, ask_size: float, side_sign: int = 1
) -> list[TickSample]:
    """A persistent imbalance whose TAIL is STILL CLIMBING (genuine follow-through).

    ``bid_size``/``ask_size`` set the |ofi| magnitude. The 16-tick run climbs into
    a 4-tick still-extending tail (new high every tick, velocity holding) so
    ``flow_confirmed`` is True under the loosened confirm knobs — NOT an
    exhaustion. ``side_sign`` -1 mirrors to an ask-heavy dropping book (short).
    """
    tail_mids = (
        [100.30, 100.34, 100.39, 100.45]
        if side_sign > 0
        else [99.70, 99.66, 99.61, 99.55]
    )
    ticks: list[TickSample] = []
    run0 = tail_mids[0]
    for i in range(16):
        mid = run0 - side_sign * (16 - i) * 0.02
        trade_px = mid + side_sign * 0.05
        ticks.append(
            _tick(_BASE + i, mid, bid_size=bid_size, ask_size=ask_size,
                  last_trade_price=trade_px, last_trade_size=4.0)
        )
    for j, m in enumerate(tail_mids):
        trade_px = m + side_sign * 0.05
        ticks.append(
            _tick(_BASE + 16 + j, m, bid_size=bid_size, ask_size=ask_size,
                  last_trade_price=trade_px, last_trade_size=4.0)
        )
    return ticks


def test_flow_pressure_now_fires_on_body_imbalance() -> None:
    # bid=65/ask=35 → |ofi| ≈ 0.30 sits between the new 0.25 and old 0.40 bar; the
    # tail still climbs (flow_confirmed True), aggr_flow agrees → now fires (was
    # None at θ_o=0.40). flow_not_block.
    feat = compute_tick_features(_extending_imbalance_window(65.0, 35.0), NOW, CFG)
    assert feat.ofi is not None and 0.25 < feat.ofi < 0.40
    assert feat.flow_confirmed is True
    assert feat.aggr_flow is not None and feat.aggr_flow > 0.0  # agrees
    intent = flow_pressure(
        feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG
    )
    assert isinstance(intent, TickIntent)
    assert intent.side == "long"  # side = sign(ofi) preserved
    assert intent.signal_id == "flow_pressure"
    assert math.isfinite(intent.conviction)
    assert 0.0 < intent.conviction <= CFG.conviction_cap


def test_flow_pressure_body_imbalance_short_bidirectional() -> None:
    # Mirror: an ask-heavy still-dropping book in the body band fires a SHORT.
    feat = compute_tick_features(
        _extending_imbalance_window(35.0, 65.0, side_sign=-1), NOW, CFG
    )
    assert feat.ofi is not None and -0.40 < feat.ofi < -0.25
    assert feat.flow_confirmed is True
    intent = flow_pressure(
        feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG
    )
    assert isinstance(intent, TickIntent)
    assert intent.side == "short"


def test_flow_pressure_silent_below_new_bar() -> None:
    # bid=58/ask=42 → |ofi| ≈ 0.16 < new 0.25 bar → still None (θ check trips
    # first, before confirm). No firing on the sub-bar noise band.
    feat = compute_tick_features(_extending_imbalance_window(58.0, 42.0), NOW, CFG)
    assert feat.ofi is not None and abs(feat.ofi) < 0.25
    assert (
        flow_pressure(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG)
        is None
    )


# ---------------------------------------------------------------------------
# flow_confirmed STRUCTURE preserved — the confirm gate is loosened in MAGNITUDE
# (confirm_ofi_frac 0.6→0.4, confirm_ticks 4→3), NOT turned always-True. A
# genuine EXHAUSTION / immediate-reversal must STILL read flow_confirmed False.
# ---------------------------------------------------------------------------


def _exhaustion_spike_window(side_sign: int) -> list[TickSample]:
    """A bid-heavy (long) book whose PRICE immediately REVERSES through the spike
    midpoint and whose near-touch size evaporates — the classic exhaustion top.

    |ofi| EWMA stays imbalanced (clears the new θ_o) so flow_pressure ARMS on OFI,
    but the follow-through must read this as ``flow_confirmed == False`` under the
    loosened confirm knobs (the magnitude bar dropped, the reversal STRUCTURE
    test did not) → no top-buy entry.
    """
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(16):
        mid += side_sign * 0.03
        bid_size, ask_size = (120.0, 8.0) if side_sign > 0 else (8.0, 120.0)
        trade_px = mid + side_sign * 0.05
        ticks.append(
            _tick(_BASE + i, mid, bid_size=bid_size, ask_size=ask_size,
                  last_trade_price=trade_px, last_trade_size=4.0)
        )
    for j in range(4):
        mid -= side_sign * 0.10  # reverse hard against the spike
        bid_size, ask_size = (60.0, 8.0) if side_sign > 0 else (8.0, 60.0)
        trade_px = mid - side_sign * 0.05  # aggressor now opposing
        ticks.append(
            _tick(_BASE + 16 + j, mid, bid_size=bid_size, ask_size=ask_size,
                  last_trade_price=trade_px, last_trade_size=4.0)
        )
    return ticks


def test_exhaustion_still_unconfirmed_with_loosened_knobs() -> None:
    # The loosened confirm (frac 0.4 / ticks 3) did NOT become always-True: a
    # genuine immediate-reversal exhaustion is STILL flow_confirmed False → no
    # top-buy. Both directions. (compute path uses cfg.confirm_* + cfg.theta_ofi.)
    up = compute_tick_features(_exhaustion_spike_window(+1), NOW, CFG)
    assert up.ofi is not None and up.ofi > CFG.theta_ofi  # ARMS on OFI
    assert up.flow_confirmed is False
    assert (
        flow_pressure(up, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG)
        is None
    )
    down = compute_tick_features(_exhaustion_spike_window(-1), NOW, CFG)
    assert down.flow_confirmed is False


def test_flow_followthrough_floor_uses_loosened_frac() -> None:
    # Direct unit on the follow-through reader: with confirm_ofi_frac=0.4 /
    # confirm_ticks=3 the tail-OFI floor is 0.4 × θ_o (loosened from 0.6), and the
    # k>=3 clamp is honoured. A still-climbing bid-heavy tail confirms; the same
    # reader rejects a tail whose instantaneous OFI has decayed below the floor.
    confirmed = _extending_imbalance_window(65.0, 35.0)
    assert (
        _flow_followthrough(
            confirmed,
            ofi_sign=1.0,
            theta_ofi=CFG.theta_ofi,
            confirm_ticks=CFG.confirm_ticks,
            confirm_ofi_frac=CFG.confirm_ofi_frac,
        )
        is True
    )
    exhausted = _exhaustion_spike_window(+1)
    assert (
        _flow_followthrough(
            exhausted,
            ofi_sign=1.0,
            theta_ofi=CFG.theta_ofi,
            confirm_ticks=CFG.confirm_ticks,
            confirm_ofi_frac=CFG.confirm_ofi_frac,
        )
        is False
    )
