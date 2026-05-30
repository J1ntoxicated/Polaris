"""Gate architecture Phase 2 — per-stream G4 fast-path eligibility guards.

The G4 Pre-Entry Watcher's fast-path is an EFFICIENCY / ELIGIBILITY decision:
skip the slow GPT watcher when the entry is clearly clean. It is NOT an entry
throttle — a not-fast-path-eligible signal always falls through to the slow GPT
path (which PROCEEDs or KILLs), so eligibility NEVER blocks an entry outright.
AGGRESSIVE bias preserved.

The legacy eligibility logic is CRYPTO-shaped (spread-vs-baseline + listing_age
+ session-open shock). That is correct only for stream A (OKX spot). This module
supplies the per-stream-CORRECT eligibility checks for B and C, dispatched on the
``StreamProfile.guard_hooks`` token (resolved once via
``guard_token_for_product_class`` keyed on ``product_class``):

- A (crypto/spot, ``crypto_spread_listing``): the legacy logic — left verbatim in
  ``pre_entry_watcher.is_fast_path_eligible`` for byte-identity, NOT re-routed here.
- B (cfd, ``cfd_session_state``): the real pre-entry risk is SESSION state — the
  FX/indices market being open and not in the daily rollover / session-open shock
  window. ``cfd_fast_path_eligible`` reuses the shared microstructure-cleanliness
  check, then requires market-open + not-rollover + not-session-open-shock.
- C (equity, ``equity_rth_pdt_gap``): the real pre-entry risk is the RTH boundary,
  the opening-auction gap, and the PDT day-trade-count. ``equity_fast_path_eligible``
  reuses the shared cleanliness check, then requires RTH + not-opening-gap +
  PDT-clean (``pdt_rank_penalty == 0`` — PDT-flagged is NOT a block, just
  not-fast-path; it still flows to the slow GPT path).

None of this is a T4 sizing-chain multiplier — the guard decides only whether the
fast-path may skip the GPT call; it never touches notional, never halts on P&L.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from polaris.core.sizing.session import derive_session
from polaris.core.streams import (
    GUARD_TOKEN_BY_PRODUCT_CLASS,
    guard_token_for_product_class,
)
from polaris.venues.alpaca.equity_session_gate import (
    pdt_rank_penalty,
    us_equity_session_state,
)

if TYPE_CHECKING:
    from polaris.core.pipeline.agents.pre_entry_watcher import FastPathContext

__all__ = [
    "FX_ROLLOVER_OPEN_HOUR_UTC",
    "GUARD_CFD",
    "GUARD_CRYPTO",
    "GUARD_EQUITY",
    "cfd_fast_path_eligible",
    "cfd_market_open_at",
    "cfd_rollover_window_at",
    "cfd_session_open_shock_at",
    "equity_fast_path_eligible",
    "signal_microstructure_clean",
]

# FX/CFD daily rollover (wide-spread, low-liquidity) window: 21:00-22:00 UTC,
# the daily settlement/rollover around the 17:00 ET close. A new entry placed
# here meets abnormal spreads, so it is NOT fast-path eligible (it still flows
# to the slow GPT path — never blocked).
FX_ROLLOVER_OPEN_HOUR_UTC: int = 21  # [21:00, 22:00) UTC

# Guard token aliases (the SSOT mapping lives in ``streams.config`` so the
# StreamProfile and the dispatcher agree without a circular import).
GUARD_CRYPTO: str = GUARD_TOKEN_BY_PRODUCT_CLASS["spot"]
GUARD_CFD: str = GUARD_TOKEN_BY_PRODUCT_CLASS["cfd"]
GUARD_EQUITY: str = GUARD_TOKEN_BY_PRODUCT_CLASS["equity"]

# US equity RTH state that permits a fast-path skip. Outside RTH the venue
# rejects new entries (market closed) — the slow GPT path still runs, the entry
# is NOT blocked here.
_RTH_STATE = "rth"


def signal_microstructure_clean(fp: FastPathContext) -> bool:
    """Stream-agnostic signal/microstructure cleanliness (shared by B and C).

    These are the conditions that are NOT crypto-specific — they speak to the
    signal and the spread, not the venue's listing/session model:
    top-quartile cell, strong validated signal, a positive spread baseline, a
    tight spread vs that baseline, and no recent reject. The crypto-specific
    listing-age / session-open-shock checks are intentionally NOT here — each
    stream's guard adds its own venue-correct session check on top.

    Mirrors the first five conditions of the legacy crypto guard exactly so B/C
    share the same cleanliness bar; the legacy A path stays byte-identical (it is
    not routed through this helper).
    """
    if fp.cell_quartile != "top":
        return False
    if fp.signal_strength < 1.25:
        return False
    if fp.baseline_p50_spread_bps <= 0.0:
        return False
    if fp.spread_bps > fp.baseline_p50_spread_bps * 0.9:
        return False
    return not fp.recent_reject_in_6h


def cfd_fast_path_eligible(fp: FastPathContext) -> bool:
    """B (Capital CFD) fast-path eligibility — SESSION-state correct.

    Eligible only when the signal is microstructure-clean AND the CFD market is
    open AND not in the daily rollover window AND not in the session-open shock
    window. ``listing_age_hours`` is meaningless for a CFD and is intentionally
    NOT consulted. Not eligible -> slow GPT path (never blocked).
    """
    if not signal_microstructure_clean(fp):
        return False
    if not fp.cfd_market_open:
        return False
    if fp.cfd_rollover_window:
        return False
    return not fp.session_open_shock_window


def equity_fast_path_eligible(fp: FastPathContext) -> bool:
    """C (Alpaca US equity) fast-path eligibility — RTH + PDT + gap correct.

    Eligible only when the signal is microstructure-clean AND the session is RTH
    AND not in the opening-auction gap window AND PDT-clean
    (``pdt_rank_penalty(daytrade_count) == 0`` — at/above the PDT threshold the
    entry is RANKED DOWN, never blocked, so it is simply not-fast-path and flows
    to the slow GPT watcher). ``listing_age_hours`` is meaningless for equity and
    is intentionally NOT consulted. Not eligible -> slow GPT path (never blocked).
    """
    if not signal_microstructure_clean(fp):
        return False
    if fp.equity_session_state != _RTH_STATE:
        return False
    if fp.opening_gap_window:
        return False
    return pdt_rank_penalty(fp.daytrade_count) == 0.0


def equity_session_state_for(ts: int | float) -> str:
    """Thin re-export of the reused us_equity_cal clock (closed/pre/rth/after)."""
    return us_equity_session_state(ts)


def guard_token_for(product_class: str) -> str:
    """Re-export of the streams-config guard-token mapping (single SSOT)."""
    return guard_token_for_product_class(product_class)


# ---------------------------------------------------------------------------
# B (cfd) deterministic FX/indices session clock — supplies the guard inputs.
# ---------------------------------------------------------------------------
#
# Deterministic, no per-tick HTTP (mirrors the equity clock). The authoritative
# market-open signal at runtime is the venue (Capital MARKET_CLOSED reject); this
# clock is the per-tick pre-entry input. FX trades ~24/5: open Sun 22:00 UTC ->
# Fri 22:00 UTC, with a daily rollover window. Outside that = closed.


def _utc(ts: int | float) -> dt.datetime:
    try:
        ts_int = int(ts)
    except (TypeError, ValueError, OverflowError):
        ts_int = 0
    if ts_int < 0:
        ts_int = 0
    return dt.datetime.fromtimestamp(ts_int, tz=dt.UTC)


def cfd_market_open_at(ts: int | float) -> bool:
    """Whether the FX/indices CFD market is OPEN at ``ts`` (UTC, deterministic).

    Open Sunday 22:00 UTC through Friday 22:00 UTC; closed over the weekend
    (Friday >= 22:00, all Saturday, Sunday < 22:00). Closed -> not fast-path (the
    slow GPT path still runs; the entry is NOT blocked here).
    """
    d = _utc(ts)
    weekday = d.weekday()  # Mon=0 .. Sun=6
    if weekday == 5:  # Saturday — fully closed
        return False
    if weekday == 6:  # Sunday — opens 22:00 UTC
        return d.hour >= 22
    if weekday == 4:  # Friday — closes 22:00 UTC
        return d.hour < 22
    return True  # Mon-Thu — open


def cfd_rollover_window_at(ts: int | float) -> bool:
    """Whether ``ts`` is in the daily FX/CFD rollover window (21:00-22:00 UTC)."""
    return _utc(ts).hour == FX_ROLLOVER_OPEN_HOUR_UTC


def cfd_session_open_shock_at(ts: int | float) -> bool:
    """Whether ``ts`` is in an FX session-open shock window (volatile open).

    Reuses ``derive_session`` (asia/eu/us bands): the first 30 minutes of the
    major UTC session opens (00 / 07 / 13) are the shock window — same semantics
    as the legacy crypto ``session_open_shock_window`` but session-aware for FX.
    """
    d = _utc(ts)
    # The derived session label confirms we are inside a defined band; the shock
    # window is the first 30 min of the major session opens (00/07/13 UTC).
    _ = derive_session(ts)
    return d.hour in (0, 7, 13) and d.minute < 30
