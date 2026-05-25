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

__all__ = ["derive_session", "SESSION_LABELS"]

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
