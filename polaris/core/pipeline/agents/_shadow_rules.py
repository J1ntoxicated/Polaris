"""AI-conductor P0 SHADOW — deterministic technical rules (G3 + G4).

These pure functions compute a technical decision that runs IN PARALLEL with the
live GPT gate. In P0/SHADOW they NEVER drive the pipeline (behavior 0); the
caller logs the technical decision against the GPT decision so the acceptance
gate (next session) can decide a cutover.

Design SSOT: ``.claude/plans/ai_conductor_architecture_2026-05-30.md`` +
``vault/50_research/debates/ai_conductor_transition_2026-05-30.md``.

Codex BLOCKING constraints baked in (non-negotiable, aggressive bias preserved):

- G3 cold cell (``n_eff < CELL_WARM_MIN_N_EFF``) = PASS-through ALWAYS
  ("모호하면 통과"). Only a WARM cell may KILL, and only on a narrow rule
  (bottom quartile AND losing avg_pnl_r). Cell-independent boosters (recent
  same-symbol consecutive losses / wide spread / fresh listing) only REINFORCE
  an already-warm KILL — they never KILL a cold cell on their own.
- G4 PROCEED default; KILL ONLY on a stale / crossed book. Wide spread and
  adverse drift are FLAGS (surfaced for calibration), never KILL. realized-vol
  is NOT an input at all ("expanding vol = opportunity" thesis).
- ``net_edge_r`` is NEVER consulted by either rule (it is a self-declared
  placeholder, "NOT alpha, Do not trade off this number").

The deterministic KILL set is, by construction, a SUBSET of what the slow GPT
path could reject (cold = pass-through, spread/drift = flag) so a future cutover
preserves flow_not_block — the technical layer kills LESS than GPT, never more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from polaris.core.pipeline.gate_state import GateDecision

__all__ = [
    "CELL_WARM_MIN_N_EFF",
    "G3ShadowInputs",
    "G4ShadowInputs",
    "MODIFY_CONSERVATIVE_SCALAR",
    "ShadowDecision",
    "SPREAD_WIDE_MULT",
    "STALE_TICK_MAX_SEC",
    "g3_shadow_inputs_from_payload",
    "g4_shadow_inputs_from_payload",
    "technical_validate_decision",
    "technical_watch_decision",
]

# Cell warmth threshold — mirrors ``payload_builder.CELL_POOL_MIN_N_EFF`` (the
# quartile-eligibility floor). Below this, the cell is COLD → pass-through.
CELL_WARM_MIN_N_EFF: Final[float] = 5.0

# Conservative MODIFY scalar applied to warm-mid / cold-quartile cells. Inside
# the GPT G3 MODIFY range [0.5, 1.5] and <= 1.0 (a mild trim, never an amplify).
MODIFY_CONSERVATIVE_SCALAR: Final[float] = 0.85

# Consecutive same-symbol losses that REINFORCE a warm KILL (booster only).
RECENT_LOSS_STREAK_FOR_KILL: Final[int] = 3

# G4 staleness bound — a top-of-book older than this is "stale" → KILL. 30s is
# the G4 watch window; double it for the freshness bound.
STALE_TICK_MAX_SEC: Final[float] = 60.0

# G4 spread-vs-baseline FLAG multiplier (flag only — never a KILL).
SPREAD_WIDE_MULT: Final[float] = 3.0

# G4 adverse drift FLAG threshold in bps (flag only — never a KILL).
DRIFT_FLAG_BPS: Final[float] = 100.0


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    """Result of a deterministic technical rule (shadow, never live in P0)."""

    decision: GateDecision
    scalar: float = 1.0
    reason: str = ""
    flags: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# G3 — Signal Validator technical rule
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class G3ShadowInputs:
    """Inputs for the G3 technical rule (all from the existing G3 payload).

    Deliberately does NOT carry ``net_edge_r`` — it must not gate (codex
    BLOCKING). ``quartile`` is the cell-routing label; ``n_eff`` the cell's
    effective sample size (cold < warm threshold). ``recent_trades`` /
    ``spread_bps`` / ``listing_age_hours`` are cell-independent booster inputs.
    """

    n_eff: float
    quartile: str
    avg_pnl_r: float
    recent_trades: list[dict[str, Any]] = field(default_factory=list)
    spread_bps: float = 0.0
    baseline_p50_spread_bps: float = 0.0
    listing_age_hours: float = 0.0


def _recent_loss_streak(recent_trades: list[dict[str, Any]]) -> int:
    """Leading run of consecutive losses in the most-recent same-symbol trades.

    ``recent_trades`` is ordered most-recent-first (matches
    ``load_recent_same_symbol_trades``). A trade is a loss when ``won`` is falsy.
    """
    streak = 0
    for t in recent_trades:
        if bool(t.get("won", False)):
            break
        streak += 1
    return streak


def technical_validate_decision(inp: G3ShadowInputs) -> ShadowDecision:
    """G3 deterministic technical rule — PASS default, narrow warm KILL only.

    Rules (in order):
    1. COLD cell (``n_eff < CELL_WARM_MIN_N_EFF``) OR quartile label ``cold`` →
       NEVER KILL. A losing cold-quartile label gets a conservative MODIFY; an
       otherwise-cold cell passes through ("모호하면 통과").
    2. WARM cell AND quartile == 'bottom' AND avg_pnl_r < 0 → narrow KILL.
       Cell-independent boosters (>= 3 consecutive same-symbol losses) only add
       to the reason of an already-qualifying warm KILL.
    3. WARM mid quartile → conservative MODIFY (mild trim, never amplify).
    4. Otherwise (warm top / warm bottom-but-not-losing) → PASS.

    ``net_edge_r`` is never consulted (not even an input).
    """
    quartile = inp.quartile.lower()
    warm = inp.n_eff >= CELL_WARM_MIN_N_EFF

    # Rule 1 — cold cell / cold-quartile label = pass-through (no KILL ever).
    if quartile == "cold":
        # A cold-labelled quartile with losing history → conservative trim, not KILL.
        if inp.avg_pnl_r < 0.0:
            return ShadowDecision(
                decision=GateDecision.MODIFY,
                scalar=MODIFY_CONSERVATIVE_SCALAR,
                reason="cold_quartile_conservative_modify",
            )
        return ShadowDecision(decision=GateDecision.PASS, reason="cold_quartile_pass")
    if not warm:
        return ShadowDecision(decision=GateDecision.PASS, reason="cold_cell_passthrough")

    # Rule 2 — warm narrow KILL (bottom quartile AND losing).
    if quartile == "bottom" and inp.avg_pnl_r < 0.0:
        streak = _recent_loss_streak(inp.recent_trades)
        booster = (
            f"+loss_streak={streak}" if streak >= RECENT_LOSS_STREAK_FOR_KILL else ""
        )
        return ShadowDecision(
            decision=GateDecision.KILL,
            scalar=0.0,
            reason=f"warm_bottom_losing{booster}",
        )

    # Rule 3 — warm mid quartile = conservative MODIFY.
    if quartile == "mid":
        return ShadowDecision(
            decision=GateDecision.MODIFY,
            scalar=MODIFY_CONSERVATIVE_SCALAR,
            reason="warm_mid_conservative_modify",
        )

    # Rule 4 — warm top / warm bottom-but-not-losing → PASS.
    return ShadowDecision(decision=GateDecision.PASS, reason="warm_pass")


def g3_shadow_inputs_from_payload(payload: dict[str, Any]) -> G3ShadowInputs:
    """Project the live G3 ``ctx.payload`` onto the technical-rule inputs."""
    cell = payload.get("cell_routing", {})
    cell = cell if isinstance(cell, dict) else {}
    recent = payload.get("recent_trades", [])
    recent = recent if isinstance(recent, list) else []
    return G3ShadowInputs(
        n_eff=float(cell.get("n_eff", 0.0) or 0.0),
        quartile=str(cell.get("quartile", "mid")),
        avg_pnl_r=float(cell.get("avg_pnl_r", 0.0) or 0.0),
        recent_trades=recent,
        spread_bps=float(payload.get("spread_bps", 0.0) or 0.0),
        baseline_p50_spread_bps=float(
            payload.get("baseline_p50_spread_bps", 0.0) or 0.0
        ),
        listing_age_hours=float(payload.get("listing_age_hours", 0.0) or 0.0),
    )


# ---------------------------------------------------------------------------
# G4 — Pre-Entry Watcher technical rule
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class G4ShadowInputs:
    """Inputs for the G4 technical rule (from tick window + spread baseline).

    Deliberately carries NO realized-vol field (codex BLOCKING B — vol expansion
    is opportunity, never a KILL) and NO ``net_edge_r``. ``best_bid`` / ``best_ask``
    + ``last_tick_age_sec`` drive the only KILL paths (crossed / stale book).
    ``spread_bps`` / ``drift_bps`` only raise FLAGS.
    """

    best_bid: float
    best_ask: float
    last_tick_age_sec: float
    spread_bps: float = 0.0
    baseline_p50_spread_bps: float = 0.0
    drift_bps: float = 0.0


def technical_watch_decision(inp: G4ShadowInputs) -> ShadowDecision:
    """G4 deterministic technical rule — PROCEED default, KILL only stale/crossed.

    KILL (microstructure broken) only when:
    - the book is CROSSED (best_bid >= best_ask, both positive), or
    - the top-of-book is STALE (last_tick_age_sec > STALE_TICK_MAX_SEC).

    Wide spread (spread_bps > baseline * SPREAD_WIDE_MULT) and adverse drift
    (|drift_bps| > DRIFT_FLAG_BPS) raise FLAGS but the decision stays PROCEED
    (codex C — flag default, KILL promotion deferred to a calibrated cutover).
    realized-vol is not an input (codex B). PROCEED carries any flags.
    """
    # KILL — crossed book (positive prices, bid at/above ask).
    if inp.best_bid > 0.0 and inp.best_ask > 0.0 and inp.best_bid >= inp.best_ask:
        return ShadowDecision(decision=GateDecision.KILL, reason="crossed_book")

    # KILL — stale top-of-book.
    if inp.last_tick_age_sec > STALE_TICK_MAX_SEC:
        return ShadowDecision(decision=GateDecision.KILL, reason="stale_book")

    # FLAG-only (never KILL).
    flags: list[str] = []
    if (
        inp.baseline_p50_spread_bps > 0.0
        and inp.spread_bps > inp.baseline_p50_spread_bps * SPREAD_WIDE_MULT
    ):
        flags.append("spread_wide")
    if abs(inp.drift_bps) > DRIFT_FLAG_BPS:
        flags.append("drift")

    return ShadowDecision(
        decision=GateDecision.PROCEED,
        reason="proceed" if not flags else "proceed_flagged",
        flags=tuple(flags),
    )


def _last_tick_age_sec(tick_window: list[dict[str, Any]], now_ts: int | float) -> float:
    """Age (sec) of the most-recent tick; large sentinel when empty/unknown."""
    if not tick_window:
        # No tick stream supplied (P0 payloads pass tick_window=[]). Treat as
        # "unknown freshness", NOT stale — return 0.0 so the absence of a tick
        # stream never manufactures a stale KILL (behavior-safe shadow).
        return 0.0
    last = tick_window[-1]
    ts = last.get("ts")
    if ts is None:
        return 0.0
    try:
        return max(0.0, float(now_ts) - float(ts))
    except (TypeError, ValueError):
        return 0.0


def g4_shadow_inputs_from_payload(
    payload: dict[str, Any], *, now_ts: int | float
) -> G4ShadowInputs:
    """Project the live G4 ``ctx.payload`` onto the technical-rule inputs."""
    tick_window = payload.get("tick_window", [])
    tick_window = tick_window if isinstance(tick_window, list) else []
    best_bid = 0.0
    best_ask = 0.0
    drift_bps = 0.0
    if tick_window:
        last = tick_window[-1]
        best_bid = float(last.get("bid", 0.0) or 0.0)
        best_ask = float(last.get("ask", 0.0) or 0.0)
        first = tick_window[0]
        first_mid = float(first.get("mid", 0.0) or 0.0)
        last_mid = float(last.get("mid", 0.0) or 0.0)
        if first_mid > 0.0:
            drift_bps = (last_mid - first_mid) / first_mid * 10_000.0
    return G4ShadowInputs(
        best_bid=best_bid,
        best_ask=best_ask,
        last_tick_age_sec=_last_tick_age_sec(tick_window, now_ts),
        spread_bps=float(payload.get("spread_bps", 0.0) or 0.0),
        baseline_p50_spread_bps=float(
            payload.get("baseline_p50_spread_bps", 0.0) or 0.0
        ),
        drift_bps=drift_bps,
    )
