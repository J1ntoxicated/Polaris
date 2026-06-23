"""Polaris dashboard v1 — snapshot panel queries (funnel / cells / trades / etc).

Per-panel best-effort read helpers consumed by ``snapshot.collect_snapshot``:
gate funnel, cell-matrix top/bottom, regime heatmap, recent closed trades,
learner state, GPT cost, alert log, universe focus. Split out of
``snapshot_queries.py`` to keep each module ≤500 LOC. Shared low-level helpers
and lookback constants are imported from ``snapshot_queries``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Final

from polaris.core.economics.fees import demo_fee_usd, real_fee_usd
from polaris.core.learners.posterior import edge_verdict
from polaris.core.metrics.risk_unit import R_USD_PROXY

# E2 IA-rebuild tab queries live in ``snapshot_e2`` to keep this module within
# the LOC guideline; re-exported here so callers keep a single import surface.
from polaris.scripts.dashboard.snapshot_e2 import (
    _ai_shadow_panel,
    _entry_admission_stats,
    _exit_surface,
    _regime_states,
    _shadow_agreement,
)
from polaris.scripts.dashboard.snapshot_models import (
    AlertRow,
    CellRow,
    ClosedTrade,
    EdgeValidationRow,
    GateDecisionRow,
    GptStat,
    LearnerSlot,
    RegimeBar,
    RotationEvent,
    RotationTelemetry,
)
from polaris.scripts.dashboard.snapshot_queries import (
    GATE_FUNNEL_LOOKBACK_SEC,
    GPT_LOOKBACK_SEC,
    GPT_PRICE_PER_1K,
    GPT_TOKENS_PER_CALL,
    LEARNER_DELTA_LOOKBACK_SEC,
    _as_dict,
    _safe_query,
    _symbol_from_inst,
)

__all__ = [
    "_ai_shadow_panel",
    "_entry_admission_stats",
    "_exit_surface",
    "_regime_states",
    "_shadow_agreement",
]

# ---------------------------------------------------------------------------
# Section: per-gate decision summary (replaces the pass/kill funnel)
# ---------------------------------------------------------------------------


_GATE_LABELS: Final[dict[int, str]] = {
    1: "Universe",
    2: "Strategy",
    3: "Validator",
    4: "PreEntry",
    5: "Sizer",
    6: "Monitor",
    7: "Exit",
    8: "Reflector",
}


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _decode_payload(payload_json: str | None) -> dict[str, Any]:
    if not payload_json:
        return {}
    try:
        data = json.loads(payload_json)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _gate_decisions(
    conn: sqlite3.Connection,
    *,
    now_s: int,
    universe_focus_n: int,
    universe_refresh: str,
) -> list[GateDecisionRow]:
    """Per-gate DECISION-summary rows (flow_not_block → no pass/kill ratio).

    One window read over ``gate_events``; the meaningful per-gate metrics are
    decoded from ``payload_json`` (G5 T4 size aggregates, G7/G6 action counts, G8
    lesson mix, G2 signal count) rather than folded into a structurally-100%
    pass-rate. G1 uses the already-computed focus count + a derived liquidity-floor
    exclusion (active-universe count − focus). Display-only; never feeds trading."""
    since = now_s - GATE_FUNNEL_LOOKBACK_SEC
    rows = _safe_query(
        conn,
        """SELECT gate_id, decision, payload_json FROM gate_events
           WHERE created_ts >= ?""",
        (since,),
    )
    # Bucket raw rows by gate for per-gate roll-up.
    by_gate: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    for r in rows:
        gid = int(r[0] or 0)
        dec = str(r[1] or "").upper()
        by_gate.setdefault(gid, []).append((dec, _decode_payload(r[2])))

    out: list[GateDecisionRow] = []
    for gid in range(1, 9):
        evs = by_gate.get(gid, [])
        n = len(evs)
        headline, metrics = _gate_headline(
            gid,
            evs,
            universe_focus_n=universe_focus_n,
            universe_refresh=universe_refresh,
            conn=conn,
            since=since,
        )
        out.append(
            GateDecisionRow(
                gate_id=gid,
                label=_GATE_LABELS.get(gid, f"G{gid}"),
                headline=headline,
                n=n,
                metrics=metrics,
            )
        )
    return out


def _gate_headline(
    gid: int,
    evs: list[tuple[str, dict[str, Any]]],
    *,
    universe_focus_n: int,
    universe_refresh: str,
    conn: sqlite3.Connection,
    since: int,
) -> tuple[str, dict[str, Any]]:
    """The characteristic meaningful (headline, metrics) for one gate's window."""
    if gid == 1:  # Universe — focus count + derived liquidity-floor exclusions.
        active_rows = _safe_query(
            conn, "SELECT COUNT(*) FROM universe WHERE is_active = 1"
        )
        active_n = int(active_rows[0][0]) if active_rows and active_rows[0][0] else 0
        excluded = max(0, active_n - universe_focus_n)
        m = {
            "focus_n": universe_focus_n,
            "excluded_n": excluded,
            "refresh": universe_refresh,
        }
        return (
            f"focus {universe_focus_n} · excluded {excluded} below floor"
            f" · {universe_refresh}",
            m,
        )

    if gid == 2:  # Strategy — signals fired + top strategies.
        srows = _safe_query(
            conn,
            """SELECT s.strategy_id, COUNT(DISTINCT ge.signal_id)
               FROM gate_events ge
               JOIN signals s ON s.signal_id = ge.signal_id
               WHERE ge.gate_id = 2 AND ge.created_ts >= ?
               GROUP BY s.strategy_id
               ORDER BY 2 DESC""",
            (since,),
        )
        fired = sum(int(r[1] or 0) for r in srows) or len(evs)
        top = ", ".join(str(r[0]) for r in srows[:3])
        m = {"fired_n": fired, "top_strategies": [str(r[0]) for r in srows[:3]]}
        return (f"{fired} signals fired" + (f" · {top}" if top else ""), m)

    if gid == 3:  # Validator — verdict mix + median scalar.
        counts = _decision_counts(evs)
        scalars = [
            float(p["scalar"])
            for _d, p in evs
            if isinstance(p.get("scalar"), (int, float))
        ]
        med = _median(scalars)
        m = {"decisions": counts, "median_scalar": round(med, 3)}
        passed = counts.get("PASS", 0)
        return (
            f"{passed} validated"
            + (f" · scalar~{med:.2f}" if scalars else ""),
            m,
        )

    if gid == 4:  # PreEntry — proceed / hold counts.
        counts = _decision_counts(evs)
        proc = counts.get("PROCEED", 0)
        hold = counts.get("HOLD", 0)
        m = {"decisions": counts}
        return (f"{proc} proceeded / {hold} holding", m)

    if gid == 5:  # Sizer — THE T4 headline (median size + tier mix).
        risks: list[float] = []
        notionals: list[float] = []
        tier_mix: dict[str, int] = {}
        for _d, p in evs:
            sized = _as_dict(p.get("sized"))
            rp = sized.get("final_risk_pct")
            nt = sized.get("final_notional_usd")
            if isinstance(rp, (int, float)):
                risks.append(float(rp))
            if isinstance(nt, (int, float)):
                notionals.append(float(nt))
            prop = _as_dict(sized.get("proposal"))
            tier = prop.get("tier_amplifier")
            if isinstance(tier, (int, float)):
                key = f"{float(tier):g}x"
                tier_mix[key] = tier_mix.get(key, 0) + 1
        mrp = _median(risks) * 100.0
        mnt = _median(notionals)
        tier_str = " ".join(f"{k}:{v}" for k, v in sorted(tier_mix.items()))
        m = {
            "sized_n": len(risks),
            "median_risk_pct": round(mrp, 3),
            "median_notional_usd": round(mnt, 1),
            "tier_mix": tier_mix,
        }
        return (
            f"{len(risks)} sized · risk~{mrp:.2f}% · ${mnt:,.0f}"
            + (f" · tier {tier_str}" if tier_str else ""),
            m,
        )

    if gid == 6:  # Monitor — LIVE open positions only (was windowed event count).
        counts = _decision_counts(evs)
        adj = counts.get("ADJUST_EXIT", 0)
        ex = counts.get("EXIT_NOW", 0)
        swap = counts.get("SWAP_STRATEGY", 0)
        open_rows = _safe_query(
            conn, "SELECT COUNT(*) FROM positions WHERE status = 'open'"
        )
        open_n = int(open_rows[0][0]) if open_rows and open_rows[0][0] else 0
        m = {"decisions": counts, "open_positions": open_n, "window_checks": len(evs)}
        return (
            f"{open_n} monitored · {len(evs)} checks · adjust {adj}"
            f" / exit {ex}" + (f" / swap {swap}" if swap else ""),
            m,
        )

    if gid == 7:  # Exit — action mix + top reason.
        counts = _decision_counts(evs)
        reasons: dict[str, int] = {}
        for _d, p in evs:
            rs = p.get("reason")
            if isinstance(rs, str) and rs:
                reasons[rs] = reasons.get(rs, 0) + 1
        top_reason = max(reasons.items(), key=lambda kv: kv[1])[0] if reasons else ""
        hold = counts.get("HOLD", 0)
        adj = counts.get("ADJUST_EXIT", 0)
        ex = counts.get("EXIT_NOW", 0)
        m = {"decisions": counts, "top_reason": top_reason}
        return (
            f"hold {hold} / adjust {adj} / exit {ex}"
            + (f" · {top_reason}" if top_reason else ""),
            m,
        )

    if gid == 8:  # Reflector — lessons + type mix + avg confidence.
        types: dict[str, int] = {}
        confs: list[float] = []
        for _d, p in evs:
            lt = p.get("lesson_type")
            if isinstance(lt, str) and lt:
                types[lt] = types.get(lt, 0) + 1
            cf = p.get("confidence")
            if isinstance(cf, (int, float)):
                confs.append(float(cf))
        avg_conf = sum(confs) / len(confs) if confs else 0.0
        type_str = " ".join(f"{k}:{v}" for k, v in sorted(types.items()))
        m = {
            "lessons_n": len(evs),
            "type_mix": types,
            "avg_confidence": round(avg_conf, 3),
        }
        return (
            f"{len(evs)} lessons"
            + (f" · {type_str}" if type_str else "")
            + (f" · conf~{avg_conf:.2f}" if confs else ""),
            m,
        )

    return ("", {})


def _decision_counts(evs: list[tuple[str, dict[str, Any]]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d, _p in evs:
        if d:
            counts[d] = counts.get(d, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Section: cell matrix top/bottom
# ---------------------------------------------------------------------------


def _cell_top_bottom(
    conn: sqlite3.Connection,
    *,
    cell_mult: dict[tuple[str, str, str, str], float],
    n: int = 5,
) -> tuple[list[CellRow], list[CellRow], int]:
    rows = _safe_query(
        conn,
        """SELECT exchange, strategy, ticker, regime, n_eff, score
           FROM cell_matrix_p0
           WHERE n_eff >= 20
           ORDER BY score DESC""",
    )
    if not rows:
        return [], [], 0
    eligible_n = len(rows)
    top_rows = rows[:n]
    bot_rows = list(reversed(rows[-n:]))
    if eligible_n < n * 2:
        # Avoid duplicate row showing in both top & bottom for tiny pools
        seen = {(r[0], r[1], r[2], r[3]) for r in top_rows}
        bot_rows = [r for r in bot_rows if (r[0], r[1], r[2], r[3]) not in seen]

    def _build(r: tuple[Any, ...]) -> CellRow:
        key = (str(r[0]), str(r[1]), str(r[2]), str(r[3]))
        return CellRow(
            exchange=str(r[0]),
            strategy=str(r[1]),
            ticker=str(r[2]),
            regime=str(r[3]),
            n_eff=float(r[4] or 0.0),
            score=float(r[5] or 0.0),
            mult=cell_mult.get(key, 1.0),
        )

    return [_build(r) for r in top_rows], [_build(r) for r in bot_rows], eligible_n


# ---------------------------------------------------------------------------
# Section: regime heatmap
# ---------------------------------------------------------------------------


def _regime_bars(
    conn: sqlite3.Connection,
) -> tuple[list[RegimeBar], dict[tuple[str, str], str]]:
    rows = _safe_query(
        conn,
        """SELECT venue, underlying_group_id, regime FROM regime_state""",
    )
    counts: dict[str, int] = {}
    lookup: dict[tuple[str, str], str] = {}
    for v, g, regime in rows:
        regime_s = str(regime or "chop")
        counts[regime_s] = counts.get(regime_s, 0) + 1
        lookup[(str(v), str(g))] = regime_s
    ordered = ["chop", "bull_trend", "bear_trend", "crisis"]
    bars = [RegimeBar(regime=r, count=counts.get(r, 0)) for r in ordered]
    return bars, lookup


# ---------------------------------------------------------------------------
# Section: recent closed trades (trade-level pairing)
# ---------------------------------------------------------------------------


def _pnl_r_by_contribution(conn: sqlite3.Connection) -> dict[str, float]:
    """{position_id: canonical pnl_r} for CLOSED positions (Step M lookup).

    Lets a close fill resolve its trade's canonical risk-unit R via
    ``fills.contribution_id == positions.position_id``, so the recent-closed
    panel shows the SAME R the ticker / strategy panels do. Reconciled
    (tracking-failure) rows carry NULL pnl_r → excluded. Graceful empty."""
    rows = _safe_query(
        conn,
        "SELECT position_id, pnl_r FROM positions "
        "WHERE status = 'closed' AND pnl_r IS NOT NULL",
    )
    return {str(r[0]): float(r[1] or 0.0) for r in rows}


def _recent_closed_trades(
    conn: sqlite3.Connection,
    *,
    n: int = 10,
    regime_lookup: dict[tuple[str, str], str] | None = None,
) -> list[ClosedTrade]:
    """Pair close fills with their entry fills, exact when possible.

    Codex round 2 P0 fix: the production pipeline now persists every fill
    with a ``contribution_id`` that equals the originating ``position_id``
    (see ``_production_pipeline.persist_fill`` + ``_production_close``).
    We pair via that exact ID first, falling back to FIFO per
    ``(venue, instrument_id, strategy_id)`` only when ``contribution_id`` is
    NULL (legacy / smoke rows). This eliminates cross-pairing when two open
    positions share the same ``(venue, inst, strat)`` bucket.

    E2 display additions (read-only): each closed trade also carries its
    ``fee_usd`` (the DEMO close fee — 70 bps OKX sandbox, recomputed from the
    close notional), ``real_fee_usd`` (the SAME close notional re-priced at the
    REAL venue schedule via ``economics.fees``), ``pnl_pct`` (close pnl as a % of
    entry notional), and a best-effort ``regime`` label (the CURRENT
    regime_state for the venue, via ``regime_lookup`` keyed on (venue, symbol) →
    falls back to "" when absent).
    """
    rows = _safe_query(
        conn,
        """SELECT venue, instrument_id, strategy_id, side, fill_price,
                  pnl_usd, ts_ms, base_qty, size_usd, is_close,
                  contribution_id
           FROM fills
           ORDER BY ts_ms ASC""",
    )
    # Step M (2026-06-22): r_units is the canonical risk-unit R, read from the
    # close's position (positions.pnl_r via contribution_id) so a trade shows the
    # SAME R on this panel as on the ticker / strategy panels. A fill with no
    # matched position (legacy / smoke) falls back to the shared $-proxy denom
    # (one number, not the old $10/$50 split).
    pnl_r_by_contrib = _pnl_r_by_contribution(conn)
    # Index by exact contribution_id → first open fill (entry)
    open_by_contrib: dict[str, tuple[float, int]] = {}
    open_fifo: dict[tuple[str, str, str], list[tuple[float, int]]] = {}
    closed_trades: list[ClosedTrade] = []
    for r in rows:
        venue = str(r[0])
        inst = str(r[1])
        strat = str(r[2])
        side = str(r[3])
        fill_price = float(r[4] or 0.0)
        pnl = float(r[5] or 0.0)
        ts_ms = int(r[6] or 0)
        qty = float(r[7] or 0.0)
        size_usd = float(r[8] or 0.0)
        is_close = bool(r[9])
        contrib = r[10]
        contrib_str = str(contrib) if contrib else None
        bucket = (venue, inst, strat)
        if not is_close:
            if contrib_str and contrib_str not in open_by_contrib:
                open_by_contrib[contrib_str] = (fill_price, ts_ms)
            open_fifo.setdefault(bucket, []).append((fill_price, ts_ms))
            continue
        # CLOSE leg pairing rules (codex round 3 P0 tightening):
        #  • contribution_id present + exact open found → pair exact
        #  • contribution_id present + open MISSING     → reconstruct from PnL
        #    (do NOT FIFO-pair, that would re-introduce the cross-pair bug)
        #  • contribution_id NULL (legacy / smoke rows) → FIFO bucket fallback
        entry_px: float | None = None
        open_ts_ms: int | None = None
        if contrib_str:
            if contrib_str in open_by_contrib:
                entry_px, open_ts_ms = open_by_contrib.pop(contrib_str)
                # Drain matching FIFO entry so legacy fallbacks don't reuse it.
                fifo = open_fifo.get(bucket)
                if fifo:
                    for i, (px, ts) in enumerate(fifo):
                        if px == entry_px and ts == open_ts_ms:
                            fifo.pop(i)
                            break
            # else: deliberately do NOT FIFO-pair — fall through to reconstruct.
        else:
            fifo = open_fifo.get(bucket) or []
            if fifo:
                entry_px, open_ts_ms = fifo.pop(0)
        if entry_px is None or open_ts_ms is None:
            # Stale close: reconstruct entry from PnL
            if qty > 0 and fill_price > 0:
                sign = 1.0 if side.lower() == "sell" else -1.0
                entry_px = fill_price - (pnl / qty) * sign
            else:
                entry_px = fill_price
            held_sec = 0.0
        else:
            held_sec = max(0.0, (ts_ms - open_ts_ms) / 1000.0)
        if pnl > 0:
            reason = "TP"
        elif pnl < 0:
            reason = "SL" if held_sec < 600 else "TIME"
        else:
            reason = "FLAT"
        symbol = _symbol_from_inst(inst)
        # E2 display columns (read-only). Both fees are recomputed from the
        # close notional via the centralized schedule: demo_fee = 70 bps OKX
        # sandbox drain (the stored fills.fee_usd now holds the REAL fee, so the
        # demo column is recomputed), real_fee = real venue schedule. pnl_pct =
        # close pnl / entry notional; regime = current regime_state when supplied.
        real_fee = real_fee_usd(venue, size_usd) if size_usd > 0 else 0.0
        demo_fee = demo_fee_usd(venue, size_usd) if size_usd > 0 else 0.0
        entry_notional = (entry_px or 0.0) * abs(qty)
        pnl_pct = (pnl / entry_notional * 100.0) if entry_notional > 0 else 0.0
        regime = (
            regime_lookup.get((venue, symbol), "") if regime_lookup else ""
        )
        closed_trades.append(
            ClosedTrade(
                ts_close=ts_ms // 1000,
                venue=venue,
                symbol=symbol,
                strategy_id=strat,
                side_close=side,
                entry_price=entry_px,
                exit_price=fill_price,
                pnl_usd=pnl,
                r_units=(
                    pnl_r_by_contrib[contrib_str]
                    if contrib_str and contrib_str in pnl_r_by_contrib
                    else pnl / R_USD_PROXY
                ),
                held_sec=held_sec,
                exit_reason=reason,
                regime=regime,
                pnl_pct=pnl_pct,
                fee_usd=demo_fee,
                real_fee_usd=real_fee,
            )
        )
    closed_trades.sort(key=lambda t: t.ts_close, reverse=True)
    return closed_trades[:n]


# ---------------------------------------------------------------------------
# Section: learner state (3 P0)
# ---------------------------------------------------------------------------


_LEARNER_FEATURED: Final[tuple[str, ...]] = ("session_mult", "regime_mult", "max_hold")


def _learner_slots(conn: sqlite3.Connection, *, now_s: int) -> list[LearnerSlot]:
    cutoff = now_s - LEARNER_DELTA_LOOKBACK_SEC
    out: list[LearnerSlot] = []
    for lid in _LEARNER_FEATURED:
        # Pick the row with highest n_eff (most-informed key)
        rows = _safe_query(
            conn,
            """SELECT key, value, n_eff, updated_at FROM learner_state
               WHERE learner_id = ? ORDER BY n_eff DESC LIMIT 1""",
            (lid,),
        )
        if not rows:
            out.append(LearnerSlot(learner_id=lid, key="-", value=1.0, delta_1h=0.0, n_eff=0.0))
            continue
        key = str(rows[0][0])
        value = float(rows[0][1] or 1.0)
        n_eff = float(rows[0][2] or 0.0)
        # Look up snapshot ≥ 1h ago for delta
        snap = _safe_query(
            conn,
            """SELECT value FROM learner_snapshot
               WHERE learner_id = ? AND key = ? AND snapshot_ts <= ?
               ORDER BY snapshot_ts DESC LIMIT 1""",
            (lid, key, cutoff),
        )
        prev = float(snap[0][0]) if snap else value
        out.append(
            LearnerSlot(
                learner_id=lid, key=key, value=value, delta_1h=value - prev, n_eff=n_eff,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Section: edge validation (Phase 1 — Bayesian posterior, display only)
# ---------------------------------------------------------------------------


def _edge_validation(
    conn: sqlite3.Connection, *, n: int = 8
) -> list[EdgeValidationRow]:
    """Read the cost-adjusted expectancy posterior per (strategy × cell × regime).

    Ordered by confidence ``p_pos`` so the most-validated / most-anti edges
    surface first. The ``est_cost`` flag is set for Capital rows (const-bps
    cost stand-in) vs OKX rows (real fee + slippage). Measurement/display
    only — this query has no effect on sizing.
    """
    rows = _safe_query(
        conn,
        """SELECT exchange, strategy, ticker, regime, mu, p_pos, n_samples
           FROM learner_posterior
           ORDER BY n_samples DESC, p_pos DESC""",
    )
    out: list[EdgeValidationRow] = []
    for r in rows[:n]:
        exchange = str(r[0])
        p_pos = float(r[5] or 0.5)
        out.append(
            EdgeValidationRow(
                exchange=exchange,
                strategy=str(r[1]),
                ticker=str(r[2]),
                regime=str(r[3]),
                cost_adj_exp=float(r[4] or 0.0),
                p_pos=p_pos,
                n_samples=int(r[6] or 0),
                verdict=edge_verdict(p_pos),
                est_cost=exchange == "capital",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Section: GPT cost
# ---------------------------------------------------------------------------


def _gpt_stats(conn: sqlite3.Connection, *, now_s: int) -> list[GptStat]:
    rows = _safe_query(
        conn,
        """SELECT model_used, phase, COUNT(*) FROM gate_events
           WHERE created_ts >= ? AND model_used IS NOT NULL
           GROUP BY model_used, phase""",
        (now_s - GPT_LOOKBACK_SEC,),
    )
    # Aggregate ok / total per model (phase == 'success' is an ok call; any
    # other phase — 'fail' — is an error). Drives the silent-degradation card.
    totals: dict[str, int] = {}
    ok: dict[str, int] = {}
    for model, phase, n in rows:
        if not model:
            continue
        m = str(model)
        if m == "python":
            continue
        cnt = int(n)
        totals[m] = totals.get(m, 0) + cnt
        if str(phase) == "success":
            ok[m] = ok.get(m, 0) + cnt
    out: list[GptStat] = []
    for m, total in totals.items():
        ok_n = ok.get(m, 0)
        price = GPT_PRICE_PER_1K.get(m, GPT_PRICE_PER_1K["gpt"])
        cost_per_h = total * GPT_TOKENS_PER_CALL / 1000.0 * price
        out.append(
            GptStat(
                model=m,
                calls_per_h=total,
                cost_per_h_usd=cost_per_h,
                cost_24h_proj_usd=cost_per_h * 24.0,
                ok_pct=(ok_n / total * 100.0) if total else 100.0,
                err_n=total - ok_n,
            )
        )
    out.sort(key=lambda g: g.calls_per_h, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Section: alert log
# ---------------------------------------------------------------------------


def _alerts(conn: sqlite3.Connection, *, n: int = 3) -> list[AlertRow]:
    out: list[AlertRow] = []
    risk = _safe_query(
        conn,
        """SELECT created_ts, event_type, strategy_id, payload_json FROM risk_events
           ORDER BY created_ts DESC LIMIT ?""",
        (n,),
    )
    for r in risk:
        out.append(
            AlertRow(
                ts=int(r[0] or 0),
                level="WARN",
                module=str(r[2] or "risk"),
                msg=f"{r[1]}: {str(r[3])[:60]}",
            )
        )
    fault = _safe_query(
        conn,
        """SELECT event_ts, fault_type, strategy_id, detail_json FROM strategy_fault_events
           ORDER BY event_ts DESC LIMIT ?""",
        (n,),
    )
    for r in fault:
        out.append(
            AlertRow(
                ts=int(r[0] or 0),
                level="ERROR",
                module=str(r[2] or "strat"),
                msg=f"{r[1]}: {str(r[3])[:60]}",
            )
        )
    out.sort(key=lambda a: a.ts, reverse=True)
    return out[:n]


# ---------------------------------------------------------------------------
# Section: universe
# ---------------------------------------------------------------------------


def _universe(conn: sqlite3.Connection) -> tuple[int, str]:
    # Current cycle's focus count (NOT all-time accumulated rows)
    rows = _safe_query(
        conn,
        "SELECT COUNT(*), cycle_ts FROM watchlist_focus "
        "WHERE cycle_ts = (SELECT MAX(cycle_ts) FROM watchlist_focus) "
        "GROUP BY cycle_ts",
    )
    if rows and rows[0][0]:
        cnt, last_ts = rows[0]
        ts_str = (
            time.strftime("%H:%M:%S", time.localtime(int(last_ts))) if last_ts else "n/a"
        )
        return int(cnt), ts_str
    rows = _safe_query(conn, "SELECT COUNT(*) FROM universe WHERE is_active = 1")
    cnt = int(rows[0][0]) if rows else 0
    return cnt, "n/a"


# ---------------------------------------------------------------------------
# Section: rotation + session-forced-exit telemetry (display-only, #12)
# ---------------------------------------------------------------------------


def _rotation_telemetry(
    conn: sqlite3.Connection, *, now_s: int
) -> RotationTelemetry:
    """Read rotation + session-forced-exit observability (display-only).

    Sources the ``loop_rotation_events`` / ``loop_session_exit_events`` tables
    the rotation/forced-exit wires append to (the in-memory ``state.rotations``
    counter is unreachable from the read-only dashboard process). Best-effort:
    a missing table (older schema) or empty tables degrade to a zeroed
    ``RotationTelemetry`` with ``last_rotation=None`` — graceful zero. NEVER read
    by sizing / gating / the rotation evaluator.
    """
    rot_rows = _safe_query(conn, "SELECT COUNT(*) FROM loop_rotation_events")
    rotation_count = int(rot_rows[0][0] or 0) if rot_rows else 0
    exit_rows = _safe_query(conn, "SELECT COUNT(*) FROM loop_session_exit_events")
    session_forced_exit_count = int(exit_rows[0][0] or 0) if exit_rows else 0

    last: RotationEvent | None = None
    last_rows = _safe_query(
        conn,
        """SELECT ts, venue, victim_symbol, victim_strategy, winner_symbol,
                  e_new, e_held, margin, cost
           FROM loop_rotation_events
           ORDER BY ts DESC, rowid_pk DESC LIMIT 1""",
    )
    if last_rows:
        r = last_rows[0]
        last = RotationEvent(
            ts=int(r[0] or 0),
            venue=str(r[1] or ""),
            victim_symbol=str(r[2] or ""),
            victim_strategy=str(r[3] or ""),
            winner_symbol=str(r[4] or ""),
            e_new=float(r[5] or 0.0),
            e_held=float(r[6] or 0.0),
            margin=float(r[7] or 0.0),
            cost=float(r[8] or 0.0),
        )
    # Step M (2026-06-22, Jin locked): drift-close (reconciled) loss is a
    # SEPARATE counter — "tracking failures, not trades" — NEVER mixed into the
    # R ledger. Count = reconciled positions; the rough DOLLAR estimate is summed
    # from the ``orphan_reconciled`` audit rows' ``est_drift_usd`` (mae_r ×
    # risk_usd, recorded at reconcile time). pnl_r is NULL on these rows now, so
    # this no longer reads R from positions. Graceful zero when absent.
    cnt_rows = _safe_query(
        conn,
        "SELECT COUNT(*) FROM positions WHERE status = 'reconciled'",
    )
    reconciled_loss_n = int(cnt_rows[0][0] or 0) if cnt_rows else 0
    drift_rows = _safe_query(
        conn,
        "SELECT payload_json FROM risk_events WHERE event_type = 'orphan_reconciled'",
    )
    reconciled_loss_usd = 0.0
    for (payload_json,) in drift_rows:
        try:
            est = float(json.loads(payload_json).get("est_drift_usd", 0.0))
        except (ValueError, TypeError, AttributeError):
            est = 0.0
        reconciled_loss_usd += est
    # Hardening #1 (2026-06-23): the PRIMARY drift figure is the ACTUAL realized
    # Σ fills.pnl_usd over the reconciled positions' close legs — real money the
    # bot lost to tracking failures, not the mae_r×risk estimate above. The est
    # stays the labeled secondary (some reconciles have no close fill). Still a
    # SEPARATE counter — never mixed into PF/WR/R; the headline surfaces exclude
    # these rows (the LEFT JOIN in _daily_realised_pnl et al).
    realized_rows = _safe_query(
        conn,
        """SELECT COALESCE(SUM(f.pnl_usd), 0.0)
           FROM fills f
           JOIN positions p
             ON p.position_id = f.contribution_id AND f.is_close = 1
           WHERE p.status = 'reconciled'""",
    )
    reconciled_realized_usd = (
        float(realized_rows[0][0] or 0.0) if realized_rows else 0.0
    )
    # Hardening #5 (2026-06-23): count of distinct instruments that fell back to a
    # venue-correct asset_class (the idempotent ``asset_class_fallback`` rows the
    # held-position / bar-baseline paths append). Graceful zero on older schema.
    acfb_rows = _safe_query(
        conn,
        "SELECT COUNT(*) FROM risk_events WHERE event_type='asset_class_fallback'",
    )
    asset_class_fallback_n = int(acfb_rows[0][0] or 0) if acfb_rows else 0
    return RotationTelemetry(
        rotation_count=rotation_count,
        session_forced_exit_count=session_forced_exit_count,
        reconciled_realized_usd=reconciled_realized_usd,
        reconciled_loss_usd=reconciled_loss_usd,
        reconciled_loss_n=reconciled_loss_n,
        asset_class_fallback_n=asset_class_fallback_n,
        last_rotation=last,
    )
