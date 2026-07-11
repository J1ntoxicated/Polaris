"""G4 TTM Squeeze watch-feature SHADOW (frontgate-scan item #9, behavior-0).

DEMO/PAPER, virtual capital. Deterministic BB-in-Keltner squeeze on/release
detector, computed purely from ``bars`` (no strategy/GPT wiring) and logged
via ``gate_shadow_events`` (``shadow_log.log_shadow_event`` — the JUDGE
pattern) so it is live-recorded regardless of ``POLARIS_AI_FREE`` mode.
TAG-ONLY: never returned as a decision, never mutates ``det_result``/payload
(frontgate integration-blueprint invariant ①).

Canonical TTM Squeeze definition (John Carter): squeeze ON when the
20-period Bollinger Bands (``k=2.0``) sit entirely INSIDE the 20-period
Keltner Channel (``ATR x 1.5``). Both bands reuse existing production
indicators — ``compute_bollinger`` for BB, ``compute_ma`` (SMA-20) + the
canonical ``ATR x multiplier`` formula (``compute_atr_pct(n=20) *
last_price``) for the Keltner center/width (verifier note #3) — no new EMA
function. Release direction reuses ``compute_momentum`` (verifier note #4).

Logged via the EXISTING ``gate_shadow_events`` table (verifier note #1) —
NOT a new table (unlike item #6's VWAP shadow, whose columns don't fit
``gate_shadow_events`` at all). ``gpt_decision=None`` pins ``mismatch=0``
always (this is a watch tag, not a technical-vs-GPT comparison).
``technical_flags`` carries the squeeze state; ``payload["watch_flags"]``
(display-only) is NEVER touched by this module — see verifier note #6.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from polaris.core.indicators.production import (
    compute_atr_pct,
    compute_bollinger,
    compute_ma,
    compute_momentum,
)
from polaris.strategies.base import BarView

__all__ = [
    "BB_STDDEV_K",
    "KELTNER_ATR_MULT",
    "SQUEEZE_LOOKBACK",
    "SqueezeState",
    "compute_squeeze_state",
]

# Canonical TTM Squeeze parameters (John Carter) — 20-period BB(k=2.0) inside
# 20-period Keltner(ATR x 1.5). Not tunable per-ticker in this shadow wave.
SQUEEZE_LOOKBACK: Final[int] = 20
BB_STDDEV_K: Final[float] = 2.0
KELTNER_ATR_MULT: Final[float] = 1.5

_NO_SQUEEZE = "no_squeeze"
_SQUEEZE_ON = "squeeze_on"
_RELEASE_BULLISH = "squeeze_release_bullish"
_RELEASE_BEARISH = "squeeze_release_bearish"
_RELEASE_NEUTRAL = "squeeze_release_neutral"


@dataclass(frozen=True, slots=True)
class SqueezeState:
    """One BB-in-Keltner squeeze read (current bar vs the prior bar)."""

    squeeze_on: bool
    released: bool  # True only on the ON -> OFF transition (this bar)
    momentum: float
    reason: str  # squeeze_on | squeeze_release_{bullish,bearish,neutral} | no_squeeze


def _squeeze_on_at(bars: list[BarView]) -> bool | None:
    """BB(20,2.0) fully inside Keltner(20, ATR x 1.5) for the LAST bar of
    ``bars``. ``None`` when the window is too short for either indicator."""
    if len(bars) < SQUEEZE_LOOKBACK + 1:
        return None
    closes = [b.close for b in bars]
    last_price = closes[-1]
    if last_price <= 0.0:
        return None
    bb_low, _bb_mid, bb_up = compute_bollinger(closes, n=SQUEEZE_LOOKBACK, k=BB_STDDEV_K)
    kc_mid = compute_ma(closes, SQUEEZE_LOOKBACK)
    atr_pct = compute_atr_pct(bars, n=SQUEEZE_LOOKBACK)
    kc_width = atr_pct * last_price * KELTNER_ATR_MULT
    kc_low, kc_up = kc_mid - kc_width, kc_mid + kc_width
    return bb_low > kc_low and bb_up < kc_up


def compute_squeeze_state(bar_views: list[BarView]) -> SqueezeState:
    """Squeeze on/release read over the known-at-time ``bar_views`` window.

    ``released`` is the ON(prior bar) -> OFF(current bar) transition only —
    detected by re-running the SAME 20-bar rule one bar earlier (excludes the
    last bar). Direction on release reuses ``compute_momentum`` (n=20):
    positive -> bullish, negative -> bearish, exactly 0.0 -> neutral.
    """
    current_on = _squeeze_on_at(bar_views)
    if current_on is None:
        return SqueezeState(squeeze_on=False, released=False, momentum=0.0, reason=_NO_SQUEEZE)
    if current_on:
        return SqueezeState(squeeze_on=True, released=False, momentum=0.0, reason=_SQUEEZE_ON)
    prior_on = _squeeze_on_at(bar_views[:-1])
    if not prior_on:
        return SqueezeState(squeeze_on=False, released=False, momentum=0.0, reason=_NO_SQUEEZE)
    momentum = compute_momentum([b.close for b in bar_views], n=SQUEEZE_LOOKBACK)
    if momentum > 0.0:
        reason = _RELEASE_BULLISH
    elif momentum < 0.0:
        reason = _RELEASE_BEARISH
    else:
        reason = _RELEASE_NEUTRAL
    return SqueezeState(squeeze_on=False, released=True, momentum=momentum, reason=reason)
