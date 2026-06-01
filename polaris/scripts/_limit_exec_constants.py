"""#7 maker/limit-execution tuning constants (OKX entry leg).

Single source of truth for the post-only-limit-at-touch entry path. The entry
previously went straight to a ``market`` order (taker fee 0.06 % + crossing the
spread = double leak). The new path posts a **maker** limit at the passive
touch (best bid for a buy, best ask for a sell) so a fill pays the maker fee
(0.02 %) and zero spread-cross, then **falls back to market** if it does not
fill inside ``LIMIT_FILL_WAIT_SEC`` — the fallback always closes the entry, so
flow is preserved (AGGRESSIVE; this is not a throttle).

learner-tunable: ``LIMIT_FILL_WAIT_SEC`` and ``STRONG_SIGNAL_STRENGTH`` are
candidates for Layer 5 to tune once the edge-validation cost overlay measures
maker fill-rate vs. opportunity cost per regime. Env overrides let a paper
run sweep them without a redeploy.
"""

from __future__ import annotations

import os
from typing import Final

__all__ = [
    "LIMIT_FILL_WAIT_SEC",
    "LIMIT_POLL_DELAY_SEC",
    "OKX_BALANCE_CLAMP_BUFFER_FRAC",
    "OKX_CLOSE_CAP_BUFFER_FRAC",
    "OKX_MAX_MARKET_NOTIONAL_USDT",
    "STRONG_SIGNAL_STRENGTH",
    "limit_fill_wait_sec",
    "strong_signal_strength",
]

# OKX SPOT VENUE RULE (not our choice, not tunable): a single market order's
# quote value may not exceed 1000 USDT (reject 51201 "The value of a market
# order can't exceed 1000USDT"). A larger entry must be split into sequential
# child market orders each <= this cap. Encoding the venue rule so a >1000 USDT
# entry actually FILLS (flow_not_block) instead of being 100 % rejected — this
# is a bug fix, NOT a sizing-parameter change (the sized notional is unchanged;
# it is delivered in <=1000 USDT chunks).
OKX_MAX_MARKET_NOTIONAL_USDT: Final[float] = 1_000.0

# CLOSE-LEG ONLY downward buffer on the 1000-USDT cap. The venue evaluates the
# 51201 cap on child_base × the LIVE market price at submit time, not the mark
# we sized the child off. An appreciated long whose price drifts up between
# split-time and venue-eval can tip a cap-sized child over 1000 → 51201 → the
# first child Nones → the close never converges (recomputes the same oversized
# chunks every tick). Sizing each full close child off cap × (1 - this frac)
# leaves headroom so modest upward drift cannot breach the hard cap. Entry is
# unbuffered (it sends quote-ccy notional the venue funds exactly). This is a
# fill-ENABLING headroom (lets a risen-price close actually execute), NOT a cut.
OKX_CLOSE_CAP_BUFFER_FRAC: Final[float] = 0.10

# When clamping the OKX entry to the live available USDT balance (reject 51008
# "insufficient balance"), leave this fraction as headroom so OKX's own
# fee/precision accounting can never push the cash buy over the wallet balance.
# This is a fill-ENABLING clamp toward what the venue can actually fund (lets
# SOME order through instead of 100 % rejection), NOT a defensive size cut.
OKX_BALANCE_CLAMP_BUFFER_FRAC: Final[float] = 0.01


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    return val if val > 0.0 else default


# Max seconds to wait for the post-only limit to fill before cancel + market
# fallback. ~3 s keeps intraday signals fresh while giving a maker order a
# realistic shot at filling at the touch.
LIMIT_FILL_WAIT_SEC: Final[float] = _env_float("POLARIS_LIMIT_FILL_WAIT_SEC", 3.0)

# Poll cadence while waiting on the limit (REST order-state query interval).
LIMIT_POLL_DELAY_SEC: Final[float] = _env_float("POLARIS_LIMIT_POLL_DELAY_SEC", 0.5)

# Signal strength at/above which we SKIP the limit and go straight to market —
# a strong signal must not risk missing the move waiting on a maker fill
# (AGGRESSIVE). Reuses the sizing engine's top anchor (continuous_scalar caps
# at strength >= 1.5), so the strong tier is defined consistently.
STRONG_SIGNAL_STRENGTH: Final[float] = _env_float(
    "POLARIS_STRONG_SIGNAL_STRENGTH", 1.5
)


def limit_fill_wait_sec() -> float:
    """Resolve the limit fill-wait at call time (env override honoured)."""
    return _env_float("POLARIS_LIMIT_FILL_WAIT_SEC", LIMIT_FILL_WAIT_SEC)


def strong_signal_strength() -> float:
    """Resolve the strong-signal market-skip threshold at call time."""
    return _env_float("POLARIS_STRONG_SIGNAL_STRENGTH", STRONG_SIGNAL_STRENGTH)
