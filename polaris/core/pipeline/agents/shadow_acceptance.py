"""AI-conductor SHADOW — acceptance-metrics analysis (READ-ONLY).

These pure functions read the live shadow log (``gate_shadow_events``) + the
trade ledger (``positions`` / ``fills``) + the cell matrix (``cell_matrix_p0``)
and return plain dicts. They feed a FUTURE G3/G4 cutover decision
(``vault/50_research/debates/conductor_g3g4_cutover_2026-05-31.md`` follow-up
#1) and must be HONEST — every proxy / heuristic / conversion assumption is
labelled in-line and surfaced in the returned dict.

Hard constraints (non-negotiable):

- READ-ONLY. No INSERT/UPDATE/DELETE, no network. The live bot decision path is
  never touched — this is analysis, not a gate.
- No defensive throttle / entry block — these metrics MEASURE churn + cost; they
  do not act on it. ``fee_slippage_drag_r`` is a *measurement* of cost drag, not
  a skip rule (cf. ``net_edge.SKIP_ON_NEGATIVE_NET_EDGE = False``).

Schema reality (NOT what an idealised spec assumes):

- ``gate_shadow_events`` carries ``venue`` / ``symbol`` / ``regime`` /
  ``technical_decision`` / ``gpt_decision`` (empty when GPT errored / no client)
  / ``mismatch`` / ``cell_warm`` — but NO ``strategy`` column.
- ``positions`` has NO ``pnl_r`` and NO persisted ``loser_timeout`` close
  marker. ``exit_state`` is the FSM label (open/touched/protected/harvest), NOT
  a close reason. Realised PnL lives on the close ``fills`` row
  (``contribution_id = position_id``, ``is_close = 1``, ``pnl_usd``).

Conversion assumption (documented, single source): a $-PnL is projected to an
R-multiple by dividing by ``PNL_R_USD_DENOM`` ($50 risk-per-trade heuristic),
exactly mirroring ``payload_builder.load_recent_same_symbol_trades``. This is a
coarse constant-risk proxy — the true per-trade R-unit (``risk_usd``) is not yet
persisted (P1 Layer 5 R6), so the R figures are directional, not exact.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from typing import Any

from polaris.core.pipeline.gate_state import (
    GATE_PRE_ENTRY_WATCHER,
    GATE_SIGNAL_VALIDATOR,
)

__all__ = [
    "LOSER_TIMEOUT_SEC",
    "PNL_R_USD_DENOM",
    "GateAgreement",
    "chop_churn",
    "fee_adjusted_realized_r",
    "gate_agreement",
    "gpt_kill_counterfactual",
    "summarize",
]

# Mirrors ``payload_builder.PNL_R_USD_DENOM`` — the $-risk-per-trade heuristic
# used to project $-PnL into R. Constant-risk proxy (see module docstring).
PNL_R_USD_DENOM: float = 50.0

# Base stale-loser timeout (seconds) — mirrors
# ``exit_engine.EXIT_LOSER_TIMEOUT_SEC`` default. ``positions`` does not persist
# the close reason, so a loser-timeout close is *inferred* heuristically
# (negative realised pnl_r AND hold >= this bound). Documented in
# ``chop_churn``'s returned ``loser_timeout_heuristic`` note.
LOSER_TIMEOUT_SEC: float = 900.0

# Warm n_eff floor for the counterfactual cell join — mirrors
# ``payload_builder.CELL_POOL_MIN_N_EFF`` (= 5.0). Only cells with this much
# effective sample carry meaningful ``avg_pnl_r`` evidence, so the
# ``gpt_kill_counterfactual`` join restricts to warm cells; a KILL with no warm
# cell at/above this floor lands in ``no_match`` (honestly "no warm evidence",
# not a silent average over near-empty cold cells).
COUNTERFACTUAL_WARM_FLOOR_N_EFF: float = 5.0


def _pct(num: float, den: float) -> float:
    """Percent num/den, 0.0 when den == 0 (no division-by-zero, no NaN)."""
    return (num / den * 100.0) if den else 0.0


# ===========================================================================
# 1. gate_agreement
# ===========================================================================


@dataclass(frozen=True, slots=True)
class GateAgreement:
    """Counts for one bucket (a regime or the overall set)."""

    n: int
    gpt_kill: int
    technical_kill: int
    mismatch: int
    gpt_kill_pct: float
    technical_kill_pct: float
    mismatch_pct: float


def _agreement_bucket(rows: list[tuple[str, str, int]]) -> dict[str, Any]:
    """rows = [(technical_decision, gpt_decision, mismatch), ...] (gpt non-empty)."""
    n = len(rows)
    gpt_kill = sum(1 for _, g, _ in rows if g == "KILL")
    tech_kill = sum(1 for t, _, _ in rows if t == "KILL")
    mismatch = sum(1 for _, _, m in rows if m)
    return {
        "n": n,
        "gpt_kill": gpt_kill,
        "technical_kill": tech_kill,
        "mismatch": mismatch,
        "gpt_kill_pct": _pct(gpt_kill, n),
        "technical_kill_pct": _pct(tech_kill, n),
        "mismatch_pct": _pct(mismatch, n),
    }


def gate_agreement(conn: sqlite3.Connection, gate_id: int) -> dict[str, Any]:
    """Per-regime + overall GPT-vs-technical KILL agreement for one gate.

    Rows with an empty ``gpt_decision`` (GPT error / no client) are EXCLUDED —
    an agreement figure is only meaningful where a GPT decision exists.

    Returns ``{"gate_id", "n", "overall": {...}, "by_regime": {regime: {...}}}``
    where each bucket carries n / gpt_kill / technical_kill / mismatch (+ pct).
    """
    cur = conn.execute(
        """
        SELECT regime, technical_decision, gpt_decision, mismatch
        FROM gate_shadow_events
        WHERE gate_id = ? AND gpt_decision != ''
        """,
        (int(gate_id),),
    )
    by_regime_rows: dict[str, list[tuple[str, str, int]]] = {}
    all_rows: list[tuple[str, str, int]] = []
    for regime, technical, gpt, mismatch in cur:
        rec = (str(technical), str(gpt), int(mismatch))
        all_rows.append(rec)
        by_regime_rows.setdefault(str(regime), []).append(rec)
    return {
        "gate_id": int(gate_id),
        "n": len(all_rows),
        "overall": _agreement_bucket(all_rows),
        "by_regime": {r: _agreement_bucket(rs) for r, rs in by_regime_rows.items()},
    }


# ===========================================================================
# 2. chop_churn
# ===========================================================================


def chop_churn(conn: sqlite3.Connection) -> dict[str, Any]:
    """Churn signature from ``positions`` + close ``fills``.

    - ``entries_per_closed_trade`` = total positions (entries) / closed trades.
      A high ratio (many opens per close, or many opens still churning) is the
      chop signature a deterministic over-block could be masking.
    - ``loser_timeout_closes`` = closes attributable to the stale-loser timeout.
      ``positions`` persists no close-reason marker, so this is a HEURISTIC:
      a closed trade with negative realised pnl_r AND hold >= LOSER_TIMEOUT_SEC.
      (Documented in the returned ``loser_timeout_heuristic`` field.)
    - ``avg_hold_sec`` = mean (closed_ts - opened_ts) over closed trades.
    - ``same_symbol_repeat_loss_max`` = longest run of consecutive same-symbol
      losing closes (chronological), maxed across symbols.
    - ``n_closed`` = number of closed trades.
    """
    total_entries = conn.execute(
        "SELECT COUNT(*) FROM positions"
    ).fetchone()[0]

    # Closed trades joined to their realised close-fill pnl_usd. LEFT JOIN so a
    # closed position with no close fill still counts (pnl unknown → 0.0).
    closed = conn.execute(
        """
        SELECT p.position_id, p.symbol, p.opened_ts, p.closed_ts,
               COALESCE(SUM(f.pnl_usd), 0.0) AS pnl_usd
        FROM positions p
        LEFT JOIN fills f
            ON f.contribution_id = p.position_id AND f.is_close = 1
        WHERE p.status = 'closed' AND p.closed_ts IS NOT NULL
        GROUP BY p.position_id
        ORDER BY p.closed_ts ASC, p.position_id ASC
        """
    ).fetchall()

    n_closed = len(closed)
    if n_closed == 0:
        return {
            "entries_per_closed_trade": 0.0,
            "loser_timeout_closes": 0,
            "avg_hold_sec": 0.0,
            "same_symbol_repeat_loss_max": 0,
            "n_closed": 0,
            "total_entries": int(total_entries),
            "loser_timeout_heuristic": (
                "negative pnl_r AND hold >= 900s (no persisted close marker)"
            ),
        }

    holds: list[float] = []
    loser_timeouts = 0
    per_symbol_streak: dict[str, int] = {}
    repeat_loss_max = 0
    for _pid, symbol, opened_ts, closed_ts, pnl_usd in closed:
        hold = max(0.0, float(closed_ts) - float(opened_ts))
        holds.append(hold)
        pnl_r = float(pnl_usd) / PNL_R_USD_DENOM
        if pnl_r < 0.0 and hold >= LOSER_TIMEOUT_SEC:
            loser_timeouts += 1
        sym = str(symbol)
        if pnl_r < 0.0:
            per_symbol_streak[sym] = per_symbol_streak.get(sym, 0) + 1
            repeat_loss_max = max(repeat_loss_max, per_symbol_streak[sym])
        else:
            per_symbol_streak[sym] = 0

    return {
        "entries_per_closed_trade": float(total_entries) / n_closed,
        "loser_timeout_closes": loser_timeouts,
        "avg_hold_sec": sum(holds) / n_closed,
        "same_symbol_repeat_loss_max": repeat_loss_max,
        "n_closed": n_closed,
        "total_entries": int(total_entries),
        "loser_timeout_heuristic": (
            "negative pnl_r AND hold >= 900s (no persisted close marker)"
        ),
    }


# ===========================================================================
# 3. fee_adjusted_realized_r
# ===========================================================================


def fee_adjusted_realized_r(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gross vs fee/slippage-adjusted realised R from close ``fills``.

    Per close fill (``is_close = 1``):
      - gross contribution = pnl_usd / PNL_R_USD_DENOM
      - fee drag           = fee_usd / PNL_R_USD_DENOM
      - slippage drag      = (size_usd * slippage_bps / 10_000) / PNL_R_USD_DENOM

    ``net_realized_r = gross_realized_r - fee_slippage_drag_r``. Gross PnL on a
    fill may already be net of fees depending on the venue accounting, so the
    drag here is the EXPLICIT fee+slippage cost surfaced separately — it makes
    the cost wedge visible (gross can hide churn that net reveals). The R
    conversion is the constant-risk proxy documented at module scope.
    """
    rows = conn.execute(
        """
        SELECT pnl_usd, fee_usd, slippage_bps, size_usd
        FROM fills
        WHERE is_close = 1
        """
    ).fetchall()
    n = len(rows)
    if n == 0:
        return {
            "gross_realized_r": 0.0,
            "fee_slippage_drag_r": 0.0,
            "net_realized_r": 0.0,
            "n": 0,
            "r_conversion": f"pnl_usd / {PNL_R_USD_DENOM} (constant-risk proxy)",
        }
    gross_usd = 0.0
    drag_usd = 0.0
    for pnl_usd, fee_usd, slippage_bps, size_usd in rows:
        gross_usd += float(pnl_usd)
        slippage_usd = abs(float(size_usd)) * abs(float(slippage_bps)) / 10_000.0
        drag_usd += abs(float(fee_usd)) + slippage_usd
    gross_r = gross_usd / PNL_R_USD_DENOM
    drag_r = drag_usd / PNL_R_USD_DENOM
    return {
        "gross_realized_r": gross_r,
        "fee_slippage_drag_r": drag_r,
        "net_realized_r": gross_r - drag_r,
        "n": n,
        "r_conversion": f"pnl_usd / {PNL_R_USD_DENOM} (constant-risk proxy)",
    }


# ===========================================================================
# 4. gpt_kill_counterfactual
# ===========================================================================


def gpt_kill_counterfactual(conn: sqlite3.Connection) -> dict[str, Any]:
    """PROXY for whether G3 GPT-KILLs blocked winners vs avoided losers.

    ``gate_shadow_events`` has no ``strategy`` column and no forward PnL on a
    non-entered signal, so this is explicitly a PROXY, NOT a true
    counterfactual. For each G3 row with ``gpt_decision = 'KILL'`` we join
    ``cell_matrix_p0`` on (exchange = venue, ticker = symbol, regime), aggregated
    over strategy (mean ``avg_pnl_r``), and bucket:

      - ``over_block``  : matched-cell mean avg_pnl_r > 0  (potential over-block
                          of a historically WINNING cell — a KILL we should be
                          suspicious of),
      - ``justified``   : matched-cell mean avg_pnl_r < 0  (KILL avoided a
                          historically LOSING cell),
      - ``no_match``    : no warm cell matched (cold / unknown — no evidence).

    A mean of exactly 0.0 counts as ``justified`` (not > 0, so not flagged as an
    over-block). This is directional evidence only — there is NO forward PnL on
    the signals GPT killed, so a "winning cell" KILL is a *flag to investigate*,
    not proof of a missed winner.
    """
    kill_rows = conn.execute(
        """
        SELECT venue, symbol, regime
        FROM gate_shadow_events
        WHERE gate_id = ? AND gpt_decision = 'KILL'
        """,
        (GATE_SIGNAL_VALIDATOR,),
    ).fetchall()

    over_block = 0
    justified = 0
    no_match = 0
    for venue, symbol, regime in kill_rows:
        row = conn.execute(
            """
            SELECT AVG(avg_pnl_r)
            FROM cell_matrix_p0
            WHERE exchange = ? AND ticker = ? AND regime = ? AND n_eff >= ?
            """,
            (str(venue), str(symbol), str(regime), COUNTERFACTUAL_WARM_FLOOR_N_EFF),
        ).fetchone()
        mean = row[0] if row is not None else None
        if mean is None:
            no_match += 1
        elif float(mean) > 0.0:
            over_block += 1
        else:
            justified += 1

    n = len(kill_rows)
    return {
        "n_gpt_kill": n,
        "over_block": over_block,
        "justified": justified,
        "no_match": no_match,
        "over_block_pct": _pct(over_block, n),
        "justified_pct": _pct(justified, n),
        "no_match_pct": _pct(no_match, n),
        "proxy": (
            "matched-cell mean avg_pnl_r over strategies; NO forward PnL on "
            "killed signals — directional flag only, not a true counterfactual"
        ),
    }


# ===========================================================================
# 5. summarize + CLI
# ===========================================================================


def summarize(conn: sqlite3.Connection) -> dict[str, Any]:
    """All acceptance metrics in one read-only dict (future-cutover grounding)."""
    return {
        "g3_agreement": gate_agreement(conn, GATE_SIGNAL_VALIDATOR),
        "g4_agreement": gate_agreement(conn, GATE_PRE_ENTRY_WATCHER),
        "chop_churn": chop_churn(conn),
        "fee_adjusted_realized_r": fee_adjusted_realized_r(conn),
        "gpt_kill_counterfactual": gpt_kill_counterfactual(conn),
    }


def _print_report(db_path: str) -> None:
    """Read-only CLI report. Opens the DB read-only (mode=ro) so a stray run can
    never mutate a live DB."""
    import json

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        report = summarize(conn)
    finally:
        conn.close()
    print(json.dumps(report, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Shadow acceptance metrics (READ-ONLY analysis)."
    )
    parser.add_argument("--db", required=True, help="path to the shadow DB")
    args = parser.parse_args()
    _print_report(args.db)


if __name__ == "__main__":  # pragma: no cover
    main()
