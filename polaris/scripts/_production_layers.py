"""Day 8 production paper loop — Layer 0/1/6 wiring helpers.

Splits the per-layer plumbing out of ``production_paper_loop.py`` so the main
file stays under the 500-line budget. Each function here is invocable on its
own (smoke + tests cover them in isolation).

Layers covered
--------------
* **Layer 0** — Dynamic universe producer. Refreshes the OKX SPOT universe
  every 5 min and Capital CFD universe every 10 min (with chart-endpoint
  proxy compute). Persists to ``universe`` + ``watchlist_focus``.
* **Layer 1** — Per-tick bar ingest. Fetches 1m bars for the active focus
  list, persists to ``bars`` + ``ticker_baseline_*``.
* **Layer 6** — Per-tick recalc cycle. Marks dirty positions, runs
  ``regime_flip.detect_regime_flip`` per (venue, group), evaluates strategy
  swap candidates against the Layer 6 SSOT.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections.abc import Sequence
from typing import Any

import httpx

from polaris.core.altdata.fuser import fuse_evidence
from polaris.core.data.schema import Bar
from polaris.core.isolation.blocklist import load_blocklist
from polaris.core.live_recalc.regime_flip import detect_regime_flip
from polaris.core.live_recalc.tick_recalc import (
    mark_position_dirty,
    run_live_recalc_cycle,
)
from polaris.core.universe.discovery import (
    fetch_alpaca_instruments,
    fetch_capital_instruments,
    fetch_okx_instruments,
    persist_universe,
    rank_active_universe,
)
from polaris.core.universe.schema import UniverseInstrument
from polaris.core.universe.watchlist import compute_dynamic_focus, persist_focus
from polaris.scripts._production_bars import (
    CAPITAL_RESOLUTION_BY_INTERVAL,
    TIMEFRAME_FETCH_CADENCE_SEC,
    fetch_bars_one,
    ingest_bars_for_focus,
    ingest_bars_per_timeframe,
    read_recent_bars,
)
from polaris.scripts._production_indicators import compute_real_regime_signal
from polaris.venues.capital.market_proxy import populate_capital_proxies
from polaris.venues.capital.session import CapitalSession

logger = logging.getLogger(__name__)

# Layer 1 bar-ingest helpers + timeframe constants now live in ``_production_bars``;
# re-exported here so existing ``_production_layers`` import paths keep working.
__all__ = [
    "ALPACA_REFRESH_SEC",
    "CAPITAL_REFRESH_SEC",
    "CAPITAL_RESOLUTION_BY_INTERVAL",
    "OKX_REFRESH_SEC",
    "TIMEFRAME_FETCH_CADENCE_SEC",
    "capital_active_ids_after_collapse_guard",
    "compose_regime_candidate",
    "compute_and_flip_regime",
    "fetch_bars_one",
    "get_focus_targets",
    "ingest_bars_for_focus",
    "ingest_bars_per_timeframe",
    "open_position_targets",
    "read_active_universe",
    "read_recent_bars",
    "refresh_alpaca_universe_once",
    "refresh_capital_universe_once",
    "refresh_focus_watchlist",
    "refresh_okx_universe_once",
    "run_recalc_for_active_positions",
]

OKX_REFRESH_SEC = 300
CAPITAL_REFRESH_SEC = 600
ALPACA_REFRESH_SEC = 600

# C1 — Capital active-universe collapse guard (forensic 2026-05-31): a healthy
# Capital fetch but a session-driven validity collapse (only 1 epic TRADEABLE on
# the weekend) left the active book at exactly 1 closed symbol (EURUSD_W) for 27h
# — FX is 24/5, so breadth must be preserved across the closure, not handed to a
# single survivor. When a refresh's active set collapses to ``<= FLOOR`` while the
# PRIOR book was healthy (``>= PRIOR_MIN``), KEEP the prior active set
# (flow_not_block: preserve breadth, never zero-out). A genuine FULL closure
# (active=0) stays the existing session_wait path — the guard does not resurrect
# it; those rows revive automatically next refresh once TRADEABLE.
CAPITAL_COLLAPSE_ACTIVE_FLOOR = 1
CAPITAL_COLLAPSE_PRIOR_MIN = 5


# ---------------------------------------------------------------------------
# Layer 0 — universe producer
# ---------------------------------------------------------------------------


async def refresh_okx_universe_once(
    conn: sqlite3.Connection, *, now_ts: int | None = None
) -> int:
    """Fetch OKX tickers → 4-axis filter → persist. Returns active count."""
    ts = now_ts if now_ts is not None else int(time.time())
    try:
        instruments = await fetch_okx_instruments(now_ts=ts)
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning("[L0] OKX fetch failed: %r", exc)
        return 0
    active = rank_active_universe(instruments)
    active_ids = {ins.instrument_id for ins in active}
    persist_universe(conn, instruments, is_active_set=active_ids)
    logger.info("[L0/okx] universe %d → active %d", len(instruments), len(active))
    return len(active)


def _read_capital_active_ids(conn: sqlite3.Connection) -> set[str]:
    """Prior Capital active (``is_active=1``) instrument_ids (collapse-guard read)."""
    try:
        return {
            str(r[0])
            for r in conn.execute(
                "SELECT instrument_id FROM universe "
                "WHERE venue = 'capital' AND is_active = 1"
            ).fetchall()
        }
    except sqlite3.Error:
        return set()


def capital_active_ids_after_collapse_guard(
    *,
    new_active_ids: set[str],
    prior_active_ids: set[str],
    fetched_count: int,
) -> set[str]:
    """Preserve prior Capital breadth when a refresh's active set abnormally collapses.

    Returns the active instrument_id set to persist. The new set is used as-is
    UNLESS it has collapsed to ``<= CAPITAL_COLLAPSE_ACTIVE_FLOOR`` while the prior
    book was healthy (``>= CAPITAL_COLLAPSE_PRIOR_MIN``) and the fetch itself was
    non-empty — the forensic 1-symbol weekend collapse. In that case the PRIOR
    active set is kept (flow_not_block: breadth preserved across the session
    closure, never zeroed out to a single stale survivor).

    A genuine full closure (``new_active_ids`` empty) is the legitimate
    session_wait path and is returned unchanged (the guard never resurrects a
    zeroed book; those rows revive next refresh once TRADEABLE). Cold start (no
    prior book) is also a no-op.
    """
    if fetched_count <= 0:
        return new_active_ids
    if not new_active_ids:
        return new_active_ids  # full closure → legitimate session_wait, not a collapse
    if (
        len(new_active_ids) <= CAPITAL_COLLAPSE_ACTIVE_FLOOR
        and len(prior_active_ids) >= CAPITAL_COLLAPSE_PRIOR_MIN
    ):
        return prior_active_ids
    return new_active_ids


async def refresh_capital_universe_once(
    conn: sqlite3.Connection, *, now_ts: int | None = None
) -> int:
    """Fetch Capital CFD nav → proxy compute → 4-axis → persist."""
    ts = now_ts if now_ts is not None else int(time.time())
    api_key = os.environ.get("CAP_API_KEY")
    email = os.environ.get("CAP_EMAIL")
    password = os.environ.get("CAP_PASSWORD")
    if not (api_key and email and password):
        logger.info("[L0/capital] credentials missing — skipping refresh")
        return 0
    try:
        instruments = await fetch_capital_instruments(now_ts=ts)
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning("[L0] Capital fetch failed: %r", exc)
        return 0
    if not instruments:
        return 0
    # Populate proxies — best-effort (per-row failure leaves zeros so the
    # 4-axis filter rejects them cleanly).
    try:
        async with CapitalSession(
            api_key=api_key, identifier=email, password=password, auto_ping=False
        ) as session:
            tokens = await session.ensure_tokens()
            instruments = await populate_capital_proxies(
                instruments,
                cst=tokens.cst,
                security_token=tokens.security_token,
            )
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning("[L0/capital] proxy fetch failed: %r", exc)

    active = rank_active_universe(instruments)
    new_active_ids = {ins.instrument_id for ins in active}
    # C1 collapse guard: preserve prior breadth on a session-driven 1-symbol
    # collapse (read prior BEFORE the upsert overwrites it).
    prior_active_ids = _read_capital_active_ids(conn)
    active_ids = capital_active_ids_after_collapse_guard(
        new_active_ids=new_active_ids,
        prior_active_ids=prior_active_ids,
        fetched_count=len(instruments),
    )
    guarded = active_ids != new_active_ids
    persist_universe(conn, instruments, is_active_set=active_ids)
    if guarded:
        logger.info(
            "[L0/capital] collapse guard: rank→active %d (prior %d) — KEEPING prior "
            "breadth %d (session-driven 1-symbol collapse, flow_not_block)",
            len(new_active_ids), len(prior_active_ids), len(active_ids),
        )
    logger.info(
        "[L0/capital] universe %d → active %d (continuous-rank)",
        len(instruments),
        len(active_ids),
    )
    return len(active_ids)


async def refresh_alpaca_universe_once(
    conn: sqlite3.Connection, *, now_ts: int | None = None
) -> int:
    """Fetch Alpaca US-equity assets → rank → persist. Returns active count.

    Track C (additive). Coarse liquidity proxies are set at fetch time
    (``_alpaca``); a per-row proxy refine step is deferred to the dashboards /
    learners, so this path stays minimal (fetch → rank → persist), mirroring
    the OKX producer. Returns 0 when credentials are missing (smoke-safe).
    """
    ts = now_ts if now_ts is not None else int(time.time())
    try:
        instruments = await fetch_alpaca_instruments(now_ts=ts)
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning("[L0] Alpaca fetch failed: %r", exc)
        return 0
    if not instruments:
        return 0
    active = rank_active_universe(instruments)
    active_ids = {ins.instrument_id for ins in active}
    persist_universe(conn, instruments, is_active_set=active_ids)
    logger.info(
        "[L0/alpaca] universe %d → active %d (continuous-rank)",
        len(instruments),
        len(active),
    )
    return len(active)


def read_active_universe(conn: sqlite3.Connection) -> list[UniverseInstrument]:
    """Read all is_active=1 rows from ``universe``."""
    rows = conn.execute(
        """
        SELECT venue, symbol, instrument_id, underlying_group_id, asset_class,
               quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct,
               depth_10bps_usd, signal_density_7d, listing_ts, last_seen_ts
        FROM universe
        WHERE is_active = 1
        """
    ).fetchall()
    return [
        UniverseInstrument(
            venue=str(r[0]),
            symbol=str(r[1]),
            instrument_id=str(r[2]),
            underlying_group_id=str(r[3]),
            asset_class=str(r[4]),
            quote_ccy=str(r[5]),
            state=str(r[6]),
            vol_24h_usd=float(r[7] or 0.0),
            spread_bps=float(r[8] or 0.0),
            atr_24h_pct=float(r[9] or 0.0),
            depth_10bps_usd=float(r[10] or 0.0),
            signal_density_7d=float(r[11] or 0.0),
            listing_ts=int(r[12]) if r[12] is not None else None,
            last_seen_ts=int(r[13] or 0),
        )
        for r in rows
    ]


def refresh_focus_watchlist(
    conn: sqlite3.Connection, *, cycle_ts: int | None = None
) -> int:
    """Compute dynamic focus over active universe + persist; return count.

    Task 3 / D2: runtime-blocklisted (venue, symbol) — venue-permanent
    compliance rejects (51155) — are excluded so they never enter focus and
    cannot churn the order path.
    """
    ts = cycle_ts if cycle_ts is not None else int(time.time())
    universe = read_active_universe(conn)
    if not universe:
        return 0
    blocked = load_blocklist(conn)
    if blocked:
        universe = [
            ins for ins in universe if (ins.venue, ins.symbol) not in blocked
        ]
        if not universe:
            return 0
    focus = compute_dynamic_focus(universe, cycle_ts=ts)
    persist_focus(conn, focus)
    logger.info("[L0/focus] universe=%d → focus=%d", len(universe), len(focus))
    return len(focus)


def open_position_targets(
    conn: sqlite3.Connection,
) -> list[tuple[str, str, str, str]]:
    """Read every OPEN position as a focus-shaped ``(venue, symbol, asset_class,
    group_id)`` tuple, across ALL venues (OKX / Capital / Alpaca).

    FIX 2/2 — held-position visibility/precision. A position whose symbol is NOT
    in the dynamic focus still needs LIVE WS quotes + fresh bars (dashboard live
    price + exit precision). ``asset_class`` is resolved via a LEFT JOIN to
    ``universe`` (the focus source) and falls back to the ``underlying_group_id``
    prefix (e.g. ``crypto:HYPE`` → ``crypto``) when the held name has aged out of
    the active universe. De-duplicated on ``(venue, symbol)`` so multiple
    positions on one name yield one target. ADD-only (flow_not_block): this never
    blocks an entry, it only forces a held name to stay watched while held.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT p.venue, p.symbol, u.asset_class, p.underlying_group_id
        FROM positions p
        LEFT JOIN universe u
          ON p.venue = u.venue AND p.symbol = u.symbol
        WHERE p.status NOT IN ('closed', 'cancelled', 'reconciled')
        """
    ).fetchall()
    out: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        venue, symbol = str(r[0]), str(r[1])
        if (venue, symbol) in seen:
            continue
        seen.add((venue, symbol))
        group_id = str(r[3] or "")
        asset_class = r[2]
        if not asset_class:
            # Fall back to the group_id prefix (``crypto:HYPE`` → ``crypto``);
            # default ``crypto`` mirrors get_focus_targets when neither is known.
            asset_class = group_id.split(":", 1)[0] if ":" in group_id else "crypto"
        out.append((venue, symbol, str(asset_class), group_id))
    return out


def get_focus_targets(
    conn: sqlite3.Connection, *, cycle_ts: int | None = None, max_n: int = 30
) -> list[tuple[str, str, str, str]]:
    """Read the latest focus cycle as ``(venue, symbol, asset_class, group_id)``.

    Returns up to ``max_n`` dynamic-focus entries (ordered by focus_rank asc)
    UNIONED with every OPEN position's symbol (:func:`open_position_targets`).
    The held symbols are appended AFTER the dynamic picks and are NOT subject to
    ``max_n`` — a held name can never fall off the watched/subscribed set while
    its position is open (FIX 2/2: dashboard live price + exit precision). This
    is the single seam both the bar ingest (``_run_tick``) and the WS
    subscription (``_focus_by_venue``) read, so the union keeps held symbols
    bar-ingested AND WS-subscribed for as long as they are held.

    Empty dynamic focus + no open positions → empty list (caller falls back to
    BTC seed). Union is ADD-only (flow_not_block): never blocks an entry.
    """
    ts = cycle_ts if cycle_ts is not None else int(time.time())
    focus: list[tuple[str, str, str, str]] = []
    row = conn.execute(
        "SELECT MAX(cycle_ts) FROM watchlist_focus WHERE cycle_ts <= ?", (ts,)
    ).fetchone()
    if row is not None and row[0] is not None:
        latest_cycle = int(row[0])
        rows = conn.execute(
            """
            SELECT wf.venue, wf.symbol, u.asset_class, u.underlying_group_id
            FROM watchlist_focus wf
            LEFT JOIN universe u
              ON wf.venue = u.venue AND wf.symbol = u.symbol
            WHERE wf.cycle_ts = ?
            ORDER BY wf.focus_rank ASC
            LIMIT ?
            """,
            (latest_cycle, int(max_n)),
        ).fetchall()
        focus = [
            (str(r[0]), str(r[1]), str(r[2] or "crypto"), str(r[3] or ""))
            for r in rows
        ]
    # Force-seat held symbols (additive, not truncated by max_n). De-dup on
    # (venue, symbol) so a held name already in the dynamic focus is not doubled.
    seen = {(v, s) for v, s, _ac, _g in focus}
    for target in open_position_targets(conn):
        if (target[0], target[1]) in seen:
            continue
        seen.add((target[0], target[1]))
        focus.append(target)
    return focus



# ---------------------------------------------------------------------------
# Layer 6 — recalc + regime flip
# ---------------------------------------------------------------------------


_REGIME_LABELS: tuple[str, ...] = ("bull_trend", "bear_trend", "chop", "crisis")
# Conviction floor mirrors fuser.CONVICTION_FLOOR — an evidence label must clear
# this before it can tilt a borderline price candidate.
_EVIDENCE_CONVICTION_FLOOR = 1.5


def compose_regime_candidate(
    price_candidate: str,
    price_strength: float,
    evidence_scores: dict[str, float],
) -> str:
    """Weighted price↔evidence synthesis — SIGNAL-only label, no side effects.

    Price is the base. Evidence (the fuser's per-label scores) can *tilt* the
    candidate ONLY when its winning conviction beats the price conviction:

      * No evidence scores → price candidate stands (fallback).
      * A price-derived ``crisis`` is NEVER tilted away (safety; a price crash
        is the highest-priority signal and keeps the immediate-flip path).
      * Otherwise compare conviction: ``price_strength`` (0..1) scaled to the
        evidence score-space vs the best evidence score above the conviction
        floor. The stronger conviction wins; ties go to price (status-quo bias,
        NOT a throttle — synthesis only relabels, it never reduces flow).

    This is a pure relabel. It does not size, block, exit, or halt.
    """
    if price_candidate == "crisis":
        return "crisis"
    if not evidence_scores:
        return price_candidate
    best_label = max(evidence_scores, key=lambda k: evidence_scores[k])
    best_score = evidence_scores[best_label]
    if best_score < _EVIDENCE_CONVICTION_FLOOR or best_label == price_candidate:
        return price_candidate
    if best_label not in _REGIME_LABELS:
        return price_candidate
    # Project price conviction into the evidence score-space. A full-strength
    # (1.0) price regime maps to a conviction comparable to a strong evidence
    # win (~3.0); evidence must out-convict it to tilt.
    price_conviction = price_strength * 3.0
    if best_score > price_conviction:
        return best_label
    return price_candidate


def _compose_confidence(
    price_strength: float,
    composed_candidate: str,
    price_candidate: str,
    evidence_scores: dict[str, float],
) -> float:
    """Dynamic confidence (P5) in ``[0, 1]`` from L3 strength + L2/L1 agreement.

    Base = price strength. Agreement bonus when the evidence's winning label
    matches the composed candidate (L3↔L2/L1 concur); a mild penalty when
    evidence disagreed but did not win the tilt. Computed BEFORE the
    detect_regime_flip confirm gate (does NOT alter the gate).
    """
    conf = max(0.0, min(1.0, price_strength))
    if not evidence_scores:
        return max(0.1, conf)  # price-only: never report a zero-confidence label
    best_label = max(evidence_scores, key=lambda k: evidence_scores[k])
    best_score = evidence_scores[best_label]
    if best_score >= _EVIDENCE_CONVICTION_FLOOR:
        if best_label == composed_candidate:
            conf = conf + 0.25 * (1.0 - conf)  # concurrence → tighten toward 1
        elif best_label != price_candidate:
            conf = conf * 0.85  # unresolved disagreement → slightly less certain
    return max(0.1, min(1.0, conf))


def compute_and_flip_regime(
    conn: sqlite3.Connection,
    *,
    venue: str,
    underlying_group_id: str,
    bars: Sequence[Bar],
    now_ts: int,
    altdata_cache: Any = None,
) -> str:
    """Compute candidate regime + run Layer 6 SSOT 2-consecutive-close gate.

    Returns the regime SSOT *after* applying the flip rule so callers using
    the Layer 6 SSOT receive the gated value (matches strategy_swap's
    ``_lookup_regime`` semantics).

    ``altdata_cache`` (#6) supplies alt-data EVIDENCE only. The L3 price signal
    is the base; the fuser's per-label scores tilt a *borderline* candidate via
    ``compose_regime_candidate`` (price conviction vs evidence conviction):

      * The price-derived candidate is computed first and always stands when
        there is no fresh evidence (failing/keyless collector → price-only;
        correct fallback, NOT a defensive throttle).
      * Evidence NEVER downgrades a price-derived ``crisis``.
      * ``candidate_source`` (P4) tags the candidate: a ``crisis`` that came
        from PRICE keeps the immediate-flip fast path; a ``crisis`` introduced
        by EVIDENCE must clear the SAME 2-consecutive-close gate (no bypass).
      * The (possibly tilted) candidate STILL clears the unchanged confirm
        gate. Evidence is additive context only; it does not size, block,
        exit, or write learner/risk state. SIGNAL-only.
    """
    price_candidate, price_strength, _price_ev = compute_real_regime_signal(bars)
    candidate = price_candidate
    evidence: dict[str, Any] = {}
    evidence_scores: dict[str, float] = {}
    if altdata_cache is not None:
        _hint, _conf, ev = fuse_evidence(
            underlying_group_id, altdata_cache, now_ts=now_ts
        )
        evidence = ev
        raw_scores = ev.get("scores") if isinstance(ev, dict) else None
        if isinstance(raw_scores, dict):
            evidence_scores = {str(k): float(v) for k, v in raw_scores.items()}
        candidate = compose_regime_candidate(
            price_candidate, price_strength, evidence_scores
        )
    # P5: dynamic confidence from L3 strength + L2/L1 agreement (pre-gate).
    confidence = _compose_confidence(
        price_strength, candidate, price_candidate, evidence_scores
    )
    # P4: a crisis candidate that PRICE did not produce is evidence-derived and
    # must NOT take the immediate-flip path.
    candidate_source = (
        "evidence"
        if candidate == "crisis" and price_candidate != "crisis"
        else "price"
    )
    # Regime candidate (DEBUG): the pre-gate composition — the L3 price candidate,
    # the (possibly evidence-tilted) composed candidate, its source, and the
    # dynamic confidence. The 2-consecutive-close confirm gate below is unchanged;
    # only a confirmed flip emits INFO. Log only — never sizes/blocks/exits.
    logger.debug(
        "[L6/regime] candidate %s/%s price=%s tilt=%s source=%s "
        "price_strength=%.3f confidence=%.3f",
        venue, underlying_group_id, price_candidate, candidate,
        candidate_source, price_strength, confidence,
    )
    decision = detect_regime_flip(
        conn,
        venue=venue,
        underlying_group_id=underlying_group_id,
        candidate=candidate,
        now_ts=now_ts,
        evidence=evidence,
        confidence=confidence,
        candidate_source=candidate_source,
    )
    # Either the flip was confirmed (decision.to_regime is the new SSOT) or
    # the row stayed at the prior regime. Read back the persisted SSOT so the
    # caller sees what every other Layer 6 consumer sees.
    row = conn.execute(
        "SELECT regime FROM regime_state "
        "WHERE venue = ? AND underlying_group_id = ?",
        (venue, underlying_group_id),
    ).fetchone()
    if row is None:
        return candidate
    persisted = str(row[0])
    if decision.confirmed:
        logger.info(
            "[L6/regime] flip %s/%s → %s (%s)",
            venue, underlying_group_id, persisted, decision.reason,
        )
    return persisted


async def run_recalc_for_active_positions(
    conn: sqlite3.Connection,
    *,
    now_ts: int,
) -> int:
    """Sweep active positions through Layer 6 dirty-mark recalc cycle.

    Returns count of positions evaluated. Marks each open position dirty
    (per-tick price proxy) so the cycle has something to evaluate.
    """
    rows = conn.execute(
        "SELECT position_id FROM positions "
        "WHERE status NOT IN ('closed', 'cancelled', 'reconciled')"
    ).fetchall()
    for r in rows:
        mark_position_dirty(
            conn, position_id=str(r[0]), reason="tick_5s", now_ts=now_ts
        )
    if not rows:
        return 0
    active = [{"position_id": str(r[0])} for r in rows]
    await run_live_recalc_cycle(conn, now_ts=now_ts, active_positions=active)
    return len(rows)
