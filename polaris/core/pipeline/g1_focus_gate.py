"""G1-EFF — G1 Universe Scanner GPT call-efficiency gate (on-change-only).

Problem (Jin 2026-05-30 audit): G1 Universe Scanner called gpt-5-mini once per
*validated signal* per tick (``run_pipeline_for_signal`` starts at G1), each time
re-deciding the SAME focus list from the SAME ``universe_rows`` snapshot —
~876 calls/4.1h = 56.5% of all GPT spend, structurally ALWAYS PASS, avg 3.35s.
The focus decision is identical across those calls when the universe composition
has not moved.

Fix: gate the G1 GPT call behind a cooldown + composition-change check (mirrors
the proven ``g6_call_gate``). We re-call GPT only when either:
  - the cooldown window has elapsed (``cooldown_sec`` since the last call), or
  - the universe composition changed materially since the last call:
      * a new listing appeared (or one was delisted) — membership SET differs, or
      * top-K (by 24h volume) membership turnover ≥ ``turnover_threshold``.
Otherwise we reuse the prior GPT focus list (Python fast-path, no call).

This is an **efficiency** optimisation, not a defensive throttle. The focus
DECISION is preserved — GPT still chooses the focus list; only the call
FREQUENCY drops. AGGRESSIVE bias is untouched: G1 STILL always PASS (it is a
focus SELECTOR, not a KILL gate), no entry is blocked, no size is cut.
Conservative: when in doubt (cold cache / new listing / delisting / shrinking
universe / parse trouble) we CALL GPT so the focus list is never starved.

Constants are env-tunable (and learner-tunable in a follow-up).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Final

__all__ = [
    "DEFAULT_G1_COOLDOWN_SEC",
    "DEFAULT_G1_TURNOVER_THRESHOLD",
    "DEFAULT_G1_TURNOVER_TOP_K",
    "CompositionFingerprint",
    "G1FocusCache",
    "composition_fingerprint",
    "should_call_gpt_g1",
]

_ENV_COOLDOWN: Final[str] = "POLARIS_G1_GPT_COOLDOWN_SEC"
_ENV_TURNOVER: Final[str] = "POLARIS_G1_GPT_TURNOVER_THRESHOLD"
_ENV_TOP_K: Final[str] = "POLARIS_G1_GPT_TURNOVER_TOP_K"


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


# Defaults: at a 5s tick, a 300s (5 min) cooldown caps a flat-universe G1 to one
# refresh per 5 min regardless of how many signals fire that tick — this is the
# dominant saving (per-signal re-decide → once per composition window). Any new
# listing / delisting / ≥25% top-K turnover still triggers an immediate call so
# the focus is never stale. The dynamic universe itself refreshes every 5 min
# (Layer 0), so the cooldown is aligned to the listing-change cadence.
DEFAULT_G1_COOLDOWN_SEC: Final[float] = _env_float(_ENV_COOLDOWN, 300.0)
DEFAULT_G1_TURNOVER_THRESHOLD: Final[float] = _env_float(_ENV_TURNOVER, 0.25)  # 25%
DEFAULT_G1_TURNOVER_TOP_K: Final[int] = _env_int(_ENV_TOP_K, 40)


def _instrument(row: dict[str, Any]) -> str:
    return f"{row.get('venue')}:{row.get('symbol')}"


@dataclass(frozen=True, slots=True)
class CompositionFingerprint:
    """Order-independent identity of a universe composition.

    ``symbols`` is the full membership SET (any add/remove is a material change).
    ``top_k`` is the top-K-by-volume membership SET (a high-volume churn beyond
    the turnover threshold is a material change even when the SET is unchanged).
    """

    symbols: frozenset[str]
    top_k: frozenset[str]


def composition_fingerprint(
    universe: list[dict[str, Any]],
    *,
    top_k: int = DEFAULT_G1_TURNOVER_TOP_K,
) -> CompositionFingerprint:
    """Snapshot the universe membership + its top-K-by-volume head."""
    members = frozenset(_instrument(r) for r in universe)
    ranked = sorted(
        universe, key=lambda r: float(r.get("vol_24h_usd", 0.0)), reverse=True
    )
    head = frozenset(_instrument(r) for r in ranked[: max(1, top_k)])
    return CompositionFingerprint(symbols=members, top_k=head)


def _turnover(prev: frozenset[str], curr: frozenset[str]) -> float:
    """Fraction of ``prev`` top-K membership that churned out (0.0–1.0)."""
    if not prev:
        return 1.0
    churned = len(prev - curr)
    return churned / len(prev)


@dataclass(slots=True)
class _Anchor:
    fingerprint: CompositionFingerprint
    focus: list[str]
    now_ts: int


@dataclass(slots=True)
class G1FocusCache:
    """Process-wide memory of the last G1 *GPT* focus decision + its context.

    Lives on the loop state so it survives across signals AND ticks. ``record``
    is called only when GPT actually ran (re-anchoring the composition); skipped
    cycles leave the anchor untouched so the cooldown window measures from the
    last real call. There is a single anchor — G1 picks one focus list for the
    whole universe (unlike G6's per-position cache).
    """

    _anchor: _Anchor | None = field(default=None)

    def record(
        self,
        universe: list[dict[str, Any]],
        *,
        focus: list[str],
        now_ts: int,
        top_k: int = DEFAULT_G1_TURNOVER_TOP_K,
    ) -> None:
        self._anchor = _Anchor(
            fingerprint=composition_fingerprint(universe, top_k=top_k),
            focus=list(focus),
            now_ts=now_ts,
        )

    def last_focus(self) -> list[str] | None:
        return list(self._anchor.focus) if self._anchor is not None else None

    def anchor_fingerprint(self) -> CompositionFingerprint | None:
        return self._anchor.fingerprint if self._anchor is not None else None

    def anchor_ts(self) -> int | None:
        return self._anchor.now_ts if self._anchor is not None else None


def should_call_gpt_g1(
    cache: G1FocusCache,
    universe: list[dict[str, Any]],
    *,
    now_ts: int,
    cooldown_sec: float = DEFAULT_G1_COOLDOWN_SEC,
    turnover_threshold: float = DEFAULT_G1_TURNOVER_THRESHOLD,
    top_k: int = DEFAULT_G1_TURNOVER_TOP_K,
) -> bool:
    """Decide whether G1 should issue a fresh GPT focus call.

    Returns ``True`` (call GPT) when there is no prior anchor, the cooldown
    elapsed, the membership SET changed (new/removed listing), or the top-K
    membership turnover ≥ ``turnover_threshold``. Returns ``False`` (reuse the
    cached focus) only when the composition is materially unchanged inside the
    cooldown. Conservative on missing/empty data → call GPT (never starve focus).
    """
    prev = cache.anchor_fingerprint()
    prev_ts = cache.anchor_ts()
    if prev is None or prev_ts is None or not cache.last_focus():
        return True
    if not universe:
        # Empty snapshot is anomalous — re-decide rather than reuse a stale list.
        return True

    # Cooldown refresh: a periodic re-confirmation even on a flat composition.
    if (now_ts - prev_ts) >= cooldown_sec:
        return True

    curr = composition_fingerprint(universe, top_k=top_k)

    # Membership SET change → a listing appeared or was delisted → material.
    if curr.symbols != prev.symbols:
        return True

    # Top-K (high-volume head) turnover → material when ≥ the small threshold.
    return _turnover(prev.top_k, curr.top_k) >= turnover_threshold
