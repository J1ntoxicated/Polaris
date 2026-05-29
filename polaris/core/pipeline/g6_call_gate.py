"""#15 — G6 GPT call-efficiency gate (cooldown + context-change trigger).

Problem (Jin 2026-05-29): G6 Position Monitor called gpt-5.5 for *every* open
position on *every* 5s tick — 2880 calls/24h with a single position, growing
linearly with position count, ~96% of total GPT spend (~$4.22/24h). Most of
those calls re-decide an unchanged context and return the same HOLD.

Fix: gate the GPT call behind a per-position cooldown + context-change check.
We re-call GPT only when either:
  - the cooldown window has elapsed (``cooldown_sec`` **and** ``min_ticks``
    since the last call — a wall-clock OR tick-count refresh), or
  - the decision context changed materially since the last call:
      * price moved beyond ``price_delta_pct`` (|Δ| relative to the anchored
        price at the last call),
      * the regime label changed, or
      * unrealized PnL crossed a 0.5R band edge.

Otherwise we reuse the prior GPT decision (Python fast-path, no call).

This is a **cost** optimisation, not a defensive throttle. AGGRESSIVE bias is
untouched: the hard loss rail and Q8 swap eligibility fire as a Python
fast-path in ``position_monitor_gate`` *before* this gate is consulted, so a
stop hit / swap is never delayed by a reused decision. Decision quality is
preserved because every *material* context change still calls GPT.

Constants are env-tunable (and learner-tunable in a follow-up).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "DEFAULT_G6_COOLDOWN_SEC",
    "DEFAULT_G6_MIN_TICKS",
    "DEFAULT_G6_PNL_BAND_R",
    "DEFAULT_G6_PRICE_DELTA_PCT",
    "G6CallCache",
    "G6CallContext",
    "pnl_band",
    "should_call_gpt",
]

_ENV_COOLDOWN: Final[str] = "POLARIS_G6_GPT_COOLDOWN_SEC"
_ENV_MIN_TICKS: Final[str] = "POLARIS_G6_GPT_MIN_TICKS"
_ENV_PRICE_DELTA: Final[str] = "POLARIS_G6_GPT_PRICE_DELTA_PCT"
_ENV_PNL_BAND: Final[str] = "POLARIS_G6_GPT_PNL_BAND_R"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    return val if math.isfinite(val) and val > 0.0 else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = int(float(raw))
    except ValueError:
        return default
    return val if val > 0 else default


# Defaults: at a 5s tick, 60s cooldown + 12 ticks = one refresh GPT call per
# minute per position when the context is flat — cuts the 5s cadence by ~12x
# (2880 → ~240 calls/24h for a single quiet position) while any real price
# move / regime flip / PnL-band cross still triggers an immediate call.
DEFAULT_G6_COOLDOWN_SEC: Final[float] = _env_float(_ENV_COOLDOWN, 60.0)
DEFAULT_G6_MIN_TICKS: Final[int] = _env_int(_ENV_MIN_TICKS, 12)
DEFAULT_G6_PRICE_DELTA_PCT: Final[float] = _env_float(_ENV_PRICE_DELTA, 0.0015)  # 0.15%
DEFAULT_G6_PNL_BAND_R: Final[float] = _env_float(_ENV_PNL_BAND, 0.5)


def pnl_band(pnl_r: float, *, band_r: float = DEFAULT_G6_PNL_BAND_R) -> int:
    """Bucket an unrealized-PnL R value into a fixed-width signed band index.

    Two PnL values in the same band are "context-equivalent" for G6 — crossing
    a band edge (e.g. 0.49R → 0.51R with a 0.5R band) is a material change.
    """
    if band_r <= 0.0:
        return 0
    return math.floor(pnl_r / band_r)


@dataclass(frozen=True, slots=True)
class G6CallContext:
    """The decision-relevant context for one G6 evaluation of one position."""

    price: float
    regime: str
    pnl_r: float
    now_ts: int
    tick_idx: int


@dataclass(slots=True)
class _Anchor:
    ctx: G6CallContext
    decision: str


@dataclass(slots=True)
class G6CallCache:
    """Per-position memory of the last *GPT* call's context + decision.

    Lives on the loop state so it survives across ticks. ``record`` is called
    only when GPT actually ran (re-anchoring the context); skipped ticks leave
    the anchor untouched so the cooldown/delta windows measure from the last
    real call.
    """

    _anchors: dict[str, _Anchor] = field(default_factory=dict)

    def record(self, position_id: str, ctx: G6CallContext, *, decision: str) -> None:
        self._anchors[position_id] = _Anchor(ctx=ctx, decision=decision)

    def last_decision(self, position_id: str) -> str | None:
        anchor = self._anchors.get(position_id)
        return anchor.decision if anchor is not None else None

    def anchor(self, position_id: str) -> G6CallContext | None:
        anchor = self._anchors.get(position_id)
        return anchor.ctx if anchor is not None else None

    def drop(self, position_id: str) -> None:
        """Forget a closed position so the cache does not grow unbounded."""
        self._anchors.pop(position_id, None)

    def prune(self, keep: set[str]) -> None:
        """Retain only the given (still-open) position ids."""
        for pid in [p for p in self._anchors if p not in keep]:
            self._anchors.pop(pid, None)


def should_call_gpt(
    cache: G6CallCache,
    position_id: str,
    ctx: G6CallContext,
    *,
    cooldown_sec: float = DEFAULT_G6_COOLDOWN_SEC,
    min_ticks: int = DEFAULT_G6_MIN_TICKS,
    price_delta_pct: float = DEFAULT_G6_PRICE_DELTA_PCT,
    band_r: float = DEFAULT_G6_PNL_BAND_R,
) -> bool:
    """Decide whether G6 should issue a fresh GPT call for this position.

    Returns ``True`` (call GPT) when there is no prior anchor, when the
    cooldown elapsed (wall-clock OR tick-count), or when the context changed
    materially (price |Δ| ≥ threshold / regime flip / PnL-band cross).
    Returns ``False`` (reuse the prior decision) otherwise.
    """
    prev = cache.anchor(position_id)
    if prev is None:
        return True

    # Cooldown refresh: a periodic re-confirmation even on a flat context, so a
    # stale HOLD cannot persist indefinitely. Either window alone forces a call.
    if (ctx.now_ts - prev.now_ts) >= cooldown_sec:
        return True
    if (ctx.tick_idx - prev.tick_idx) >= min_ticks:
        return True

    # Context-change triggers.
    if ctx.regime != prev.regime:
        return True
    if pnl_band(ctx.pnl_r, band_r=band_r) != pnl_band(prev.pnl_r, band_r=band_r):
        return True
    if prev.price > 0.0:
        rel = abs(ctx.price - prev.price) / prev.price
        if rel >= price_delta_pct:
            return True

    return False
