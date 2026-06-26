"""STEP② candidate sweep — orchestration (bucket decomposition + selection).

Split from ``_candidate_sweep.py`` to keep both files ≤500 LOC. This module reads
the static ground per ticker, runs the two-stage score (``_candidate_sweep`` pure
primitives), decomposes the cap-200 active set into buckets, applies venue/session
allocation + hysteresis + fast-track + open-position force-seat, and emits valid
``FocusSelection`` rows.

🚨 flow_not_block / 9-stack / aggressive: the output is ``focus_rank``/``tier``/
``bucket`` ONLY — never a sizing multiplier, never a membership cut. cap WIDENS
the live set; cold-start = penalty (NOT exclusion); fast-track + exploration fight
scan-miss/blindness. Deterministic, AI-free. DEMO/PAPER only.
"""

from __future__ import annotations

import logging
import random
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Protocol

from polaris.core.universe.schema import (
    FocusBucket,
    FocusSelection,
    UniverseInstrument,
    tier_band_boundaries,
)
from polaris.core.universe.watchlist import assign_tiers, score_focus_candidates
from polaris.scripts._candidate_sweep import (
    BUCKET_ANCHOR,
    BUCKET_DYNAMIC,
    BUCKET_EVENT_HOT,
    BUCKET_EXPLORATION,
    COLD_START_MIN_BARS,
    COLD_START_PENALTY,
    HYST_ENTER_DELTA,
    HYST_EXIT_DELTA,
    activation_score,
    apply_hysteresis,
    candidate_score,
    edge_score,
    fast_track_outliers,
)
from polaris.scripts._candidate_sweep_bars import bars_by_resolution, confirmed
from polaris.scripts._session_map import instrument_session_weight
from polaris.scripts._static_ground import read_ticker_ground

logger = logging.getLogger(__name__)

__all__ = ["select_candidate_focus"]


class ActivationProvider(Protocol):
    """The per-symbol motion surface the sweep reads (the quote writer's #39 accumulator).

    Kept as a structural Protocol (not a concrete ``QuoteTickWriter`` import) so the
    pure sweep module has no dependency on the WS writer — the writer satisfies it
    by exposing ``activation_metrics``.
    """

    def activation_metrics(  # pragma: no cover - structural
        self, instrument_id: str
    ) -> dict[str, float | int] | None: ...


def _read_spread_bps(conn: sqlite3.Connection) -> dict[str, float]:
    """Per-symbol ``quote_ticks.spread_bps`` keyed by instrument_id (read-only).

    ``quote_ticks`` is single-row LWW, so this is the latest spread per symbol —
    the spread_tradeability component's source (an ADDITIVE term inside activation,
    never a multiplier). A read error / missing table → empty (degrade to neutral).
    """
    try:
        rows = conn.execute(
            "SELECT instrument_id, spread_bps FROM quote_ticks"
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(r[0]): float(r[1] or 0.0) for r in rows}


def _activation_metrics(
    provider: ActivationProvider | None, instrument_id: str
) -> dict[str, float | int] | None:
    """Read the per-symbol motion snapshot, degrading to None on any miss.

    A ``None`` provider, a provider that does not expose ``activation_metrics``
    (a partial surface, e.g. a technical-lean-only stub), or an instrument with no
    live ticks yet → None (the activation blend's live-motion components stay
    neutral; the name scores on bars+spread alone — flow_not_block, never an error).
    """
    if provider is None or not hasattr(provider, "activation_metrics"):
        return None
    return provider.activation_metrics(instrument_id)


def _score_universe(
    conn: sqlite3.Connection,
    universe: Sequence[UniverseInstrument],
    now_ts: int,
    spread_by_iid: Mapping[str, float],
    activation_provider: ActivationProvider | None,
) -> dict[str, dict[str, float]]:
    """Per-instrument activation/edge/candidate scores + cold-start + session flag.

    Returns ``{instrument_id: {activation, edge, candidate, has_event,
    cold_start, session_weight}}``. activation reads PER-SYMBOL live motion (the
    writer's #39 accumulator, keyed instrument_id) + per-symbol
    ``quote_ticks.spread_bps``, so an intraday-active major (EURUSD 9s tick, US100)
    outranks a calm wide-daily name. A missing writer/quote → those components
    degrade to neutral (the name scores on bars alone, never an error —
    flow_not_block). Cold-start (thin/absent ground OR < N confirmed bars) keeps
    the pre-existing 0.5× penalty APPLIED to ``candidate`` — penalty, NOT
    exclusion, and the ONLY ≤1 multiplier on the candidate (9-stack ban honored).

    ``session_weight`` (#48) is the per-instrument global-session clock ∈ (0, 1]:
    1.0 = inside its active cash session, ``SESSION_DORMANT`` outside it. It is
    NOT folded into ``candidate`` (no 9-stack); it is a SEPARATE seat-allocation
    key the bucket selection reads to ROTATE focus toward whatever market is awake
    now (deprioritize the dormant, never exclude — flow_not_block).
    """
    out: dict[str, dict[str, float]] = {}
    for inst in universe:
        iid = inst.instrument_id
        all_bars = bars_by_resolution(conn, venue=inst.venue, symbol=inst.symbol)
        ground_row = read_ticker_ground(conn, iid)
        ground = ground_row.get("ground") if ground_row else None
        has_event = bool(ground_row and ground_row.get("has_event"))

        motion = _activation_metrics(activation_provider, iid)
        spread_bps = spread_by_iid.get(iid)
        act = activation_score(all_bars, motion, spread_bps, now_ts)
        edge = edge_score(all_bars, ground, now_ts)
        cand = candidate_score(act, edge)

        n_confirmed = sum(
            len(confirmed(all_bars.get(res), now_ts, res))
            for res in ("15m", "1H", "1D")
        )
        cold = (not ground) or n_confirmed < COLD_START_MIN_BARS
        if cold:
            cand *= COLD_START_PENALTY
        session_w = instrument_session_weight(
            inst.venue, inst.asset_class, inst.symbol, now_ts
        )
        out[iid] = {
            "activation": act,
            "edge": edge,
            "candidate": cand,
            "has_event": 1.0 if has_event else 0.0,
            "cold_start": 1.0 if cold else 0.0,
            "session_weight": session_w,
        }
    return out


def _venue_seat_allocation(
    universe: Sequence[UniverseInstrument],
    venue_weights: Mapping[str, float],
    total_seats: int,
    session_by_iid: Mapping[str, float],
) -> dict[str, int]:
    """Split ``total_seats`` across venues by session-weighted presence.

    A venue's seat share ∝ Σ(its rows' per-instrument session weight) × its venue
    weight. The per-instrument session term (#48) means a venue whose names are
    mostly OUT of session right now (e.g. Capital at an Asia hour, with US/Europe
    indices dormant) gets FEWER dynamic seats, and those seats ROTATE to whatever
    venue is awake — allocation, NOT a block: the venue is never zeroed while it
    has rows and any positive weight (the dormant floor keeps the sum positive).
    """
    venue_session_sum: dict[str, float] = {}
    venue_rows: dict[str, int] = {}
    for inst in universe:
        venue_rows[inst.venue] = venue_rows.get(inst.venue, 0) + 1
        venue_session_sum[inst.venue] = venue_session_sum.get(
            inst.venue, 0.0
        ) + session_by_iid.get(inst.instrument_id, 1.0)
    weighted = {
        v: venue_session_sum[v] * max(0.0, venue_weights.get(v, 1.0))
        for v in venue_rows
    }
    total_w = sum(weighted.values())
    if total_w <= 0.0:
        # Degenerate (all weights 0) — fall back to equal split (flow_not_block:
        # never starve a venue to zero when the market is the only signal).
        venues = list(venue_rows)
        base = total_seats // len(venues) if venues else 0
        return {v: base for v in venues}
    return {v: int(total_seats * (w / total_w)) for v, w in weighted.items()}


def select_candidate_focus(
    conn: sqlite3.Connection,
    universe: Sequence[UniverseInstrument],
    *,
    now_ts: int,
    cap: int,
    bucket_counts: Mapping[str, int] | None,
    venue_weights: Mapping[str, float],
    open_targets: Sequence[tuple[str, str, str, str]],
    prev_focus_symbols: set[str],
    rng: random.Random | None = None,
    opportunity_scores: Mapping[str, float] | None = None,
    trade_eligible: Mapping[str, bool] | None = None,
    activation_provider: ActivationProvider | None = None,
) -> list[FocusSelection]:
    """Bucket-decomposed dynamic focus selection (the sweep producer).

    Buckets (env-tunable counts; bucket→FocusBucket-literal mapping documented):
    - **anchor** — top merit via the EXISTING ``score_focus_candidates`` (the base
      engine keeps its proven liquid names); → ``"core"``.
    - **dynamic** — top ``candidate_score``, venue/session allocated (today's
      movers, the main thrust); → ``"core"``.
    - **event_hot** — top by event/sentiment spike (``has_event`` + edge); →
      ``"core"``.
    - **exploration** — SEEDED-random from the ground (anti-blindness); →
      ``"satellite"``.
    De-dup across buckets (highest-priority bucket wins). Open-position symbols are
    FORCE-SEATED into the active set. ``focus_rank`` 1..K by final ordering; tier
    via the EXISTING ``assign_tiers``. flow_not_block: a richer ordering, never a
    membership cut — the producer's output is a different ``focus_rank`` within the
    SAME schema.

    The EntranceJudge ``opportunity_scores`` + ``trade_eligible`` (the WATCH/TRADE
    decouple) are PRESERVED through to the emitted rows when supplied — the judge
    still owns those fields (telemetry + the trade-subset decouple), so the sweep
    only changes ``focus_rank``/``bucket`` ordering, never the eligibility contract.
    A row with no judgment keeps the flow-preserving default (None / eligible).
    """
    if not universe:
        return []
    opportunity_scores = opportunity_scores or {}
    trade_eligible = trade_eligible or {}
    rng = rng or random.Random(now_ts)
    counts = _resolve_bucket_counts(bucket_counts, cap)
    spread_by_iid = _read_spread_bps(conn)
    scored = _score_universe(
        conn, universe, now_ts, spread_by_iid, activation_provider
    )
    cand_by_iid = {iid: s["candidate"] for iid, s in scored.items()}
    session_by_iid = {iid: s["session_weight"] for iid, s in scored.items()}

    merit_list = score_focus_candidates(list(universe))
    by_iid = {inst.instrument_id: inst for inst in universe}
    # Pre-map merit by iid (parallel order) so the anchor sort is O(n log n), not
    # O(n²) — the 1650-row walk would otherwise rebuild list(by_iid) per compare.
    merit_by_iid = {
        inst.instrument_id: merit_list[i] for i, inst in enumerate(universe)
    }

    # Hysteresis stabilizes WHICH names occupy the dynamic set (anti-flicker).
    stable = apply_hysteresis(
        prev_focus_symbols, cand_by_iid, HYST_ENTER_DELTA, HYST_EXIT_DELTA
    )

    chosen: list[str] = []  # ordered, de-duped instrument_ids
    seen: set[str] = set()

    def _seat(iids: Sequence[str], limit: int) -> None:
        added = 0
        for iid in iids:
            if added >= limit:
                break
            if iid in seen:
                continue
            seen.add(iid)
            chosen.append(iid)
            added += 1

    # 1) anchor — top merit rank.
    anchor_order = sorted(by_iid, key=lambda i: merit_by_iid[i], reverse=True)
    _seat(anchor_order, counts["anchor"])

    # 2) dynamic — top candidate_score, venue/session allocated. The per-instrument
    #    session weight (#48) is the PRIMARY sort key so an in-session name seats
    #    ahead of a dormant one (rotation toward the awake market), then the
    #    hysteresis-stable names, then raw candidate order. Dormant names are NOT
    #    dropped — they fall to the seat tail (deprioritize, never exclude).
    dynamic_alloc = _venue_seat_allocation(
        universe, venue_weights, counts["dynamic"], session_by_iid
    )
    for venue, seats in dynamic_alloc.items():
        venue_iids = [
            iid for iid in by_iid
            if by_iid[iid].venue == venue and iid not in seen
        ]
        venue_iids.sort(
            key=lambda i: (
                session_by_iid.get(i, 1.0), i in stable, cand_by_iid.get(i, 0.0)
            ),
            reverse=True,
        )
        _seat(venue_iids, seats)

    # 3) event_hot — event/sentiment spike (has_event then edge).
    event_order = sorted(
        by_iid,
        key=lambda i: (scored[i]["has_event"], scored[i]["edge"]),
        reverse=True,
    )
    _seat(event_order, counts["event_hot"])

    # 4) exploration — SEEDED-random from the ground (anti-blindness).
    explore_pool = [iid for iid in by_iid if iid not in seen]
    rng.shuffle(explore_pool)
    explore_chosen = explore_pool[: counts["exploration"]]
    _seat(explore_chosen, counts["exploration"])

    # 5) fast-track — out-of-focus spikes WIDEN the active set (never a block).
    focus_keys = {(by_iid[i].venue, by_iid[i].symbol) for i in chosen}
    for venue, symbol in fast_track_outliers(universe, cand_by_iid, focus_keys):
        iid = f"{venue}:{symbol}"
        if iid not in seen and iid in by_iid:
            seen.add(iid)
            chosen.append(iid)

    # 6) open-position force-seat — a held name is ALWAYS in the active set.
    for venue, symbol, _ac, _grp in open_targets:
        iid = f"{venue}:{symbol}"
        if iid in seen:
            continue
        if iid in by_iid:
            seen.add(iid)
            chosen.append(iid)

    # Final ordering: held names keep their seat, then in-session names lead
    # (#48 rotation — the rank head is the awake-market mover), then by
    # candidate_score. cap-bounded — held names never trimmed; dormant names keep
    # a seat at the tail (deprioritize, never exclude — flow_not_block).
    held_iids = {f"{v}:{s}" for v, s, _a, _g in open_targets}
    chosen.sort(
        key=lambda i: (
            i in held_iids, session_by_iid.get(i, 1.0), cand_by_iid.get(i, 0.0)
        ),
        reverse=True,
    )
    if len(chosen) > cap:
        kept_held = [i for i in chosen if i in held_iids]
        rest = [i for i in chosen if i not in held_iids][: max(0, cap - len(kept_held))]
        chosen = (kept_held + rest)[:cap]

    bucket_map = _bucket_assignment(chosen, explore_chosen_set=set(explore_chosen))
    tiers = assign_tiers(len(chosen), tier_band_boundaries())
    out = [
        FocusSelection(
            cycle_ts=now_ts,
            venue=by_iid[iid].venue,
            symbol=by_iid[iid].symbol,
            focus_score=float(cand_by_iid.get(iid, 0.0)),
            rank=rank_idx,
            bucket=bucket_map[iid],
            opportunity_score=(
                None if iid not in opportunity_scores
                else float(opportunity_scores[iid])
            ),
            # Flow-preserving default: a row with no judgment stays trade-eligible
            # (the WATCH/TRADE decouple, owned by the judge — never a sweep block).
            trade_eligible=bool(trade_eligible.get(iid, True)),
            tier=tiers[rank_idx - 1],
        )
        for rank_idx, iid in enumerate(chosen, start=1)
    ]
    logger.info(
        "[sweep] candidate_focus: universe=%d → selected=%d (cap=%d) buckets=%s",
        len(universe), len(out), cap, _bucket_summary(bucket_map),
    )
    return out


def _resolve_bucket_counts(
    bucket_counts: Mapping[str, int] | None, cap: int
) -> dict[str, int]:
    """Resolve env defaults, then scale to ``cap`` if the sum overshoots it."""
    counts = {
        "anchor": BUCKET_ANCHOR,
        "dynamic": BUCKET_DYNAMIC,
        "event_hot": BUCKET_EVENT_HOT,
        "exploration": BUCKET_EXPLORATION,
    }
    if bucket_counts is not None:
        counts = {k: int(bucket_counts.get(k, counts[k])) for k in counts}
    total = sum(counts.values())
    if total > cap and total > 0:
        # Proportionally scale down so the buckets fit the cap (the dynamic
        # bucket absorbs the remainder — it is the main thrust).
        scaled = {k: int(cap * v / total) for k, v in counts.items()}
        scaled["dynamic"] += cap - sum(scaled.values())
        return scaled
    return counts


def _bucket_assignment(
    chosen: Sequence[str], *, explore_chosen_set: set[str]
) -> dict[str, FocusBucket]:
    """Map each chosen iid to a VALID FocusBucket literal (schema-safe).

    anchor/dynamic/event_hot/fast-track/held → ``"core"``; exploration →
    ``"satellite"``. The internal 4-bucket detail is carried only in the log
    summary — the persisted ``target_bucket`` stays a valid FocusBucket so the
    schema + ``persist_focus`` are untouched.
    """
    out: dict[str, FocusBucket] = {}
    for iid in chosen:
        out[iid] = "satellite" if iid in explore_chosen_set else "core"
    return out


def _bucket_summary(bucket_map: Mapping[str, FocusBucket]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for b in bucket_map.values():
        summary[b] = summary.get(b, 0) + 1
    return summary
