"""Per-symbol activation accumulator (#39) — split from ``quote_writer`` (≤500 LOC).

``quote_ticks`` is single-row LWW (no per-symbol history) and ``tick_inflow`` is
per-VENUE (every Capital symbol gets the identical micro pulse), so the candidate
sweep cannot see that EURUSD ticks every 9s while a calm agricultural is dead.
This accumulator carries the missing per-symbol motion — tick rate, recent short
move, intraday range — keyed by ``instrument_id`` (cross-venue collision safe).

Bounded by construction: 60s buckets keyed on ``ts // 60`` with only the trailing
600s window kept (≤10 live buckets), each ``(count, hi, lo, first_ts, first_mid)``.
``add`` is O(1); ``snapshot`` prunes + folds the ≤10 buckets (O(10)). Pure in-mem;
NEVER persisted — the writer's flush ships quote rows + per-venue tick_inflow ONLY,
this accumulator adds NO SQL. The writer LRU-caps the MAP of accumulators
(``ACTIVATION_MAX_SYMBOLS``) so a long run can never grow the in-mem state without
bound (retention guard).
"""

from __future__ import annotations

# Per-symbol activation accumulator window. Same 600s / 60s-bucket geometry as the
# per-venue inflow so the two share trailing-window semantics. 10 buckets max per
# symbol → O(1) append, bounded.
_ACTIVATION_WINDOW_SEC = 600
_ACTIVATION_BUCKET_SEC = 60
# Short-move reference lookback: the displacement reference is the mid recorded
# ~this-many-seconds before the latest tick (the sweep's short_move component).
_ACTIVATION_SHORT_MOVE_LOOKBACK_SEC = 120
# Hard cap on the number of DISTINCT instruments the writer's accumulator map
# tracks. Past this bound the map is LRU-evicted by ``last_tick_ts`` so a long run
# can NEVER grow the in-mem state without bound (retention guard — the per-symbol
# accumulator's explicit no-infinite-growth requirement). 4000 ≫ the watched
# universe (~1650) so eviction only fires on pathological churn, never in steady state.
ACTIVATION_MAX_SYMBOLS = 4000


class _SymbolActivation:
    """Per-SYMBOL rolling motion accumulator (in-mem, loop thread) → #39."""

    __slots__ = ("buckets", "last_tick_ts", "last_mid")

    def __init__(self) -> None:
        # bucket_key -> (count, hi, lo, first_ts, first_mid)
        self.buckets: dict[int, tuple[int, float, float, int, float]] = {}
        self.last_tick_ts: int = 0
        self.last_mid: float = 0.0

    def add(self, ts: int, mid: float) -> None:
        if ts >= self.last_tick_ts:
            self.last_tick_ts = ts
            self.last_mid = mid
        key = ts // _ACTIVATION_BUCKET_SEC
        cur = self.buckets.get(key)
        if cur is None:
            self.buckets[key] = (1, mid, mid, ts, mid)
        else:
            count, hi, lo, first_ts, first_mid = cur
            if ts < first_ts:  # an earlier tick in the same bucket
                first_ts, first_mid = ts, mid
            self.buckets[key] = (
                count + 1, max(hi, mid), min(lo, mid), first_ts, first_mid
            )

    def snapshot(self) -> dict[str, float | int]:
        """Prune stale buckets, then fold the trailing-600s window to metrics.

        Returns ``ticks_600s`` (rate), ``mid_120s_ago`` (short-move reference),
        ``mid_high_600s`` / ``mid_low_600s`` (intraday range), and ``last_mid`` /
        ``last_ts``. The short-move reference is the ``first_mid`` of the bucket
        nearest ~120s before the latest tick (degrading to the oldest live bucket's
        ``first_mid`` when the window is younger than the lookback — a smaller move,
        never a crash).
        """
        floor_key = (
            self.last_tick_ts - _ACTIVATION_WINDOW_SEC
        ) // _ACTIVATION_BUCKET_SEC
        for key in [k for k in self.buckets if k < floor_key]:
            del self.buckets[key]
        ticks = sum(b[0] for b in self.buckets.values())
        hi = max((b[1] for b in self.buckets.values()), default=self.last_mid)
        lo = min((b[2] for b in self.buckets.values()), default=self.last_mid)
        ref_mid = self._short_move_reference()
        return {
            "ticks_600s": ticks,
            "mid_120s_ago": ref_mid,
            "mid_high_600s": hi,
            "mid_low_600s": lo,
            "last_mid": self.last_mid,
            "last_ts": self.last_tick_ts,
        }

    def _short_move_reference(self) -> float:
        """``first_mid`` of the bucket covering ~``lookback`` before the latest tick.

        Picks the OLDEST live bucket whose ``first_ts`` is at or before the lookback
        cutoff (the displacement reference); when no bucket is that old yet (window
        younger than the lookback) it falls back to the oldest live bucket's
        ``first_mid`` — a genuine recent reference, never a manufactured spike.
        """
        if not self.buckets:
            return self.last_mid
        cutoff = self.last_tick_ts - _ACTIVATION_SHORT_MOVE_LOOKBACK_SEC
        older = [b for b in self.buckets.values() if b[3] <= cutoff]
        if older:
            # Most-recent bucket that is still at/before the cutoff → closest to
            # exactly ``lookback`` ago.
            return max(older, key=lambda b: b[3])[4]
        # Window younger than the lookback — use the oldest mid we have.
        return min(self.buckets.values(), key=lambda b: b[3])[4]
