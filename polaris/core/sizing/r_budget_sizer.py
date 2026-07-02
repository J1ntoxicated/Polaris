"""Layer 3 — R-budget qty-aimed sizing + strength_scalar fold (Wave B debate).

Spec source: vault/50_research/debates/waveB_sizing_params_2026-07-02.md
(agenda ① R-budget sizing, agenda ② strength_scalar → T4).

DEMO/PAPER only. Aggressive bias preserved — flow_not_block, no defensive
throttle. 9-stack ban: this module adds ZERO new multiplier slots. Agenda ②
folds strength_scalar into the SAME single continuous-scalar clamp (one
``clamp()`` call, not a second mult applied to an already-clamped value).
Agenda ① replaces the %-of-equity notional chain with a direct qty
computation; every existing cap is converted to a qty ceiling and reduced via
ONE ``min()`` — no new cap slot, no stacked shrink.

Shadow-verify rollout (agenda ①): the new qty path runs in SHADOW (computed +
logged, but the existing %-of-equity T4 path still places the order) until
either ``POLARIS_RBUDGET_SHADOW_N`` sizing decisions have been observed or
``POLARIS_RBUDGET_SHADOW_HOURS`` have elapsed since the first observation —
whichever comes first — at which point the mode flips to ACTIVE permanently
(persisted so a restart does not re-run the shadow window). Operators can
force the mode via ``POLARIS_RBUDGET_SIZING_MODE=shadow|active|off``.
"""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from typing import Final, Literal

from polaris.core.sizing.schema import CONT_SCALAR_MAX, CONT_SCALAR_MIN

__all__ = [
    "CAP_DOMINATED_REALIZATION_THRESHOLD",
    "SHADOW_FLIP_HOURS_DEFAULT",
    "SHADOW_FLIP_N_DEFAULT",
    "CapQtyInputs",
    "RBudgetSizingResult",
    "ShadowState",
    "cap_dominated",
    "compute_r_budget_qty",
    "fold_strength_scalar",
    "qty_from_r_budget",
    "read_shadow_state",
    "record_shadow_observation",
    "resolve_sizing_mode",
    "stop_dist_usd",
    "target_realization",
    "qty_headroom_min",
]

SizingMode = Literal["shadow", "active", "off"]

# Target realization below this fraction of the intended (R_budget × T4_mult)
# risk is tagged CAP_DOMINATED — log-only, excluded from R-calibration /
# property-test population (measurement-integrity flag, never blocks the order).
CAP_DOMINATED_REALIZATION_THRESHOLD: Final[float] = 0.33

SHADOW_FLIP_N_DEFAULT: Final[int] = 100
SHADOW_FLIP_HOURS_DEFAULT: Final[float] = 24.0

_ENV_MODE: Final[str] = "POLARIS_RBUDGET_SIZING_MODE"
_ENV_SHADOW_N: Final[str] = "POLARIS_RBUDGET_SHADOW_N"
_ENV_SHADOW_HOURS: Final[str] = "POLARIS_RBUDGET_SHADOW_HOURS"

_SHADOW_DDL: Final[str] = """
CREATE TABLE IF NOT EXISTS r_budget_shadow_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    n_observed      INTEGER NOT NULL DEFAULT 0,
    first_ts        INTEGER,
    flipped_active  INTEGER NOT NULL DEFAULT 0
);
"""


# ---------------------------------------------------------------------------
# Agenda ② — strength_scalar fold (single clamp, no new mult slot)
# ---------------------------------------------------------------------------


def fold_strength_scalar(raw_cont_preclip: float, strength_scalar: float = 1.0) -> float:
    """``cont_final = clamp(raw_cont_preclip × strength_scalar, MIN, MAX)``.

    ``raw_cont_preclip`` MUST be the pre-clip continuous value (the value
    before any existing ``[CONT_SCALAR_MIN, CONT_SCALAR_MAX]`` clamp is
    applied) — multiplying an ALREADY-clamped value would lose information
    asymmetrically at the 0.75/1.50 boundary (Wave B R2 BREAK #2). This is
    the SAME single continuous-scalar slot as the regime-fit / judge_conviction
    folds in ``engine.compute_size`` — clamp happens exactly once, here.

    ``strength_scalar`` absent / non-MODIFY / non-finite → defaults to 1.0
    (byte-identical fold, no-op). Never zero (anti-collapse floor is
    ``CONT_SCALAR_MIN``).
    """
    s = strength_scalar if math.isfinite(strength_scalar) and strength_scalar > 0.0 else 1.0
    if not math.isfinite(raw_cont_preclip):
        raw_cont_preclip = CONT_SCALAR_MIN
    return max(CONT_SCALAR_MIN, min(CONT_SCALAR_MAX, raw_cont_preclip * s))


# ---------------------------------------------------------------------------
# Agenda ① — R-budget qty-aimed sizing
# ---------------------------------------------------------------------------


def stop_dist_usd(*, entry_price: float, atr_pct: float, stop_atr_mult: float) -> float:
    """Fee-floored stop distance in USD per unit — SAME source as risk_unit.py.

    ``stop_dist = entry_price × atr_pct × stop_atr_mult``. Reuses the exact
    formula ``risk_usd_at_entry`` anchors on (no new stop-distance definition
    — spec mandate: "발명 금지"). Non-finite / non-positive inputs return 0.0
    (data-integrity — caller routes to the existing %-of-equity fallback
    path, this is not a sizing judgment).
    """
    if entry_price <= 0.0 or atr_pct <= 0.0 or stop_atr_mult <= 0.0:
        return 0.0
    if not all(math.isfinite(x) for x in (entry_price, atr_pct, stop_atr_mult)):
        return 0.0
    return entry_price * atr_pct * stop_atr_mult


def qty_from_r_budget(*, r_budget_usd: float, t4_mult: float, stop_dist_usd_per_unit: float) -> float:
    """PRIMARY qty: ``qty_risk = R_budget × T4_mult ÷ stop_dist_usd``.

    ``t4_mult`` is the full existing T4 continuous×tier×cell×listing×learner
    product (unchanged composition — this function only changes the
    DENOMINATOR the budget is expressed in, from %-of-equity to qty).
    ``stop_dist_usd_per_unit`` == 0 / non-finite / negative inputs → 0.0
    (data-integrity fallback, caller must route to the existing path — ATR
    stale/zero is an error condition, not a sizing decision).
    """
    if stop_dist_usd_per_unit <= 0.0 or r_budget_usd < 0.0 or t4_mult < 0.0:
        return 0.0
    if not all(math.isfinite(x) for x in (r_budget_usd, t4_mult, stop_dist_usd_per_unit)):
        return 0.0
    return (r_budget_usd * t4_mult) / stop_dist_usd_per_unit


@dataclass(frozen=True, slots=True)
class CapQtyInputs:
    """Every existing exposure cap, pre-converted to a qty ceiling (units).

    Each field is ``cap_pct_of_equity_or_margin × equity_or_margin_basis /
    price`` computed by the CALLER (this module does not know equity/margin
    basis per cap type — it only performs the single min() reduction).
    ``None`` means "not configured / not binding" and is excluded from the
    min(), mirroring ``headroom_min``'s ``None``-skip semantics.
    """

    single_trade_qty: float
    per_symbol_qty: float
    margin_qty: float  # avail_margin × leverage / price
    underlying_qty: float | None = None
    cluster_qty: float | None = None
    track_qty: float | None = None
    venue_daily_qty: float | None = None
    total_daily_qty: float | None = None
    env_ceiling_qty: float | None = None


def qty_headroom_min(proposed_qty: float, caps: CapQtyInputs) -> tuple[float, str]:
    """Single ``min()`` over the PRIMARY qty + every qty-converted cap.

    Returns ``(final_qty, binding_cap_name)``, mirroring ``headroom_min``'s
    contract exactly — one clip slot, no stacked shrink (9-stack ban).
    """
    candidates: list[tuple[str, float]] = [
        ("proposed", proposed_qty),
        ("single_trade", caps.single_trade_qty),
        ("per_symbol", caps.per_symbol_qty),
        ("margin", caps.margin_qty),
    ]
    for name, val in (
        ("underlying", caps.underlying_qty),
        ("cluster", caps.cluster_qty),
        ("track", caps.track_qty),
        ("venue_daily", caps.venue_daily_qty),
        ("total_daily", caps.total_daily_qty),
        ("env_ceiling", caps.env_ceiling_qty),
    ):
        if val is not None:
            candidates.append((name, val))
    final_name, final_val = min(candidates, key=lambda c: c[1])
    return max(0.0, final_val), final_name


def target_realization(
    *, qty_after_caps: float, stop_dist_usd_per_unit: float, r_budget_usd: float, t4_mult: float
) -> float:
    """``staked_risk / intended_risk`` — the CAP_DOMINATED measurement.

    ``staked_risk = qty_after_caps × stop_dist_usd_per_unit``,
    ``intended_risk = R_budget × t4_mult``. Returns 0.0 when the intended
    risk is non-positive (undefined ratio → treat as fully cap-dominated by
    the caller's threshold check, never a crash).
    """
    intended = r_budget_usd * t4_mult
    if intended <= 0.0 or not math.isfinite(intended):
        return 0.0
    staked = qty_after_caps * stop_dist_usd_per_unit
    return staked / intended


def cap_dominated(realization: float) -> bool:
    """True when ``realization < CAP_DOMINATED_REALIZATION_THRESHOLD`` (0.33).

    Log-only tag — the order is NEVER altered by this flag. Consumers
    (R-calibration, property-test population) exclude tagged samples.
    """
    return realization < CAP_DOMINATED_REALIZATION_THRESHOLD


@dataclass(frozen=True, slots=True)
class RBudgetSizingResult:
    """Shadow-compute output bundle — logged alongside the legacy %-equity path."""

    qty_unclipped: float
    qty_after_caps: float
    binding_cap: str
    realization: float
    cap_dominated: bool


def compute_r_budget_qty(
    *,
    r_budget_usd: float,
    t4_mult: float,
    entry_price: float,
    atr_pct: float,
    stop_atr_mult: float,
    caps: CapQtyInputs,
) -> RBudgetSizingResult | None:
    """Full R-budget qty compute (PRIMARY + single min() + CAP_DOMINATED tag).

    Returns ``None`` on a data-integrity failure (stop_dist == 0 — ATR
    zero/stale) — the caller routes to the existing %-of-equity path and logs
    the fallback; this is never a sizing judgment.
    """
    stop = stop_dist_usd(entry_price=entry_price, atr_pct=atr_pct, stop_atr_mult=stop_atr_mult)
    if stop <= 0.0:
        return None
    qty_unclipped = qty_from_r_budget(
        r_budget_usd=r_budget_usd, t4_mult=t4_mult, stop_dist_usd_per_unit=stop
    )
    qty_after_caps, binding = qty_headroom_min(qty_unclipped, caps)
    realization = target_realization(
        qty_after_caps=qty_after_caps,
        stop_dist_usd_per_unit=stop,
        r_budget_usd=r_budget_usd,
        t4_mult=t4_mult,
    )
    return RBudgetSizingResult(
        qty_unclipped=qty_unclipped,
        qty_after_caps=qty_after_caps,
        binding_cap=binding,
        realization=realization,
        cap_dominated=cap_dominated(realization),
    )


# ---------------------------------------------------------------------------
# Shadow-verify rollout gate (persisted flip state)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ShadowState:
    n_observed: int
    first_ts: int | None
    flipped_active: bool


def _env_mode_override() -> SizingMode | None:
    import os

    raw = (os.environ.get(_ENV_MODE) or "").strip().lower()
    if raw in ("shadow", "active", "off"):
        return raw  # type: ignore[return-value]
    return None


def _env_shadow_n() -> int:
    import os

    raw = os.environ.get(_ENV_SHADOW_N)
    if raw is None or raw == "":
        return SHADOW_FLIP_N_DEFAULT
    try:
        return int(raw)
    except ValueError:
        return SHADOW_FLIP_N_DEFAULT


def _env_shadow_hours() -> float:
    import os

    raw = os.environ.get(_ENV_SHADOW_HOURS)
    if raw is None or raw == "":
        return SHADOW_FLIP_HOURS_DEFAULT
    try:
        return float(raw)
    except ValueError:
        return SHADOW_FLIP_HOURS_DEFAULT


def read_shadow_state(conn: sqlite3.Connection) -> ShadowState:
    """Read the persisted shadow-flip counters (creates the table if absent)."""
    conn.execute(_SHADOW_DDL)
    row = conn.execute(
        "SELECT n_observed, first_ts, flipped_active FROM r_budget_shadow_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return ShadowState(n_observed=0, first_ts=None, flipped_active=False)
    return ShadowState(n_observed=int(row[0]), first_ts=row[1], flipped_active=bool(row[2]))


def record_shadow_observation(conn: sqlite3.Connection, *, now_ts: int | None = None) -> ShadowState:
    """Increment the shadow observation counter and evaluate the flip condition.

    Flips to ACTIVE (permanently persisted) once ``n_observed >= shadow_n``
    OR ``hours_elapsed >= shadow_hours`` since the first observation —
    whichever comes first. Idempotent after flip (a flipped row is never
    un-flipped by this function).
    """
    ts = now_ts if now_ts is not None else int(time.time())
    conn.execute(_SHADOW_DDL)
    state = read_shadow_state(conn)
    if state.flipped_active:
        return state
    n_new = state.n_observed + 1
    first_ts = state.first_ts if state.first_ts is not None else ts
    elapsed_hours = (ts - first_ts) / 3600.0
    flip = n_new >= _env_shadow_n() or elapsed_hours >= _env_shadow_hours()
    conn.execute(
        """
        INSERT INTO r_budget_shadow_state (id, n_observed, first_ts, flipped_active)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            n_observed = excluded.n_observed,
            first_ts = excluded.first_ts,
            flipped_active = excluded.flipped_active
        """,
        (n_new, first_ts, 1 if flip else 0),
    )
    conn.commit()
    return ShadowState(n_observed=n_new, first_ts=first_ts, flipped_active=flip)


def resolve_sizing_mode(conn: sqlite3.Connection) -> SizingMode:
    """Resolve the active sizing mode: env override wins, else persisted flip state.

    ``off`` / ``active`` env forces that mode permanently (operator escape
    hatch). No env → ``shadow`` until the persisted state has flipped, then
    ``active`` forever (restart-safe — the flip is durable, not re-armed).
    """
    override = _env_mode_override()
    if override is not None:
        return override
    state = read_shadow_state(conn)
    return "active" if state.flipped_active else "shadow"
