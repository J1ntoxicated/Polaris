"""Pure tick microstructure features (P5).

Spec SSOT: ``.claude/plans/p5_tick_decision_engine_2026-06-03.md`` §"신규 모듈".

``compute_tick_features`` projects a live tick window (oldest→newest
``TickSample`` rows) into the microstructure features the signal module reads:
velocity / accel / burst_z / ofi / aggr_flow / overshoot_z / spread_bps. It is
PURE — no I/O, no clock read beyond the injected ``now_ts`` (epoch seconds, only
used for the staleness gate, never to fabricate a signal).

Safety (no false signal): if the window is too thin (``< cfg.min_ticks``) or
stale (newest tick older than ``cfg.fresh_sec`` vs ``now_ts``), the
z-score / EWMA fields are ``None`` and ``n_ticks`` / ``age_sec`` report the raw
state. A downstream signal therefore CANNOT fire on a starved window — it sees
``None`` and returns ``None`` (validation-starvation guard, not a throttle).

EWMA convention: each sample's weight decays with the *time* gap to the next
sample (``exp(-Δt / span)``), so irregular tick spacing is handled correctly
(a 2s gap decays more than a 0.1s gap). Spans are the 1/3/10s horizons in
``cfg``. The intra-window Δt come from each ``TickSample.ts`` (epoch SECONDS),
used as the real second-gaps between consecutive ticks. Absolute freshness
(``age_sec``) is ``now_ts`` minus the newest tick's ts — both epoch seconds, so
the age is the real wall-clock staleness; only *positive* staleness past
``cfg.fresh_sec`` is rejected (a negative age, e.g. a synthetic test origin
where now precedes the window, is treated as fresh, never spuriously cut).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from polaris.core.ticks.config import TickEngineConfig
from polaris.core.ticks.types import TickSample

__all__ = ["TickFeatures", "compute_tick_features"]

# Floors so divisions never blow up on degenerate windows (all-same-ts /
# zero-variance). Not a throttle — a numerical guard.
_MIN_DT_SEC = 1e-3
_MIN_STD = 1e-9

# Spread follow-through tolerance: the latest spread may sit up to this fraction
# above the tail mean before it counts as "widening against" the entry. Absorbs
# the bps creep from a falling mid at a constant absolute spread; a real adverse
# widen (the spread blowing out as the spike reverses) is far larger than 5%.
_SPREAD_WIDEN_TOL = 0.05

# Continuation-gate accel floor: a STEADY (constant-velocity) climb has accel
# EXACTLY 0 and IS still climbing — it must NOT be read as deceleration. This
# tiny negative floor absorbs only float-subtraction noise on equal step
# velocities (e.g. -1e-17), so a genuine sustained climb still arms while a real
# plateau (velocity decaying toward 0 → materially negative accel for a long)
# fails. Not a throttle — a numerical guard around the "non-deceleration" test.
_ACCEL_FLOOR = 1e-9


@dataclass(frozen=True, slots=True)
class TickFeatures:
    """Microstructure features over a live tick window (all in mid-price space).

    ``None`` fields mean "insufficient / stale window" — a safe sentinel that a
    signal must read as "do not fire" (never as a zero-valued real reading).
    ``n_ticks`` and ``age_sec`` are always populated (window state is always
    knowable, even when the features are not).

    Fields:
      - ``velocity``: Δmid/Δt of the most recent step (mid units per second).
      - ``accel``: Δvelocity between the last two steps (mid units per s²).
      - ``burst_z``: z-score of |velocity| vs the trailing per-step speed
        baseline (mean+std over the window's steps). High = a burst.
      - ``ofi``: EWMA of order-flow imbalance ``(bid_size-ask_size)/(bid+ask)``
        in ``[-1, 1]`` (+ = bid-heavy / buy pressure).
      - ``aggr_flow``: EWMA of ``sign(last_trade-mid) * last_trade_size`` —
        signed aggressor volume (+ = buyer-initiated lifting the offer).
      - ``overshoot_z``: ``(mid - EWMA_mid) / rolling_std`` — how stretched the
        latest mid is vs its short EWMA anchor (+ = stretched up).
      - ``spread_bps``: latest tick's spread in bps.
      - ``flow_confirmed``: POST-SPIKE follow-through, aligned to ``sign(ofi)`` —
        ``True`` iff the order-flow imbalance is genuine continuation (OFI stays
        elevated over the tail, the bid follows up / replenishes, the microprice
        HOLDS above the spike midpoint, spread is stable/tightening, AND the spike
        is STILL CLIMBING — non-decelerating, latest mid a new high/low, velocity
        persists same-sign), ``False`` iff the spike is exhausting (an immediate
        reversal — the 59% top-buy) OR merely plateauing at its peak (the 61%
        MFE~0 top-buy), ``None`` on a balanced book / insufficient tail. Read ONLY
        by ``flow_pressure`` as the ENTRY confirmation (TIMING precision — an
        unconfirmed tick is a DELAY, never a veto).
      - ``n_ticks``: window length.
      - ``age_sec``: seconds from the newest tick to ``now_ts`` (freshness).
    """

    velocity: float | None
    accel: float | None
    burst_z: float | None
    ofi: float | None
    aggr_flow: float | None
    overshoot_z: float | None
    spread_bps: float | None
    flow_confirmed: bool | None
    n_ticks: int
    age_sec: float | None


def _safe(n_ticks: int, age_sec: float | None) -> TickFeatures:
    """Return the all-None safe sentinel (carrying only window state)."""
    return TickFeatures(
        velocity=None,
        accel=None,
        burst_z=None,
        ofi=None,
        aggr_flow=None,
        overshoot_z=None,
        spread_bps=None,
        flow_confirmed=None,
        n_ticks=n_ticks,
        age_sec=age_sec,
    )


def _ewma_time(values: list[float], dts: list[float], span_sec: float) -> float:
    """Time-decayed EWMA: newest weighted 1, older decays ``exp(-Δt/span)``.

    ``values`` is oldest→newest (len N); ``dts`` (len N-1) is the gap *after*
    each of the first N-1 samples (``ts[i+1]-ts[i]`` in seconds). The newest
    sample anchors weight 1.0; walking backward, each prior sample's weight is
    the running product of ``exp(-Δt/span)``. Pure; span > 0 assumed.
    """
    weight = 1.0
    acc = values[-1]
    norm = 1.0
    # Walk from the second-newest back to the oldest, accumulating decay.
    for i in range(len(values) - 2, -1, -1):
        weight *= math.exp(-dts[i] / span_sec)
        acc += weight * values[i]
        norm += weight
    return acc / norm


def _microprice(t: TickSample) -> float:
    """Size-weighted microprice ``(bid*ask_size + ask*bid_size)/(bid+ask sizes)``.

    The book-pressure fair value: heavier bid size pulls it toward the ask (buy
    pressure), heavier ask size toward the bid. Degrades to the mid when sizes are
    absent (zero denom) — a price-only feed (Capital) thus has microprice == mid.
    """
    denom = t.bid_size + t.ask_size
    if denom <= 0.0:
        return t.mid
    return (t.bid * t.ask_size + t.ask * t.bid_size) / denom


def _flow_followthrough(
    window: list[TickSample],
    *,
    ofi_sign: float,
    theta_ofi: float,
    confirm_ticks: int,
    confirm_ofi_frac: float,
) -> bool:
    """POST-SPIKE follow-through for a ``sign(ofi)`` imbalance over the window tail.

    PURE. ``True`` iff the imbalance is genuine continuation (not an exhaustion
    top): over the last ``confirm_ticks`` ticks —
      (1) OFI STAYS ELEVATED / re-accelerates — both the LATEST instantaneous OFI
          and the tail MEAN hold ``≥ confirm_ofi_frac × θ_o`` in the sign
          direction (rejects a one-tick burst that decayed back to ~0),
      (2) the BID FOLLOWS UP / REPLENISHES (long: latest bid ≥ tail-start bid OR
          latest bid_size ≥ tail-mean bid_size; mirror on the ask for a short) —
          bid EVAPORATION = exhaustion,
      (3) the MICROPRICE HOLDS above the spike midpoint (long: microprice_now ≥
          midpoint of the tail mid-range; short: ≤) — a reversal back through the
          midpoint is the immediate top-buy reversal we are filtering out,
      (4) the SPREAD is STABLE / TIGHTENING (latest ≤ tail-mean, not widening).
      (5) the SPIKE is STILL CLIMBING — not merely "not dead yet" but EXTENDING:
          (a) NON-decelerating across the tail (long: latest accel ≥ 0; short:
              ≤ 0 — a plateau, whose velocity decays toward 0, fails), (b) the
              latest tail mid is a NEW HIGH (long) / NEW LOW (short) — a roll-over
              that prints below the prior tail max (long) is exhaustion, not
              continuation, (c) the last two step velocities PERSIST same-sign in
              the OFI direction (a single up-tick into a stalled book is not
              follow-through). Closes the plateau-top buy: a positive-OFI spike at
              its peak passes (1)–(4) (book still bid-heavy, microprice still
              above the midpoint) yet is no longer climbing — (5) DELAYS it to a
              later still-climbing tick.
    ``confirm_ticks`` is clamped to the available tail (≥3). This is the ENTRY
    TIMING gate — its caller DELAYS to a confirmed tick, never vetoes the flow.
    """
    n = len(window)
    k = min(confirm_ticks, n)
    if k < 3 or ofi_sign == 0.0:
        return False
    tail = window[-k:]
    inst_ofi: list[float] = []
    for t in tail:
        denom = t.bid_size + t.ask_size
        inst_ofi.append((t.bid_size - t.ask_size) / denom if denom > 0 else 0.0)
    floor = confirm_ofi_frac * theta_ofi
    # (1) elevated NOW + persisted across the tail (same sign as the EWMA ofi).
    if ofi_sign * inst_ofi[-1] < floor:
        return False
    if ofi_sign * (sum(inst_ofi) / k) < floor:
        return False
    last = tail[-1]
    start = tail[0]
    # (2) bid follows / replenishes (long) | ask follows / replenishes (short).
    mean_near_size = (
        sum(t.bid_size for t in tail) / k
        if ofi_sign > 0
        else sum(t.ask_size for t in tail) / k
    )
    if ofi_sign > 0:
        if last.bid < start.bid and last.bid_size < mean_near_size:
            return False
    else:
        if last.ask > start.ask and last.ask_size < mean_near_size:
            return False
    # (3) microprice HOLDS above (long) / below (short) the spike midpoint.
    mids = [t.mid for t in tail]
    spike_mid = (max(mids) + min(mids)) / 2.0
    micro_now = _microprice(last)
    if ofi_sign > 0 and micro_now < spike_mid:
        return False
    if ofi_sign < 0 and micro_now > spike_mid:
        return False
    # (4) spread stable / tightening (not widening AGAINST the entry). A small
    #     relative tolerance (``_SPREAD_WIDEN_TOL``) absorbs the bps-from-falling-
    #     mid drift at a constant absolute spread (spread_bps = spread/mid·1e4
    #     creeps up as the mid drifts down) — only a MATERIAL widen fails.
    mean_spread = sum(t.spread_bps for t in tail) / k
    if last.spread_bps > mean_spread * (1.0 + _SPREAD_WIDEN_TOL):
        return False
    # (5) STILL CLIMBING (extending spike), not just "not dead yet" — folds long
    #     and short via ``ofi_sign``. Tail step velocities (Δmid/Δt over the same
    #     tail), with Δt floored so a same-ts pair never divides by ~0.
    tail_dts: list[float] = []
    for i in range(k - 1):
        dt = float(tail[i + 1].ts - tail[i].ts)
        tail_dts.append(dt if dt > _MIN_DT_SEC else _MIN_DT_SEC)
    step_vel = [(mids[i + 1] - mids[i]) / tail_dts[i] for i in range(k - 1)]
    # (5c) last two step velocities persist in the OFI direction.
    if ofi_sign * step_vel[-1] <= 0.0 or ofi_sign * step_vel[-2] <= 0.0:
        return False
    # (5a) NON-decelerating: latest accel in the OFI direction ≥ -float-noise. A
    #      steady (constant-velocity) climb has accel 0 and PASSES; a plateau
    #      (velocity decaying toward 0) has accel against the side → fails.
    accel = step_vel[-1] - step_vel[-2]
    if ofi_sign * accel < -_ACCEL_FLOOR:
        return False
    # (5b) the latest tail mid is a NEW HIGH (long) / NEW LOW (short) — the spike
    #      EXTENDS, not rolls over.
    if ofi_sign > 0:
        return mids[-1] >= max(mids[:-1])
    return mids[-1] <= min(mids[:-1])


def compute_tick_features(
    window: list[TickSample],
    now_ts: float,
    cfg: TickEngineConfig | None = None,
) -> TickFeatures:
    """Compute :class:`TickFeatures` from a live tick window (oldest→newest).

    PURE. ``now_ts`` is the caller's EPOCH-SECONDS "now" (same clock domain as
    ``TickSample.ts``, which is venue event time in epoch seconds) — used for the
    freshness gate (``age_sec``). ``cfg`` supplies the EWMA spans + sufficiency
    thresholds (defaults when omitted).

    Returns the all-None safe sentinel when the window is empty, thinner than
    ``cfg.min_ticks``, or staler than ``cfg.fresh_sec`` — so no signal can fire
    on a starved/stale window.
    """
    cfg = cfg or TickEngineConfig()
    n = len(window)
    if n == 0:
        return _safe(0, None)

    # Freshness: newest tick's wall-clock age. Both ``ts`` and ``now_ts`` are
    # epoch SECONDS (same domain) → ``age_sec`` is the real staleness. A negative
    # age (a synthetic test origin where now precedes the window) is treated as
    # fresh; only positive staleness past the ceiling is rejected.
    newest = window[-1]
    age_sec = now_ts - newest.ts
    if age_sec > cfg.fresh_sec:
        return _safe(n, age_sec)
    if n < cfg.min_ticks:
        return _safe(n, age_sec)

    mids = [t.mid for t in window]
    # Per-step Δt (seconds; ts is epoch SECONDS), floored so a same-ts pair
    # never divides by ~0.
    dts: list[float] = []
    for i in range(n - 1):
        dt = float(window[i + 1].ts - window[i].ts)
        dts.append(dt if dt > _MIN_DT_SEC else _MIN_DT_SEC)

    # --- velocity (latest step) + accel (Δvelocity of last two steps) ----
    step_vel = [(mids[i + 1] - mids[i]) / dts[i] for i in range(n - 1)]
    velocity = step_vel[-1]
    accel = (step_vel[-1] - step_vel[-2]) / dts[-1] if len(step_vel) >= 2 else 0.0

    # --- burst_z: |velocity| vs trailing per-step speed baseline ----------
    speeds = [abs(v) for v in step_vel]
    mean_speed = sum(speeds) / len(speeds)
    var_speed = sum((s - mean_speed) ** 2 for s in speeds) / len(speeds)
    std_speed = math.sqrt(var_speed)
    burst_z = (abs(velocity) - mean_speed) / std_speed if std_speed > _MIN_STD else 0.0

    # --- ofi EWMA (fast horizon) -----------------------------------------
    ofi_vals = []
    for t in window:
        denom = t.bid_size + t.ask_size
        ofi_vals.append((t.bid_size - t.ask_size) / denom if denom > 0 else 0.0)
    ofi = _ewma_time(ofi_vals, dts, cfg.ewma_fast_sec)

    # --- aggr_flow EWMA (fast horizon): signed aggressor volume -----------
    aggr_vals = []
    for t in window:
        side = 0.0
        if t.last_trade_price > t.mid:
            side = 1.0
        elif t.last_trade_price < t.mid:
            side = -1.0
        aggr_vals.append(side * t.last_trade_size)
    aggr_flow = _ewma_time(aggr_vals, dts, cfg.ewma_fast_sec)

    # --- overshoot_z: (mid - EWMA_mid) / rolling_std (mid horizon) --------
    ewma_mid = _ewma_time(mids, dts, cfg.ewma_mid_sec)
    mean_mid = sum(mids) / n
    var_mid = sum((m - mean_mid) ** 2 for m in mids) / n
    std_mid = math.sqrt(var_mid)
    overshoot_z = (mids[-1] - ewma_mid) / std_mid if std_mid > _MIN_STD else 0.0

    # --- ENTRY follow-through (post-spike confirmation, aligned to sign(ofi)) -
    # ``None`` on a balanced book (no imbalance to confirm); otherwise the pure
    # tail follow-through bool the ENTRY gate (flow_pressure) reads to DELAY to a
    # confirmed tick instead of buying the exhaustion top.
    if ofi > 0.0:
        flow_confirmed: bool | None = _flow_followthrough(
            window, ofi_sign=1.0, theta_ofi=cfg.theta_ofi,
            confirm_ticks=cfg.confirm_ticks, confirm_ofi_frac=cfg.confirm_ofi_frac,
        )
    elif ofi < 0.0:
        flow_confirmed = _flow_followthrough(
            window, ofi_sign=-1.0, theta_ofi=cfg.theta_ofi,
            confirm_ticks=cfg.confirm_ticks, confirm_ofi_frac=cfg.confirm_ofi_frac,
        )
    else:
        flow_confirmed = None

    return TickFeatures(
        velocity=velocity,
        accel=accel,
        burst_z=burst_z,
        ofi=ofi,
        aggr_flow=aggr_flow,
        overshoot_z=overshoot_z,
        spread_bps=newest.spread_bps,
        flow_confirmed=flow_confirmed,
        n_ticks=n,
        age_sec=age_sec,
    )
