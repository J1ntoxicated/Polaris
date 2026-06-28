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
    "POST_ONLY_MAX_REPOSTS",
    "POST_ONLY_REPOST_STEP_BPS",
    "STRONG_SIGNAL_STRENGTH",
    "limit_fill_wait_sec",
    "marketable_limit_cap_bps",
    "post_only_max_reposts",
    "post_only_repost_step_bps",
    "strong_signal_strength",
    "weekend_maker_taker_after_n",
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


def _env_float_nonneg(name: str, default: float) -> float:
    """Like ``_env_float`` but ``0.0`` is a VALID value (an explicit OFF knob).

    Used for the touch-ward step where ``0.0`` means "post at the bare touch"
    (the #77 default), distinct from a malformed override that must fall back.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    return val if val >= 0.0 else default


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


# Max RE-POSTs of an unfilled post-only at the FRESH touch within one entry
# (#77 maker exec layer). The prior path posted ONCE then fell to market — fatal
# to the weekend thin-book edge (the fill IS the edge; a market fallback reverts
# to taker cost). A bounded repost loop (re-resolve the touch, cancel the stale
# rest, re-post) gives a deep passive bid several shots at the flush without ever
# looping unbounded: total attempts = 1 + POST_ONLY_MAX_REPOSTS, each capped by
# ``limit_fill_wait_sec``. Env-tunable for a paper sweep. A non-positive override
# falls back to the default (always ≥1 repost so the loop is meaningful).
POST_ONLY_MAX_REPOSTS: Final[int] = 3


def post_only_max_reposts() -> int:
    """Resolve the post-only repost bound at call time (env override honoured)."""
    raw = os.environ.get("POLARIS_POST_ONLY_MAX_REPOSTS")
    if raw is None or raw == "":
        return POST_ONLY_MAX_REPOSTS
    try:
        val = int(float(raw))
    except ValueError:
        return POST_ONLY_MAX_REPOSTS
    return val if val > 0 else POST_ONLY_MAX_REPOSTS


# TOUCH-WARD REPOST step (#91 maker fill-rate lever a). On a thin weekend book a
# post-only resting at the bare deep bid can sit unfilled forever — the price
# never trades back down to it. This bps step advances each repost's post-only
# price from the deep touch TOWARD the mid (BUY: bid → bid × (1 + step×idx/1e4)),
# clamped STRICTLY below the ask so the order stays a maker (no would-cross). The
# fill still pays the maker fee; it just rests progressively closer to where the
# book actually trades. ``0.0`` (default) = OFF = post at the bare touch every
# repost (byte-identical to #77). Env-tunable for a paper sweep; combine with a
# higher POST_ONLY_MAX_REPOSTS so each extra shot creeps nearer the ask. The
# maker_fill shadow records the worse fill_px so the edge cost stays measured.
POST_ONLY_REPOST_STEP_BPS: Final[float] = _env_float_nonneg(
    "POLARIS_POST_ONLY_REPOST_STEP_BPS", 0.0
)


def post_only_repost_step_bps() -> float:
    """Resolve the touch-ward repost step (bps) at call time. 0.0 = OFF."""
    return _env_float_nonneg("POLARIS_POST_ONLY_REPOST_STEP_BPS", 0.0)


def weekend_maker_taker_after_n() -> int | None:
    """Resolve the weekend cancel-mode taker-fallback threshold at call time.

    Returns the minimum attempt count (1 initial post + reposts) at/above which a
    ``no_fill="cancel"`` strategy falls back to a TAKER market order instead of
    skipping (#91 lever b). Resolution order:

      * ``POLARIS_WEEKEND_MAKER_TAKER_FALLBACK`` truthy → ``1`` (fall back after
        ANY exhausted loop — the unconditional taker-guarantee knob);
      * else ``POLARIS_WEEKEND_MAKER_TAKER_AFTER_N`` → that int (graduated: only
        after N attempts; lets a deep-bid rest for the first few shots, then
        guarantees a fill);
      * else ``None`` = OFF (default) = the #77 skip sentinel (a missed deep bid
        is 0 realised cost — the weekend maker thesis, byte-identical).

    A non-positive / malformed value is OFF (no fallback) — the knob is opt-in.
    """
    raw_flag = os.environ.get("POLARIS_WEEKEND_MAKER_TAKER_FALLBACK", "")
    if raw_flag.strip().lower() in ("1", "true", "yes", "on"):
        return 1
    raw_n = os.environ.get("POLARIS_WEEKEND_MAKER_TAKER_AFTER_N")
    if raw_n is None or raw_n == "":
        return None
    try:
        val = int(float(raw_n))
    except ValueError:
        return None
    return val if val > 0 else None


# ---------------------------------------------------------------------------
# MARKETABLE-LIMIT (momentum / breakout entry) — adverse-fill cap (bps).
#
# A momentum/breakout entry CANNOT rest passively (the price runs away while it
# waits at the touch → non-fill). Instead it crosses the spread with a limit
# capped ``cap_bps`` past the touch: it fills like a taker (keeps the trade) but
# can never fill WORSE than ``touch × (1 + cap_bps/1e4)`` (adverse-fill cap on a
# fast tape). A no-fill / reject / timeout STILL falls back to a plain market
# order — flow_not_block: a maker-miss is never a skipped entry, only (rarely) a
# worse taker.
#
# Tiered by liquidity ([[ab_letrun_maker_2026-06-24]] red-team): a too-tight cap
# (e.g. 2 bps) IOC-no-fills on a breakout and the market fallback then fills
# AFTER the move (worse). BTC/ETH = 5, top-alt = 6, mid = 10, thin/gap = 20.
# Each tier is env-overridable for a paper sweep; the resolver reads the env at
# call time so a run can retune without a redeploy.
# ---------------------------------------------------------------------------
MARKETABLE_LIMIT_CAP_BPS_MAJOR: Final[float] = 5.0
MARKETABLE_LIMIT_CAP_BPS_TOP_ALT: Final[float] = 6.0
MARKETABLE_LIMIT_CAP_BPS_MID: Final[float] = 10.0
MARKETABLE_LIMIT_CAP_BPS_THIN: Final[float] = 20.0

# OKX bases (split off the inst_id, e.g. ``BTC-USDT`` → ``BTC``) that get the
# tightest major cap and the next top-alt tier. Everything else defaults to the
# mid cap; thin/gap is reserved for an explicit caller hint (no cheap depth
# probe — a future depth-driven bump can route to the thin tier).
_MAJOR_BASES: Final[frozenset[str]] = frozenset({"BTC", "ETH"})
_TOP_ALT_BASES: Final[frozenset[str]] = frozenset(
    {"SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "TON", "TRX"}
)


def _okx_base(inst_id: str) -> str:
    """Base ccy of an OKX inst_id (``BTC-USDT`` → ``BTC``; bare → itself)."""
    sym = inst_id.upper()
    return sym.split("-", 1)[0] if "-" in sym else sym


def marketable_limit_cap_bps(inst_id: str, *, thin: bool = False) -> float:
    """Adverse-fill cap (bps) for a marketable-limit entry on ``inst_id``.

    Tiered by liquidity: BTC/ETH = 5, top-alt = 6, mid = 10, thin/gap = 20.
    ``thin=True`` forces the widest cap (a gap/illiquid hint from the caller).
    Each tier honours its env override at call time (paper-run sweep, no
    redeploy). Never returns <= 0 (a non-positive override falls back to the
    tier default so the cross is always at least the touch).
    """
    if thin:
        return _env_float("POLARIS_MKTLIMIT_CAP_BPS_THIN", MARKETABLE_LIMIT_CAP_BPS_THIN)
    base = _okx_base(inst_id)
    if base in _MAJOR_BASES:
        return _env_float("POLARIS_MKTLIMIT_CAP_BPS_MAJOR", MARKETABLE_LIMIT_CAP_BPS_MAJOR)
    if base in _TOP_ALT_BASES:
        return _env_float(
            "POLARIS_MKTLIMIT_CAP_BPS_TOP_ALT", MARKETABLE_LIMIT_CAP_BPS_TOP_ALT
        )
    return _env_float("POLARIS_MKTLIMIT_CAP_BPS_MID", MARKETABLE_LIMIT_CAP_BPS_MID)


def strong_signal_strength() -> float:
    """Resolve the strong-signal market-skip threshold at call time."""
    return _env_float("POLARIS_STRONG_SIGNAL_STRENGTH", STRONG_SIGNAL_STRENGTH)
