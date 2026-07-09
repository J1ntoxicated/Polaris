"""Polaris dashboard v1 — VIRTUAL-ledger-scoped main-board aggregates.

Bug fix (Jin 2026-07-08, dashboard-live-net hunt): the always-visible MAIN
board (desktop header + mobile status strip) must show numbers scoped to the
fresh $100k-per-exchange VIRTUAL ledger when ``POLARIS_VIRTUAL_ACCOUNT=1``
(Jin 2026-07-07) — not a raw unfiltered scan of the whole ``fills`` table,
which still carries the pre-virtual real-roundtrip history (a DIFFERENT
accounting regime: real venue orders + reconciliation, vs the virtual
internal-ledger simulation). The existing global ``daily_pnl_usd`` /
``session_pnl_usd`` / ``since_reset`` snapshot fields stay UNCHANGED
(byte-identical) — they now serve the LEGACY tab ONLY.

These ``virtual_*`` functions are additive, scoped per venue via the SAME
anchor ``virtual_account_equity.virtual_equity_now`` already uses (epoch
unless a ruin re-seed fired for that venue), aggregated across the 3
registered venues (okx / capital / alpaca). Zero venue calls — internal fills
ledger only. Display-only; NEVER feeds sizing/gating/exit.
"""

from __future__ import annotations

import sqlite3
from typing import Final

from polaris.scripts.dashboard.snapshot_models import SinceResetRollup
from polaris.scripts.dashboard.snapshot_q_common import _aest_midnight_ms
from polaris.scripts.dashboard.snapshot_q_equity import _realised_pnl_since
from polaris.scripts.dashboard.snapshot_q_reset import (
    _closed_since_reset,
    _rollup_metrics,
)
from polaris.storage.virtual_account_equity import _latest_ruin_anchor

# Registered virtual-ledger venues — mirrors the 3 lanes ``_per_stream_summary``
# seeds (``snapshot_q_streams.py``); not the streams SSOT itself (that keys on
# stream_id, this on the bare venue string the anchor/fills tables use).
_VIRTUAL_VENUES: Final[tuple[str, ...]] = ("okx", "capital", "alpaca")
VIRTUAL_SEED_USD: Final[float] = 100_000.0


def _virtual_anchor(conn: sqlite3.Connection, *, exchange: str) -> tuple[float, int]:
    """(seed/reseeded_to $, anchor_ts) for ``exchange`` — mirrors the anchor
    ``virtual_account_equity.virtual_equity_now`` resolves (epoch/$100k seed
    unless a ruin re-seed fired)."""
    anchor = _latest_ruin_anchor(conn, exchange=exchange)
    if anchor is None:
        return VIRTUAL_SEED_USD, 0
    return anchor


def virtual_daily_pnl_usd(conn: sqlite3.Connection, *, now_s: int) -> tuple[float, int]:
    """Aggregate 'today' (AEST-midnight-floored) realised PnL over the VIRTUAL
    ledger — Σ across the 3 venues, each floored at max(its own virtual anchor,
    the AEST midnight) so a venue's pre-anchor (pre-virtual) history never
    leaks in. Mirrors ``_daily_realised_pnl`` scope-for-scope, just per-venue."""
    aest_floor_ms = _aest_midnight_ms(now_s)
    total_pnl = 0.0
    total_n = 0
    for venue in _VIRTUAL_VENUES:
        _seed, anchor_ts = _virtual_anchor(conn, exchange=venue)
        floor_ms = max(anchor_ts * 1000, aest_floor_ms)
        pnl, n = _realised_pnl_since(conn, lookback_ms=floor_ms, venue=venue)
        total_pnl += pnl
        total_n += n
    return total_pnl, total_n


def virtual_session_pnl_usd(conn: sqlite3.Connection, *, now_s: int) -> tuple[float, int]:
    """Aggregate whole-session (since each venue's OWN virtual anchor) realised
    PnL over the VIRTUAL ledger. Reconciles to Σ per-venue
    ``virtual_account_equity.virtual_equity_now(...).realized_pnl_usd``."""
    total_pnl = 0.0
    total_n = 0
    for venue in _VIRTUAL_VENUES:
        _seed, anchor_ts = _virtual_anchor(conn, exchange=venue)
        pnl, n = _realised_pnl_since(conn, lookback_ms=anchor_ts * 1000, venue=venue)
        total_pnl += pnl
        total_n += n
    return total_pnl, total_n


def virtual_since_reset(conn: sqlite3.Connection) -> SinceResetRollup:
    """VIRTUAL-ledger-scoped since-reset rollup — PF / win% / net$ / avg-R over
    closed trades opened at/after EACH venue's own virtual anchor, aggregated.

    Reuses ``_closed_since_reset``'s per-venue filter (Jin 2026-07-08 addition)
    + the SAME ``_rollup_metrics`` math the legacy ``since_reset`` panel uses,
    so the two rollups are directly comparable. Always returns a (possibly
    zero-valued, never None) rollup — the virtual ledger's own go-live IS its
    reset boundary, so there is no 'no reset stamped' state to fall back from.
    ``cadence_split`` is left empty (not requested; the legacy panel's cadence
    breakdown stays LEGACY-tab-only)."""
    rows = [
        row
        for venue in _VIRTUAL_VENUES
        for row in _closed_since_reset(
            conn, reset_ts=_virtual_anchor(conn, exchange=venue)[1], venue=venue
        )
    ]
    n, pf, net_usd, win_pct, avg_r = _rollup_metrics(rows)
    anchors = [_virtual_anchor(conn, exchange=v)[1] for v in _VIRTUAL_VENUES]
    nonzero_anchors = [a for a in anchors if a > 0]
    reset_ts = min(nonzero_anchors) if nonzero_anchors else 0
    baseline = sum(_virtual_anchor(conn, exchange=v)[0] for v in _VIRTUAL_VENUES)
    return SinceResetRollup(
        reset_ts=reset_ts,
        label="virtual ledger",
        git_sha="",
        equity_baseline_usd=baseline,
        n=n,
        pf=pf,
        net_usd=net_usd,
        win_pct=win_pct,
        avg_r=avg_r,
        equity_change_usd=net_usd,
        cadence_split=[],
    )
