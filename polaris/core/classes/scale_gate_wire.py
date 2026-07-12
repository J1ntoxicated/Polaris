"""fee-split v1 FLIP (item 3) — live SCALE-gate input resolvers.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). This
module is the (impure, DB-reading) bridge that assembles ``scale_gate.
evaluate_scale_gate``'s two live-only inputs — ``gross_lcb`` and
``best_case_friction`` — for one (venue, strategy_id) track, so
``polaris.core.sizing.engine.compute_size`` can call the gate with real data.
``evaluate_scale_gate`` / ``friction_floor.best_case_friction`` /
``gross_scorer.compute_gross_lcb`` all stay pure (no I/O) — this module is
the ONLY thing that touches the DB on this path.

Item 3 / R2 T2 fix ("EARN 2축"): the gate binds the TIER-AMPLIFIER BONUS
ONLY (``applied_tier_amp`` fallback to 1.0) — baseline sizing, entry
admission, and the gross_EARN survival label (transition.py, unaffected by
this module) are completely untouched (aggressive_always_profit /
no_block_filter_architecture / no_defensive_param_dampen).

Simplification (documented, not hidden): R2 item 3's time-decay window is
``H = max(6h, 12 x signal_period)``. ``polaris.core`` must not import UP into
``polaris.scripts`` (test_core_layering.py; strategy timeframe metadata lives
in ``polaris.scripts._production_atr``), so this module uses the spec's own
FLOOR (6h) for every track rather than re-deriving the per-strategy period —
never SHORTER than the spec's minimum window, only potentially less
churn-damped for slow (1D) strategies. Bonus-only impact (never baseline).
"""

from __future__ import annotations

import sqlite3
from typing import Final

from polaris.core.classes.friction_floor import best_case_friction
from polaris.core.classes.gross_scorer import GrossEdgeSample, compute_gross_lcb
from polaris.core.economics.fees import real_fee_bps

__all__ = [
    "CHARACTERISTIC_TIME_FLOOR_SECONDS",
    "EDGE_MARGIN_BPS",
    "GROSS_LCB_WINDOW",
    "SLIP_FLOOR_BPS",
    "SPREAD_P05_BPS",
    "resolve_best_case_friction",
    "resolve_gross_lcb",
]

# R2 item 1 floor — see module docstring simplification note.
CHARACTERISTIC_TIME_FLOOR_SECONDS: Final[float] = 6.0 * 3600.0

# No per-track spread/slippage telemetry pipeline exists yet — static,
# conservative generic floors (consistent with friction_floor.py's own test
# fixtures). Bonus-eligibility-only impact; a future refinement can source
# these from live spread telemetry once that pipeline exists.
SLIP_FLOOR_BPS: Final[float] = 2.0
SPREAD_P05_BPS: Final[float] = 5.0

# Small buffer above the friction floor before "proven" (measurement-noise
# margin) — no existing project constant for this; conservative default.
EDGE_MARGIN_BPS: Final[float] = 2.0

# How many recent score_f_events closes feed the LCB — mirrors the default
# strategy_class.window_w DDL default (same "one fixed lookback" precedent
# as tools/ops/probe_reranker.py's _F_TRACK_CAP_WINDOW_W).
GROSS_LCB_WINDOW: Final[int] = 20

# Strategy naming convention already used in this registry — a "_maker"
# suffix marks a resting/maker-capable strategy (e.g.
# weekend_funding_capitulation_maker, weekend_thin_book_flush_maker).
_MAKER_SUFFIX: Final[str] = "_maker"


def resolve_best_case_friction(*, venue: str, strategy_id: str) -> float:
    """Structural round-trip friction floor (bps of notional) for a
    (venue, strategy_id) track — see module docstring for the static
    slip/spread floor simplification."""
    maker_capable = strategy_id.endswith(_MAKER_SUFFIX)
    return best_case_friction(
        maker_capable=maker_capable,
        maker_floor_bps=real_fee_bps(venue, is_maker=True),
        taker_floor_bps=real_fee_bps(venue, is_maker=False),
        slip_floor_bps=SLIP_FLOOR_BPS,
        spread_p05_bps=SPREAD_P05_BPS,
    )


def resolve_gross_lcb(
    conn: sqlite3.Connection, *, venue: str, strategy_id: str, now_ts: int,
) -> float | None:
    """Winsorized weighted t-LCB over the track's last
    :data:`GROSS_LCB_WINDOW` ``score_f_events.score_contrib`` closes (the
    JUDGED axis — gross_bps by default, see score_f.py). ``None`` when no
    closes exist yet or N_eff has not cleared the cold-start floor
    (``gross_scorer.confidence_for_n_eff``) — the caller (``evaluate_scale_gate``)
    treats ``None`` as "not yet proven", the same conservative default as
    every other below-bar case."""
    rows = conn.execute(
        "SELECT score_contrib, closed_ts FROM score_f_events "
        "WHERE venue = ? AND strategy_id = ? ORDER BY closed_ts DESC LIMIT ?",
        (venue, strategy_id, GROSS_LCB_WINDOW),
    ).fetchall()
    samples = [GrossEdgeSample(value=float(s), closed_ts=int(ts)) for s, ts in rows]
    result = compute_gross_lcb(
        samples, now_ts=now_ts, characteristic_time_seconds=CHARACTERISTIC_TIME_FLOOR_SECONDS,
    )
    return result.lcb
