"""equity_opening_range_breakout — Alpaca US equity, 15m calendar-anchored
Opening-Range Breakout (ORB). DEMO/PAPER virtual, no real capital / fees.

Spec source: ``vault`` Alpaca-sleeve build spec §1 #5 (디베이트 R2 수렴 로스터, Wave
1.5 — the FIRST Alpaca 15m strategy live-verified via ``equity_bb_meanrev_15m``
before this one lands, per the rollout order).

Signaling-strategy contract (ADR-008): entry trigger ONLY. ``correlation_
group_id="equity_orb_open"`` carries no reversion substring → TREND exit
archetype (let-run); ``hold_overnight=False`` (an ORB is an intraday day-trade
bet, EOD flatten); ``profit_target_r=None``; ``expected_holding_bars=8``.
flow_not_block: a clean trigger ALWAYS emits.

ENTRY (15m bar close, LONG-only, deterministic, no look-ahead) requires ALL:
  (1) ``equity_liquidity_ok(bars, 26)`` (T1+T2 value-based liquidity floor —
      §0d, 26 bars/session);
  (2) ``us_equity_session_state(last.ts) == "rth"`` (plain RTH — the entry
      WINDOW below already excludes the open/close edges, so the separate
      ``us_equity_rth_interior`` edge-exclusion gate is not needed here);
  (3) the **Opening-Range (OR) bar** for TODAY's NY-local calendar date
      exists — the 15m bar whose NY-local ``ts`` (Alpaca bar timestamps are
      bar-OPEN, ``_alpaca_bar_to_canonical``) falls at 09:30 ET. A missing OR
      bar (holiday / late open / halt) → **no-emit** (degrade-never-crash — NOT
      a crash, NOT a block on any OTHER symbol/day);
  (4) the CURRENT bar's NY-local time is inside the entry window
      ``[09:45, 11:30)`` ET (i.e. strictly AFTER the OR bar itself, bounded so
      a stale afternoon breakout does not chase);
  (5) ``last.close > or_high`` (the OR bar's high — the opening-range breakout
      level);
  (6) **volume confirm**: ``last.volume >= VOLUME_MULT(2.0) * median(volume at
      the SAME NY-local 15m slot over the trailing VOLUME_SESSIONS_LOOKBACK(20)
      PRIOR sessions)`` — computed PURELY from ``bars`` (no DB/IO), calendar-
      slot matched via ``equity_session_gate.NY_TZ`` (no fixed-UTC hardcode).
      Fewer than 1 prior same-slot session available → no-emit (fails closed
      on doubt, same as ``equity_liquidity_ok``'s insufficient-history case).

``WARMUP_BARS = 560`` ≈ 20 sessions × 26 bars/session + a generous buffer (the
OR-bar + same-slot-volume lookback both need real session history, not just a
raw bar count).

Verified params are named Final constants (no magic numbers):
  - ``ENTRY_WINDOW_START_LOCAL_MINUTES = 585`` (09:45 ET) /
    ``ENTRY_WINDOW_END_LOCAL_MINUTES = 690`` (11:30 ET)
  - ``VOLUME_SESSIONS_LOOKBACK = 20`` / ``VOLUME_MULT = 2.0``
  - ``EQ_LIQ_BARS_PER_DAY = 26``

🚨 OPS: same **100-trade gross (price-only, fee-前) < 0 → unconditional KILL**
trigger as ``equity_bb_meanrev_15m`` — track in the daily digest gross rollup
for ``strategy_id="equity_opening_range_breakout"``.
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Final

from polaris.core.sessions.equity_session_gate import (
    NY_TZ,
    RTH_OPEN_LOCAL_MINUTES,
    us_equity_session_state,
)
from polaris.strategies._equity_liquidity import equity_liquidity_ok
from polaris.strategies._virtual_loosen import virtual_loosen
from polaris.strategies.base import (
    BarView,
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    make_signal_id,
)

# §0d value-based liquidity floor (15m -> 26 bars/session).
EQ_LIQ_BARS_PER_DAY: Final[int] = 26

# Opening-Range bar anchor: the 15m bar OPENING at 09:30 ET (Alpaca bar ts =
# bar-open). ORB_BAR_DURATION_MINUTES is this strategy's own timeframe (15m).
ORB_BAR_DURATION_MINUTES: Final[int] = 15
OR_BAR_LOCAL_MINUTE: Final[int] = RTH_OPEN_LOCAL_MINUTES  # 570 = 09:30 ET

# Entry window: strictly AFTER the OR bar, bounded before the midday chase
# zone. [09:45, 11:30) ET.
ENTRY_WINDOW_START_LOCAL_MINUTES: Final[int] = (
    OR_BAR_LOCAL_MINUTE + ORB_BAR_DURATION_MINUTES
)  # 585 = 09:45 ET
ENTRY_WINDOW_END_LOCAL_MINUTES: Final[int] = 11 * 60 + 30  # 690 = 11:30 ET

# Same-slot volume confirm.
VOLUME_SESSIONS_LOOKBACK: Final[int] = 20
VOLUME_MULT: Final[float] = 2.0

# Calendar-session lookback: 20 sessions × 26 bars/session + buffer (OR-bar +
# same-slot-volume history both need real session depth, not a raw bar count).
WARMUP_BARS: Final[int] = 560

# Strength curve (frozen v1): deeper breakout excess (as a close/or_high
# fraction) -> stronger.
STRENGTH_FLOOR: Final[float] = 0.5
STRENGTH_GAIN: Final[float] = 20.0
TTL_BARS: Final[int] = 3

# §0c loss-reentry cooldown — 4 bars @ 15m = 1h symbol-local pacing.
LOSS_COOLDOWN_BARS: Final[int] = 4


def _ny_local(ts: int | float) -> dt.datetime:
    ts_int = max(0, int(ts))
    return dt.datetime.fromtimestamp(ts_int, tz=NY_TZ)


def _minute_of_day(local: dt.datetime) -> int:
    return local.hour * 60 + local.minute


def _find_or_bar(bars: list[BarView], today: dt.date) -> BarView | None:
    """The 09:30-ET-open 15m bar for ``today`` (NY-local calendar date).

    Scans backward from the most recent bar and stops once the NY-local date
    changes (bars are chronologically ascending) — cheap for the common case
    (the OR bar is within the last ~8 bars of the current session).
    """
    for bar in reversed(bars):
        local = _ny_local(bar.ts)
        if local.date() != today:
            break
        if _minute_of_day(local) == OR_BAR_LOCAL_MINUTE:
            return bar
    return None


def _same_slot_prior_volumes(
    bars: list[BarView], *, today: dt.date, slot_minute: int
) -> list[float]:
    """Volumes at ``slot_minute`` from the trailing PRIOR sessions (today excluded).

    Most-recent-first, capped at ``VOLUME_SESSIONS_LOOKBACK`` distinct sessions.
    """
    out: list[float] = []
    seen_dates: set[dt.date] = set()
    for bar in reversed(bars):
        local = _ny_local(bar.ts)
        bar_date = local.date()
        if bar_date == today:
            continue
        if _minute_of_day(local) == slot_minute and bar_date not in seen_dates:
            out.append(bar.volume)
            seen_dates.add(bar_date)
            if len(out) >= VOLUME_SESSIONS_LOOKBACK:
                break
    return out


class EquityOpeningRangeBreakoutStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="equity_opening_range_breakout",
        timeframe="15m",
        warmup_bars=WARMUP_BARS,
        max_positions=3,
        gross_cap=0.09,
        per_symbol_cap=0.03,
        expected_holding_bars=8,
        asset_class="equity",
        venue="alpaca",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="equity_orb_open",
        product_class="equity",
        hold_overnight=False,
        profit_target_r=None,
        dispatch_eligible=virtual_loosen(True, False),
        loss_cooldown_bars=LOSS_COOLDOWN_BARS,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < WARMUP_BARS:
            return None
        if not equity_liquidity_ok(bars, EQ_LIQ_BARS_PER_DAY):
            return None
        last = bars[-1]
        if us_equity_session_state(last.ts) != "rth":
            return None

        last_local = _ny_local(last.ts)
        last_minute = _minute_of_day(last_local)
        if not (
            ENTRY_WINDOW_START_LOCAL_MINUTES
            <= last_minute
            < ENTRY_WINDOW_END_LOCAL_MINUTES
        ):
            return None

        today = last_local.date()
        or_bar = _find_or_bar(bars, today)
        if or_bar is None:
            return None
        or_high = or_bar.high
        if last.close <= or_high:
            return None

        prior_vols = _same_slot_prior_volumes(
            bars, today=today, slot_minute=last_minute
        )
        if not prior_vols:
            return None
        median_vol = statistics.median(prior_vols)
        if median_vol <= 0.0 or last.volume < VOLUME_MULT * median_vol:
            return None

        price_excess = (last.close - or_high) / or_high
        scored = STRENGTH_FLOOR + STRENGTH_GAIN * price_excess
        strength = min(1.0, max(STRENGTH_FLOOR, scored))
        volume_ratio = last.volume / median_vol

        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"equity_orb_open+or_high={or_high:.4f}+vol_ratio={volume_ratio:.2f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={
                "or_high": f"{or_high:.4f}",
                "volume_ratio": f"{volume_ratio:.2f}",
                "median_slot_volume": f"{median_vol:.1f}",
            },
        )


__all__ = [
    "ENTRY_WINDOW_END_LOCAL_MINUTES",
    "ENTRY_WINDOW_START_LOCAL_MINUTES",
    "EQ_LIQ_BARS_PER_DAY",
    "EquityOpeningRangeBreakoutStrategy",
    "LOSS_COOLDOWN_BARS",
    "OR_BAR_LOCAL_MINUTE",
    "ORB_BAR_DURATION_MINUTES",
    "STRENGTH_FLOOR",
    "STRENGTH_GAIN",
    "TTL_BARS",
    "VOLUME_MULT",
    "VOLUME_SESSIONS_LOOKBACK",
    "WARMUP_BARS",
]
