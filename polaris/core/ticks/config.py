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
    "TickEngineConfig",
    "env_shadow_enabled",
    "tick_engine_enabled",
    "tick_engine_owns_okx",
]

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# P5 coexistence SSOT: the venues the tick-decision engine OWNS. BOTH the engine
# (as ``PHASE1_VENUES``) and the bar entry path read THIS single frozenset, so
# the two producers can never drift out of sync (no double-trade).
# Empirical (2026-06-03): the DEMO OKX WS delivers near-zero ticks (~4 symbols,
# ~0.03 tick/s — simulated us.okx.com feed), too sparse to fill a feature window;
# Capital (CFD, bidirectional) streams a dense live tick feed → it is the
# live-tick-rich venue the microstructure engine needs (and Jin's most-active
# venue). So Phase 1 = {capital, okx}: Capital carries the signal, OKX fires only
# when its sparse feed happens to fill a window.
TICK_ENGINE_OWNED_VENUES: frozenset[str] = frozenset({"capital", "okx"})


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
    theta_ofi: float = 0.20  # θ_o — |ofi| (signed imbalance, -1..1)
    theta_revert: float = 1.5  # θ_r — |overshoot_z| (z-score units)
    theta_spread: float = 8.0  # θ_s — max spread (bps) for burst entry

    # --- EWMA horizons (seconds) -----------------------------------------
    ewma_fast_sec: float = 1.0
    ewma_mid_sec: float = 3.0
    ewma_slow_sec: float = 10.0

    # --- freshness / sufficiency -----------------------------------------
    fresh_sec: float = 35.0  # newest-tick age ceiling (spec: fresh < 35s)
    min_ticks: int = 8  # below this → safe-sentinel features

    # --- loop knobs (impure loop reads these) ----------------------------
    cooldown_sec: float = 5.0
    ring_depth: int = 600
    shadow: bool = field(default_factory=lambda: env_shadow_enabled(os.getenv("TICK_ENGINE_SHADOW")))

    # --- conviction shaping ----------------------------------------------
    conviction_cap: float = 1.0
    conviction_ref_burst: float = 3.0  # burst_z this far past θ_b saturates
    conviction_ref_ofi: float = 0.6  # |ofi| this far past θ_o saturates
    conviction_ref_revert: float = 3.0  # |overshoot_z| this far past θ_r saturates
