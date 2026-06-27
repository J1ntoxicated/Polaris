"""Layer 3 — session clock (UTC ts → asia/eu/us).

Spec source: vault/30_components/layer-3-sizing-risk.md (T4 chain) +
.claude/plans/p0_l5_l3_sizing_wire.md (Design — Session derivation).

Deterministic mapping used at sizing time when ``SignalIntent.session`` is None.
The string is also the lookup key for ``SessionMultLearner.get_mult``.

Bands (UTC, half-open ``[lo, hi)``):
    asia : 00-08
    eu   : 07-16
    us   : 13-22

Overlap (07, 13-15): the band whose mid-window is closest to the current hour
wins; tie → alphabetical (asia, eu, us). Closed band (22-24) → asia (start of
next asia session).
"""

from __future__ import annotations

import datetime as dt

from polaris.core.sessions.equity_session_gate import us_equity_session_state

__all__ = ["derive_session", "resolve_venue_session", "SESSION_LABELS"]

SESSION_LABELS: tuple[str, ...] = ("asia", "eu", "us")

_BANDS: tuple[tuple[str, int, int, float], ...] = (
    # (label, lo_hour_inclusive, hi_hour_exclusive, mid_hour)
    ("asia", 0, 8, 4.0),
    ("eu", 7, 16, 11.5),
    ("us", 13, 22, 17.5),
)


def derive_session(ts: int | float) -> str:
    """Return ``"asia" | "eu" | "us"`` for the UTC hour of ``ts``.

    Deterministic — pure function of integer unix timestamp.
    Non-finite / negative input clamps to 0 (asia).
    """
    try:
        ts_int = int(ts)
    except (TypeError, ValueError, OverflowError):
        return "asia"
    if ts_int < 0:
        ts_int = 0
    hour = (ts_int % 86400) // 3600
    candidates: list[tuple[float, str]] = []
    for label, lo, hi, mid in _BANDS:
        if lo <= hour < hi:
            candidates.append((abs(hour - mid), label))
    if not candidates:
        return "asia"
    candidates.sort()
    return candidates[0][1]


# ---------------------------------------------------------------------------
# Venue-native session (D2) — the session label a learner buckets on must be
# REAL per venue, not one crypto-centric asia/eu/us UTC band stamped on every
# trade. The legacy ``derive_session`` band pinned non-crypto venues to an
# irrelevant bucket (and the production close path hardcoded ``"asia"``), so the
# session_mult learner never saw a meaningful split and stayed at the floor.
#
# This is a LABEL resolver only — it returns the bucket key, never a multiplier,
# size cut, or block (flow_not_block). Aggressive bias preserved: nothing here
# dampens notional.
#
#   okx     -> "always_on"  (crypto 24/7; one stable bucket)
#   alpaca  -> us_equity_cal RTH state (rth/pre_market/after_hours/closed),
#              DST-correct via the existing equity clock.
#   capital -> FX/indices weekend calendar: "fx_open" | "fx_weekend".
#   other   -> legacy ``derive_session`` band (no error, no block).
# ---------------------------------------------------------------------------

_OKX_SESSION_LABEL = "always_on"
_CAPITAL_OPEN = "fx_open"
_CAPITAL_WEEKEND = "fx_weekend"
# FX/indices weekend close: Friday 22:00 UTC -> Sunday 22:00 UTC (mirrors
# ``cfd_market_open_at`` in the pre-entry stream guard — kept inline here so the
# low-level sizing layer takes no import from the higher pipeline layer).
_FX_WEEKEND_REOPEN_HOUR_UTC = 22


def _capital_session(ts: int | float) -> str:
    try:
        ts_int = int(ts)
    except (TypeError, ValueError, OverflowError):
        ts_int = 0
    if ts_int < 0:
        ts_int = 0
    d = dt.datetime.fromtimestamp(ts_int, tz=dt.UTC)
    weekday = d.weekday()  # Mon=0 .. Sun=6
    if weekday == 5:  # Saturday — fully closed
        return _CAPITAL_WEEKEND
    if weekday == 6:  # Sunday — reopens 22:00 UTC
        return _CAPITAL_OPEN if d.hour >= _FX_WEEKEND_REOPEN_HOUR_UTC else _CAPITAL_WEEKEND
    if weekday == 4:  # Friday — closes 22:00 UTC
        return _CAPITAL_OPEN if d.hour < _FX_WEEKEND_REOPEN_HOUR_UTC else _CAPITAL_WEEKEND
    return _CAPITAL_OPEN  # Mon-Thu — open


def resolve_venue_session(venue: str, ts: int | float) -> str:
    """Return the VENUE-NATIVE session bucket label for ``(venue, ts)``.

    Used at BOTH trade-close (learner update) and sizing-time (learner lookup)
    so the keys match. Pure function of venue + UTC timestamp; never an error,
    never a multiplier. Unknown venue degrades to the legacy crypto band.
    """
    v = (venue or "").strip().lower()
    if v == "okx":
        return _OKX_SESSION_LABEL
    if v == "alpaca":
        return us_equity_session_state(ts)
    if v == "capital":
        return _capital_session(ts)
    return derive_session(ts)
