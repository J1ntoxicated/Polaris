"""Tick-engine in-mem state + loop-stall threshold (extracted, move-only).

Split out of ``_production_tick_engine`` to keep each module ≤500 LOC. This
holds ONLY the engine-owned dataclass carried across loop iterations and the
stall-slack constant — no behaviour, pure data. The orchestrator
(``_production_tick_engine``) re-exports ``TickEngineState`` so every existing
import (``from polaris.scripts._production_tick_engine import TickEngineState``)
keeps working byte-for-byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from polaris.core.ticks.config import TickEngineConfig

# Loop-heartbeat stall threshold: an iteration gap beyond ``cadence + this``
# means the loop was starved (e.g. a multi-minute learner full-DB backup /
# baseline rescan on the shared conn) and is logged as a WARNING on resume.
STALL_SLACK_SEC: float = 1.0


@dataclass(slots=True)
class TickEngineState:
    """Engine-owned in-mem state (no DB) carried across loop iterations.

    ``cooldowns`` suppresses a re-fire of the same (symbol, signal_id) inside
    ``cfg.cooldown_sec`` (per-symbol×signal spam guard). ``family_by_position``
    records each opened position's ``signal_family`` so the per-tick exit routes
    momentum positions to the ATR-trail and reversion positions to the scalp
    exit. ``entry_ref_by_position`` is the mid the scalp exit measures its small
    flow reversal / R target against. ``decisions`` / ``orders`` / ``skips`` are
    telemetry only.
    """

    cfg: TickEngineConfig = field(default_factory=TickEngineConfig)
    cooldowns: dict[tuple[str, str], float] = field(default_factory=dict)
    family_by_position: dict[str, str] = field(default_factory=dict)
    entry_ref_by_position: dict[str, float] = field(default_factory=dict)
    # Consecutive-broken tick count per position for the adaptive-thesis SUSTAINED
    # gate: a single noisy tick must not flip a fresh winner to BROKEN, so the
    # re-map only treats the thesis as broken once the streak clears the floor.
    # Incremented when this tick's micro signal opposes the position, reset to 0
    # otherwise; popped on close (alongside family/entry_ref).
    thesis_broken_streak_by_position: dict[str, int] = field(default_factory=dict)
    decisions: int = 0
    orders: int = 0
    shadow_logs: int = 0
    skips_stale: int = 0
    skips_dedup: int = 0
    skips_cooldown: int = 0
    drops_short: int = 0
    drops_sizing: int = 0
    scalp_exits: int = 0
    # --- eval telemetry (diagnostic: WHY signals do / don't fire) ---------
    # ``evaluated`` = symbols that passed freshness+dedup and reached feature
    # eval; ``thin`` = of those, windows too thin/stale (None features → cannot
    # fire); ``dry`` = sufficient features but no signal armed. The ``max_*`` are
    # peak feature magnitudes seen since the last telemetry emit (reset each
    # interval) so the operator can see how close features get to the thresholds.
    evaluated: int = 0
    thin: int = 0
    dry: int = 0
    max_n_ticks: int = 0
    max_burst_z: float = 0.0
    max_abs_ofi: float = 0.0
    max_abs_overshoot: float = 0.0
    tel_mono: float = 0.0
    # --- loop heartbeat (stall visibility) --------------------------------
    # ``loop_count`` increments every iteration; ``last_loop_mono`` is the
    # monotonic stamp at the top of the previous iteration. A gap between
    # successive ``last_loop_mono`` values that exceeds the cadence by more
    # than ``STALL_SLACK_SEC`` means the loop was starved (e.g. a 3-min
    # learner full-DB backup / baseline 7d rescan on the shared conn) and is
    # surfaced as a WARNING the moment the loop resumes.
    loop_count: int = 0
    last_loop_mono: float = 0.0
