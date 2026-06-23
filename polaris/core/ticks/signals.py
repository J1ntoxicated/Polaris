"""Pure bidirectional tick signals (P5).

Spec SSOT: ``.claude/plans/p5_tick_decision_engine_2026-06-03.md`` §"신규 모듈".

Three pure signal functions, each ``(TickFeatures, regime, *, venue, symbol,
ref_price, cfg) -> TickIntent | None``:

  - ``burst_rider``    (momentum): a fast directional burst with aggressor flow
    agreeing and a tight spread → ride it. side = sign(velocity).
  - ``flow_pressure``  (momentum): sustained order-flow imbalance with aggressor
    flow agreeing → lean with the book. side = sign(ofi).
  - ``micro_reversion`` (reversion): the mid is stretched past its EWMA anchor
    AND flow is opposing the overshoot (the push is exhausting) → fade it.
    side = -sign(overshoot).

All three are **bidirectional** ("모든 상황 수익(양방향)"): the same threshold
arms long or short symmetrically. Conviction is in ``[0, 1]`` and **monotone**
in the trigger magnitude (a bigger burst / imbalance / overshoot → higher
conviction), saturating at ``cfg.conviction_cap`` once the magnitude is
``cfg.conviction_ref_*`` past its threshold.

Each function first asks ``regime_gate.active_signals`` whether it is allowed to
fire in the current regime; a gated-out signal returns ``None`` (mis-aligned,
not throttled). A safe-sentinel feature window (any required field ``None``)
also returns ``None`` — no firing on a starved/stale window. PURE: no I/O.

``venue`` / ``symbol`` / ``ref_price`` are the instrument context the impure
loop supplies (the engine knows the instrument + latest mid); the pure signal
only decides side + conviction from the features.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from polaris.core.ticks.config import TickEngineConfig
from polaris.core.ticks.features import TickFeatures
from polaris.core.ticks.regime_gate import active_signals

__all__ = [
    "TickIntent",
    "burst_rider",
    "flow_pressure",
    "micro_reversion",
]

Side = Literal["long", "short"]
SignalFamily = Literal["momentum", "reversion"]

_BURST_RIDER = "burst_rider"
_FLOW_PRESSURE = "flow_pressure"
_MICRO_REVERSION = "micro_reversion"


@dataclass(frozen=True, slots=True)
class TickIntent:
    """A directional tick-decision intent (pre-sizing, pre-risk-gate).

    The pure signal output. The impure loop turns it into an order via the
    existing ``compute_size`` + executor — this carries NO notional / multiplier
    (the T4 9-stack chain is untouched).

    Fields:
      - ``venue`` / ``symbol``: instrument identity (loop-supplied context).
      - ``side``: ``'long'`` | ``'short'`` — bidirectional.
      - ``conviction``: ``[0, 1]``, monotone in the trigger magnitude.
      - ``signal_id``: which signal fired (``burst_rider`` / ``flow_pressure`` /
        ``micro_reversion``).
      - ``signal_family``: ``'momentum'`` | ``'reversion'`` — drives the hybrid
        exit horizon (momentum → ATR-trail, reversion → fast scalp).
      - ``ref_price``: the mid the decision was taken at (loop-supplied).
    """

    venue: str
    symbol: str
    side: Side
    conviction: float
    signal_id: str
    signal_family: SignalFamily
    ref_price: float


def _ramp(magnitude: float, threshold: float, ref: float, cap: float) -> float:
    """Map a trigger ``magnitude`` (≥ threshold) to conviction in ``[0, cap]``.

    Linear from 0 at the threshold to ``cap`` once ``magnitude`` is ``ref`` past
    the threshold, then clamped. Monotone non-decreasing in ``magnitude`` (the
    invariant the property tests assert). ``ref <= 0`` degrades to full cap at
    the threshold (no division).
    """
    if ref <= 0:
        return cap
    frac = (magnitude - threshold) / ref
    if frac <= 0.0:
        return 0.0
    if frac >= 1.0:
        return cap
    return cap * frac


def _same_sign(a: float, b: float) -> bool:
    """True iff ``a`` and ``b`` are both strictly positive or both strictly negative."""
    return (a > 0 and b > 0) or (a < 0 and b < 0)


def burst_rider(
    feat: TickFeatures,
    regime: str | None,
    *,
    venue: str,
    symbol: str,
    ref_price: float,
    cfg: TickEngineConfig | None = None,
) -> TickIntent | None:
    """Momentum: ride a fast directional burst confirmed by aggressor flow.

    Arms iff (gated active) ∧ ``burst_z > θ_b`` ∧ aggressor flow agrees with the
    velocity direction ∧ ``spread_bps < θ_s``. side = sign(velocity).
    Conviction ramps with ``burst_z`` past ``θ_b``. Returns ``None`` on a
    safe-sentinel window, a gated-out regime, or any unmet condition.
    """
    cfg = cfg or TickEngineConfig()
    if _BURST_RIDER not in active_signals(regime):
        return None
    if feat.burst_z is None or feat.velocity is None or feat.aggr_flow is None:
        return None
    if feat.spread_bps is None or feat.spread_bps >= cfg.theta_spread:
        return None
    if feat.burst_z <= cfg.theta_burst:
        return None
    if feat.velocity == 0.0:
        return None
    # Aggressor flow must agree with the burst direction (confirmation).
    if not _same_sign(feat.aggr_flow, feat.velocity):
        return None
    side: Side = "long" if feat.velocity > 0 else "short"
    conviction = _ramp(
        feat.burst_z, cfg.theta_burst, cfg.conviction_ref_burst, cfg.conviction_cap
    )
    return TickIntent(
        venue=venue,
        symbol=symbol,
        side=side,
        conviction=conviction,
        signal_id=_BURST_RIDER,
        signal_family="momentum",
        ref_price=ref_price,
    )


def flow_pressure(
    feat: TickFeatures,
    regime: str | None,
    *,
    venue: str,
    symbol: str,
    ref_price: float,
    cfg: TickEngineConfig | None = None,
) -> TickIntent | None:
    """Momentum: lean with a sustained order-flow imbalance — AFTER confirmation.

    Arms iff (gated active) ∧ ``|ofi| > θ_o`` ∧ aggressor flow agrees with the
    imbalance sign ∧ POST-SPIKE FOLLOW-THROUGH holds (``feat.flow_confirmed``).
    side = sign(ofi). Conviction ramps with ``|ofi|`` past ``θ_o``. Returns
    ``None`` on a safe-sentinel window, a gated-out regime, or any unmet
    condition.

    The ``flow_confirmed`` gate is the ENTRY-TIMING fix
    ([[flow_pressure_calibration_ai_2026-06-23]]): the raw-spike entry was buying
    the TOP of an OFI-positive spike that immediately reversed (59% never reached
    +0.2R — exhaustion mistaken for continuation). Requiring the follow-through
    means the entry waits for the post-spike confirmation tick (OFI stays
    elevated, the bid follows / replenishes, the microprice holds above the spike
    midpoint, spread is stable). An unconfirmed tick returns ``None`` → the symbol
    is simply re-evaluated next cadence: this is TIMING precision (a DELAY to a
    confirmed tick), NOT a veto / size-cut — flow_not_block. A genuine
    follow-through still fires bidirectionally on every real large imbalance.
    """
    cfg = cfg or TickEngineConfig()
    if _FLOW_PRESSURE not in active_signals(regime):
        return None
    if feat.ofi is None or feat.aggr_flow is None:
        return None
    if abs(feat.ofi) <= cfg.theta_ofi:
        return None
    if feat.ofi == 0.0:
        return None
    # Aggressor flow must agree with the book imbalance (pressure is real, not
    # passive size that is being hit).
    if not _same_sign(feat.aggr_flow, feat.ofi):
        return None
    # ENTRY follow-through: do not buy the exhaustion top — wait for the post-
    # spike confirmation tick. ``flow_confirmed`` is ``False`` while the spike is
    # reversing (immediate top-buy) → return None this tick (DELAY, not veto);
    # the next confirmed tick re-arms the same long/short flow.
    if feat.flow_confirmed is not True:
        return None
    side: Side = "long" if feat.ofi > 0 else "short"
    conviction = _ramp(
        abs(feat.ofi), cfg.theta_ofi, cfg.conviction_ref_ofi, cfg.conviction_cap
    )
    return TickIntent(
        venue=venue,
        symbol=symbol,
        side=side,
        conviction=conviction,
        signal_id=_FLOW_PRESSURE,
        signal_family="momentum",
        ref_price=ref_price,
    )


def micro_reversion(
    feat: TickFeatures,
    regime: str | None,
    *,
    venue: str,
    symbol: str,
    ref_price: float,
    cfg: TickEngineConfig | None = None,
) -> TickIntent | None:
    """Reversion: fade a stretched mid when flow is exhausting (opposing it).

    Arms iff (gated active) ∧ ``|overshoot_z| > θ_r`` ∧ flow opposes the
    overshoot (a positive overshoot with non-positive aggressor flow = the push
    up is spent → fade short; symmetric for a negative overshoot).
    side = -sign(overshoot). Conviction ramps with ``|overshoot_z|`` past
    ``θ_r``. Returns ``None`` on a safe-sentinel window, a gated-out regime, or
    any unmet condition.
    """
    cfg = cfg or TickEngineConfig()
    if _MICRO_REVERSION not in active_signals(regime):
        return None
    if feat.overshoot_z is None or feat.aggr_flow is None:
        return None
    if abs(feat.overshoot_z) <= cfg.theta_revert:
        return None
    if feat.overshoot_z == 0.0:
        return None
    # Flow must OPPOSE the overshoot (exhaustion). A stretched-up mid
    # (overshoot > 0) needs flow that is NOT still buying (aggr_flow <= 0); a
    # stretched-down mid needs flow that is NOT still selling (aggr_flow >= 0).
    if feat.overshoot_z > 0 and feat.aggr_flow > 0:
        return None
    if feat.overshoot_z < 0 and feat.aggr_flow < 0:
        return None
    side: Side = "short" if feat.overshoot_z > 0 else "long"
    conviction = _ramp(
        abs(feat.overshoot_z),
        cfg.theta_revert,
        cfg.conviction_ref_revert,
        cfg.conviction_cap,
    )
    return TickIntent(
        venue=venue,
        symbol=symbol,
        side=side,
        conviction=conviction,
        signal_id=_MICRO_REVERSION,
        signal_family="reversion",
        ref_price=ref_price,
    )
