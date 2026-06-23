"""Tick-decision engine config — thresholds, EWMA spans, AGGRESSIVE defaults.

Spec SSOT: ``.claude/plans/p5_tick_decision_engine_2026-06-03.md`` §"신규 모듈".

This is the single carrier for every tunable the pure feature/signal modules
read. The defaults are **AGGRESSIVE** (bias toward firing): the trigger
thresholds sit low enough that a real burst / imbalance / overshoot fires
rather than being suppressed. Tighter values would defensively throttle entries
— forbidden. This config supplies thresholds only; it is NOT a sizing
multiplier (the T4 9-stack chain is untouched — sizing stays ``compute_size``).

``shadow`` is read from the ``TICK_ENGINE_SHADOW`` env (truthy ``1/true/yes/on``,
case-insensitive). It is a logging-vs-live cutover flag for the impure
integration loop; the pure modules never read it, but it lives here so the one
config object carries the whole knob set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

__all__ = [
    "TICK_ENGINE_OWNED_VENUES",
    "TICK_ENGINE_VENUE_SIGNALS",
    "TickEngineConfig",
    "env_shadow_enabled",
    "tick_engine_enabled",
    "tick_engine_owns_okx",
    "venue_allowed_signals",
]

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# P5 coexistence SSOT: the venues the tick-decision engine OWNS. BOTH the engine
# (as ``PHASE1_VENUES``) and the bar entry path read THIS single frozenset, so
# the two producers can never drift out of sync (no double-trade).
# D3 (/debate trading_params_audit_2026-06-22): the FAST tick loop is routed to
# the DATA-RICH venue. OKX (spot) carries FULL order-book depth + trade prints
# (``bidSz``/``askSz``/``last``/``lastSz``) → all three microstructure signals
# (burst_rider / flow_pressure / micro_reversion) are STRUCTURALLY LIVE there.
# Capital (CFD) streams price quotes only — its canonical converter zeroes
# ``bid_size``/``ask_size``/``last_trade_size`` (no L2 / no trade tape), so the
# order-FLOW signals (burst_rider, flow_pressure) are structurally DEAD on
# Capital; only the price-based overshoot fade (``micro_reversion``) can fire.
# So OKX OWNS the full tick path; Capital STAYS owned but its tick path is
# restricted to overshoot-fade-only (see ``TICK_ENGINE_VENUE_SIGNALS``). This is
# ROUTING the fast loop to the data each signal needs — flow_not_block: no entry
# is blocked, the slow bar pipeline still runs every other edge on both venues.
# Close paths route by ``trade.venue`` (``real_okx_close_fill`` /
# ``real_capital_close_fill``) so an OKX tick open closes on the OKX leg.
TICK_ENGINE_OWNED_VENUES: frozenset[str] = frozenset({"okx", "capital"})

# The full microstructure signal set (the three pure tick signals).
_ALL_TICK_SIGNALS: frozenset[str] = frozenset(
    {"burst_rider", "flow_pressure", "micro_reversion"}
)

# Per-venue STRUCTURALLY-LIVE signal sets (D3). A venue only runs the signals its
# WS feed can actually compute — this is a DATA-availability routing, never a
# defensive throttle (the dead signals would only ever return None on that venue
# anyway; restricting them just stops the engine wasting eval cycles + emitting
# misleading dry-fire telemetry). An unlisted venue defaults to the full set
# (flow_not_block — a new data-rich venue is never silently muted).
#   okx     → full depth + trade tape → all three signals.
#   capital → price quotes only (sizes/tape zeroed) → overshoot-fade ONLY.
TICK_ENGINE_VENUE_SIGNALS: dict[str, frozenset[str]] = {
    "okx": _ALL_TICK_SIGNALS,
    "capital": frozenset({"micro_reversion"}),
}


def venue_allowed_signals(venue: str) -> frozenset[str]:
    """Return the tick signals ``venue``'s WS feed can structurally compute.

    D3 routing: OKX (full depth + tape) → all three; Capital (price quotes only,
    sizes/tape zeroed by the canonical converter) → ``micro_reversion`` only (the
    price-based overshoot fade). An UNLISTED venue degrades to the FULL set
    (flow_not_block — never silence a venue we have not explicitly characterised).
    Intersected with the regime-active set in the loop, so the venue restriction
    and the regime gate compose (both must allow a signal for it to fire).
    """
    return TICK_ENGINE_VENUE_SIGNALS.get(venue, _ALL_TICK_SIGNALS)


def _env_truthy(env_value: str | None) -> bool:
    """True iff ``env_value`` is a truthy token (1/true/yes/on, case-insensitive)."""
    if env_value is None:
        return False
    return env_value.strip().lower() in _TRUTHY


def tick_engine_enabled() -> bool:
    """True iff the P5 tick-decision engine should be spawned by the loop.

    Gated ON by the ``TICK_ENGINE_ENABLED`` env (truthy). Default OFF so the
    engine is an explicit opt-in additive layer — the bar pipeline is unchanged
    until the operator turns it on (and can run shadow-first via
    ``TICK_ENGINE_SHADOW``). NOT a throttle — a feature flag for a new producer.
    """
    return _env_truthy(os.getenv("TICK_ENGINE_ENABLED"))


def tick_engine_owns_okx() -> bool:
    """True iff the tick engine OWNS Phase-1 OKX entries (bar pipeline yields).

    Coexistence flag (no double-trade): when the tick engine is enabled it is the
    sole opener of tick-eligible OKX symbols, so the bar entry path skips them
    (keyed by venue). Defaults to the same value as ``tick_engine_enabled`` —
    owning OKX only matters when the engine is actually running — but is
    independently overridable via ``TICK_ENGINE_OWNS_OKX`` so an operator can run
    the engine in shadow WITHOUT yet vacating the bar pipeline. The open-dedup is
    the always-on backstop; this flag prevents the two producers from racing the
    SAME symbol in the first place.
    """
    raw = os.getenv("TICK_ENGINE_OWNS_OKX")
    if raw is None or raw == "":
        return tick_engine_enabled()
    return _env_truthy(raw)


def _env_float(env_value: str | None, default: float) -> float:
    """Parse a positive float from an env token; ``default`` on miss / non-positive.

    Used for the re-tunable tick thresholds (the honest-slate re-measure can
    re-aim ``theta_ofi`` / ``theta_revert`` without a redeploy). A blank / unset /
    unparseable / non-positive token keeps the AGGRESSIVE coded default — the env
    is an opt-in re-tune knob, never a way to silently zero a threshold.
    """
    if env_value is None or env_value.strip() == "":
        return default
    try:
        val = float(env_value)
    except (TypeError, ValueError):
        return default
    return val if val > 0.0 else default


def env_shadow_enabled(env_value: str | None) -> bool:
    """Return True iff ``env_value`` is a truthy ``TICK_ENGINE_SHADOW`` token.

    Pure helper (the env read is injected) so the parse is testable. Truthy =
    ``1``/``true``/``yes``/``on`` case-insensitive; everything else (incl. None
    and empty) is False → default is **live**, not shadow. The operator opts
    *into* shadow; the engine does not silently mute itself.
    """
    if env_value is None:
        return False
    return env_value.strip().lower() in _TRUTHY


@dataclass(frozen=True, slots=True)
class TickEngineConfig:
    """All tunables for the tick-decision engine (AGGRESSIVE defaults).

    Thresholds (low = sensitive = fires more — AGGRESSIVE bias):
      - ``theta_burst``  (θ_b): burst_z above which ``burst_rider`` arms.
      - ``theta_ofi``    (θ_o): |ofi| above which ``flow_pressure`` arms.
      - ``theta_revert`` (θ_r): |overshoot_z| above which ``micro_reversion`` arms.
      - ``theta_spread`` (θ_s): max spread (bps) ``burst_rider`` will cross.

    EWMA spans (seconds) — the 1/3/10s microstructure horizons:
      - ``ewma_fast_sec``  (~1s): aggr_flow / ofi reactivity.
      - ``ewma_mid_sec``   (~3s): mid baseline for overshoot.
      - ``ewma_slow_sec``  (~10s): velocity baseline for burst_z.

    Freshness / liveness:
      - ``fresh_sec``: a feature window older than this (newest tick age) yields
        safe-sentinel features — "실시간 들어오는 애만 거래".
      - ``min_ticks``: below this the window is too thin to compute z-scores →
        safe-sentinel (no manufactured signal).

    Loop knobs (read by the impure integration loop, not the pure modules):
      - ``cooldown_sec``: per (symbol × signal) re-fire suppression.
      - ``ring_depth``: feature-window depth the loop requests.
      - ``shadow``: log-only when True (env ``TICK_ENGINE_SHADOW``).

    Conviction shaping:
      - ``conviction_cap``: max conviction a single trigger maps to (≤ 1.0).
      - ``conviction_ref_*``: trigger magnitude (above its θ) that saturates
        conviction to ``conviction_cap``. Lower ref = conviction ramps faster.
    """

    # --- trigger thresholds (AGGRESSIVE: low = sensitive) -----------------
    theta_burst: float = 1.5  # θ_b — burst_z (z-score units)
    # θ_o — |ofi| (signed imbalance, -1..1). RE-AIMED 0.20 → 0.32 → 0.40 to the
    # cost-clearing fat tail (honest fee-net diagnosis w02ccvq0q + the second
    # re-measure: at 0.32 the per-trade net climbed −0.858 → −0.282 over 178
    # trades but gross 0.861 still trailed fee 1.143 = STILL fee-negative, so the
    # 0.32 bar still fired on imbalances whose gross edge did not clear the
    # ~20bps round-trip taker cost). 0.40 fires ONLY on the FATTEST/most-
    # persistent imbalances whose gross edge beats that cost — pushing net/trade
    # above 0. NOT a defensive dampen: flow_pressure STILL fires bidirectionally
    # on every real LARGE imbalance (long on bid-heavy, short on ask-heavy) — it
    # just stops firing on the sub-cost band. Named env-tunable
    # (``POLARIS_TICK_THETA_OFI``) so it can be re-aimed after each re-measure
    # WITHOUT a redeploy.
    theta_ofi: float = field(
        default_factory=lambda: _env_float(os.getenv("POLARIS_TICK_THETA_OFI"), 0.40)
    )
    # θ_r — |overshoot_z| (z-score units). RE-AIMED 1.5 → 2.0 to the cost-clearing
    # fat tail (honest fee-net diagnosis: micro_reversion's gross edge is POSITIVE
    # +$19.93 but net −$13.54 over 79 closes = the SAME fee>edge pattern as
    # flow_pressure — the old 1.5 bar fired on mean-reversion extremes too shallow
    # to clear the ~20bps round-trip cost). 2.0 fires ONLY on the FATTER overshoots
    # whose snap-back beats that cost. NOT a defensive dampen: micro_reversion
    # STILL fades bidirectionally on every real LARGE overshoot (short an up-
    # overshoot, long a down-overshoot) — it just stops firing on the sub-cost
    # band. Named env-tunable (``POLARIS_TICK_THETA_REVERT``) so it can be re-aimed
    # after the next re-measure WITHOUT a redeploy.
    theta_revert: float = field(
        default_factory=lambda: _env_float(
            os.getenv("POLARIS_TICK_THETA_REVERT"), 2.0
        )
    )
    theta_spread: float = 8.0  # θ_s — max spread (bps) for burst entry

    # --- EWMA horizons (seconds) -----------------------------------------
    ewma_fast_sec: float = 1.0
    ewma_mid_sec: float = 3.0
    ewma_slow_sec: float = 10.0

    # --- freshness / sufficiency -----------------------------------------
    fresh_sec: float = 35.0  # newest-tick age ceiling (spec: fresh < 35s)
    min_ticks: int = 8  # below this → safe-sentinel features

    # --- flow_pressure ENTRY follow-through confirmation ([[flow_pressure_
    #     calibration_ai_2026-06-23]]) ------------------------------------
    # The OFI-momentum entry was buying the TOP of an OFI-positive spike that
    # immediately reversed (59% of trades never reached +0.2R — exhaustion
    # mistaken for continuation). The fix is TIMING precision, NOT a veto /
    # size-cut: do NOT enter on the raw spike — require POST-SPIKE confirmation
    # over ``confirm_ticks`` (≈1.5–3s at 500ms) that the move is real follow-
    # through (OFI stays elevated / re-accelerates, the bid follows up /
    # replenishes, the microprice HOLDS above the spike midpoint, spread is
    # stable/tightening). An unconfirmed tick simply DELAYS to a later, confirmed
    # tick (the symbol is re-evaluated every cadence) — flow_not_block. Raising
    # the effective bar "moderately": the sustained-OFI floor below requires the
    # imbalance to PERSIST across the tail above ``confirm_ofi_frac × θ_o`` (more
    # than a single EWMA tick clearing θ_o), without disturbing the documented
    # fee-net θ_o=0.40 constant. AGGRESSIVE: the confirm is a brief delay, never a
    # block — a genuine follow-through still fires bidirectionally.
    confirm_ticks: int = 4  # N post-spike ticks the follow-through is read over
    confirm_ofi_frac: float = 0.6  # tail OFI must hold this fraction of θ_o

    # --- flow_pressure EXIT precision (MFE protect schedule) -------------
    # Once favourable excursion appears, tighten protection so the +0.67R avg MFE
    # is captured and the 17% >0.5R give-back is cut (late exit). At ``mfe_bep_r``
    # the stop leaves initial risk (ratchets toward BEP); at ``mfe_protect_r`` it
    # locks ``mfe_protect_lock_r`` of positive R. EXPECTANCY (a tighter precise
    # exit once green), NOT a throttle: it only RATCHETS the protective stop toward
    # profit on top of the let-winners-run ATR trail — size / entry / the G6 -1.0R
    # rail untouched. Env-tunable; flow_pressure-only (threaded per-position).
    mfe_bep_r: float = 0.35  # MFE at which the stop ratchets to break-even
    mfe_protect_r: float = 0.50  # MFE at which a meaningful positive R is locked
    mfe_protect_lock_r: float = 0.25  # R locked in once mfe_protect_r is reached

    # --- loop knobs (impure loop reads these) ----------------------------
    cooldown_sec: float = 5.0
    ring_depth: int = 600
    shadow: bool = field(default_factory=lambda: env_shadow_enabled(os.getenv("TICK_ENGINE_SHADOW")))

    # --- conviction shaping ----------------------------------------------
    conviction_cap: float = 1.0
    conviction_ref_burst: float = 3.0  # burst_z this far past θ_b saturates
    # |ofi| this far past θ_o saturates conviction to the cap. With θ_o re-aimed
    # to 0.40 the saturation point now sits at |ofi| ≈ 1.0 — conviction tracks a
    # COST-CLEARING expected move (a bigger, more-persistent imbalance), not the
    # old sub-cost ramp. Env-tunable (``POLARIS_TICK_CONVICTION_REF_OFI``).
    conviction_ref_ofi: float = field(
        default_factory=lambda: _env_float(
            os.getenv("POLARIS_TICK_CONVICTION_REF_OFI"), 0.6
        )
    )
    conviction_ref_revert: float = 3.0  # |overshoot_z| this far past θ_r saturates
