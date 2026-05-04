"""Cross-Sectional Momentum — Jegadeesh & Titman 1993 JoF.

HYPOTHESIS-035: Universe return rank — top performers long, bottom skip.

Academic basis:
  Jegadeesh, N., Titman, S. (1993).
  "Returns to buying winners and selling losers: Implications for stock
   market efficiency". Journal of Finance, 48(1), 65-91.
  - 50+ year persistent factor across stocks, futures, crypto.
  - Top decile outperforms bottom decile by 1.0-1.5%/month.

  Liu, Y., Tsyvinski, A., Wu, X. (2022).
  "Common risk factors in cryptocurrency". Journal of Finance, 77(2), 1133-1177.
  - Factor confirmed in crypto: 30-day cross-sectional momentum significant.

Crypto adaptation:
  - Universe: 8 tickers.
  - Lookback: 30 days (faster mean reversion vs equities).
  - Hold: 7 days (weekly rebalance — crypto faster cycle).
  - Top 30% (top 2-3 tickers by 30d return) → ENTER_LONG.
  - Bottom 30% → SKIP (SPOT-only, no SHORT).
  - Middle 40% → HOLD.

Logic:
  For each ticker t in universe:
    ret_30d[t] = (candles[-1].close - candles[-31].close) / candles[-31].close
  Rank descending. Top-N = ceil(top_pct * len(universe)).
  If ticker is in top-N → ENTER_LONG.
  If ticker is in bottom-N → EXIT (hold-to-sell signal).
  Else → HOLD.

Note on runner integration:
  This strategy uses evaluate(window) for single-ticker signal.
  Cross-sectional ranking is done by the CALLER (cron/runner) which fetches
  all universe candles, computes ranks, then calls evaluate() with a
  pre-computed `rank_pct` injected via candle metadata proxy.

  Alternative interface: evaluate_universe(universe_windows) which returns
  a dict {ticker: Signal}. This is the recommended interface for runners.

Pure function — no I/O, no module state, deterministic.
P6 classification: pure=true
"""
from __future__ import annotations

import math

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_UNIVERSE = (
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT",
    "ADA-USDT", "XRP-USDT", "ORDI-USDT", "SUI-USDT",
)
DEFAULT_LOOKBACK_DAYS: int = 30
DEFAULT_HOLD_DAYS: int = 7
DEFAULT_TOP_PCT: float = 0.30
DEFAULT_TARGET_SIZE_USD: float = 200.0

# min_window for single-ticker evaluate
# need lookback_days + 1 candles for return computation
_MIN_WINDOW = DEFAULT_LOOKBACK_DAYS + 1  # 31


def _return_30d(window: list[Candle], lookback: int) -> float | None:
    """Compute lookback-day return from window. Pure.

    Returns None if insufficient data.
    """
    if len(window) < lookback + 1:
        return None
    past_close = window[-(lookback + 1)].close
    if past_close <= 0.0:
        return None
    current_close = window[-1].close
    return (current_close - past_close) / past_close


class CrossSectionalMomentum(Strategy):
    """Cross-Sectional Momentum — Jegadeesh & Titman 1993 JoF.

    Two interfaces:
    1. evaluate(window) — single ticker, signal based on pre-injected rank_pct.
       rank_pct must be set via set_rank_pct(pct) before calling evaluate().
       Used by standard cron runner.

    2. evaluate_universe(universe_windows) — cross-sectional ranking across universe.
       Returns dict {ticker: Signal}.
       Used by multi-ticker cross-sectional runner (HYPO-035 specific).

    Pure — no I/O.

    Attributes:
        universe: tuple of ticker strings (for universe ranking).
        lookback_days: return lookback in days.
        hold_days: intended hold duration (informational; exit not time-based).
        top_pct: fraction of universe ranked as "top" (long candidates).
        target_size_usd: base position size.
    """

    name = "cs_momentum"
    min_window = DEFAULT_LOOKBACK_DAYS + 1  # 31 candles needed

    def __init__(
        self,
        universe: tuple[str, ...] | list[str] | None = None,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        hold_days: int = DEFAULT_HOLD_DAYS,
        top_pct: float = DEFAULT_TOP_PCT,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.universe: tuple[str, ...] = tuple(universe) if universe is not None else DEFAULT_UNIVERSE
        self.lookback_days = lookback_days
        self.hold_days = hold_days
        self.top_pct = top_pct
        self.target_size_usd = target_size_usd
        self.min_window = lookback_days + 1

        # Runtime rank injection (set by runner before evaluate call)
        self._rank_pct: float | None = None  # 0.0 = bottom, 1.0 = top

    def set_rank_pct(self, rank_pct: float) -> None:
        """Inject cross-sectional rank percentile (0.0 = worst, 1.0 = best).

        Called by runner after computing universe ranking.
        Pure effect on this instance only.

        Args:
            rank_pct: float in [0.0, 1.0]. 1.0 = highest 30d return in universe.
        """
        if not 0.0 <= rank_pct <= 1.0:
            raise ValueError(f"rank_pct must be in [0,1], got {rank_pct}")
        self._rank_pct = rank_pct

    def evaluate(self, window: list[Candle]) -> Signal:
        """Single-ticker evaluate using injected rank_pct.

        Falls back to raw return computation if rank_pct not set.
        In that case, uses only this ticker's absolute momentum (no cross-sectional).

        Args:
            window: candle list (oldest first), need >= lookback_days + 1.

        Returns:
            ENTER_LONG if top_pct rank, EXIT if bottom_pct rank, HOLD otherwise.
        """
        if not window or len(window) < self.min_window:
            ts = window[-1].timestamp_ms if window else 1
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"cs_momentum_warmup need={self.min_window} have={len(window)}",
            )

        ts = window[-1].timestamp_ms

        if self._rank_pct is not None:
            # Cross-sectional rank injected by runner
            rank_pct = self._rank_pct
        else:
            # Fallback: use raw 30d return as a proxy rank (standalone use)
            raw_ret = _return_30d(window, self.lookback_days)
            if raw_ret is None:
                return Signal(
                    timestamp_ms=ts,
                    action=SignalAction.HOLD,
                    confidence=0.0,
                    reason="cs_momentum_no_return insufficient_data",
                )
            # Map return to pseudo-rank: positive → treat as top, negative → bottom
            rank_pct = 0.8 if raw_ret > 0.05 else (0.2 if raw_ret < -0.05 else 0.5)

        if rank_pct >= (1.0 - self.top_pct):
            # Top tier — ENTER_LONG
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=round(rank_pct, 3),
                target_size_usd=self.target_size_usd,
                reason=(
                    f"cs_momentum_enter rank_pct={rank_pct:.2f} >= top={1.0-self.top_pct:.2f} "
                    f"lookback={self.lookback_days}d"
                ),
            )

        if rank_pct <= self.top_pct:
            # Bottom tier — EXIT (no SHORT in SPOT)
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=round(1.0 - rank_pct, 3),
                reason=(
                    f"cs_momentum_exit rank_pct={rank_pct:.2f} <= bottom={self.top_pct:.2f} "
                    f"lookback={self.lookback_days}d"
                ),
            )

        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=f"cs_momentum_hold rank_pct={rank_pct:.2f} mid_zone",
        )

    def evaluate_universe(
        self,
        universe_windows: dict[str, list[Candle]],
    ) -> dict[str, Signal]:
        """Cross-sectional ranking across all universe tickers.

        Args:
            universe_windows: {ticker: candle_list}. Tickers with insufficient
                              data are given HOLD signals (warmup guard).

        Returns:
            {ticker: Signal} — ranked cross-sectionally.

        Pure — no I/O, deterministic.
        """
        # Step 1: compute 30d return for each ticker
        returns: dict[str, float] = {}
        for ticker, window in universe_windows.items():
            ret = _return_30d(window, self.lookback_days)
            if ret is not None:
                returns[ticker] = ret

        n_ranked = len(returns)
        if n_ranked == 0:
            # All warmup — return HOLD for everyone
            result = {}
            for ticker, window in universe_windows.items():
                ts = window[-1].timestamp_ms if window else 1
                result[ticker] = Signal(
                    timestamp_ms=ts,
                    action=SignalAction.HOLD,
                    confidence=0.0,
                    reason="cs_momentum_universe_warmup all_no_data",
                )
            return result

        # Step 2: rank descending by return
        sorted_tickers = sorted(returns.keys(), key=lambda t: returns[t], reverse=True)

        # Top-N = ceil(top_pct * n_ranked), bottom-N = floor(top_pct * n_ranked) minimum 1
        n_top = max(1, math.ceil(self.top_pct * n_ranked))
        n_bottom = max(1, math.floor(self.top_pct * n_ranked))

        top_set = set(sorted_tickers[:n_top])
        bottom_set = set(sorted_tickers[-n_bottom:])

        # Step 3: emit signals
        result = {}
        for ticker, window in universe_windows.items():
            ts = window[-1].timestamp_ms if window else 1

            if ticker not in returns:
                # Insufficient data
                result[ticker] = Signal(
                    timestamp_ms=ts,
                    action=SignalAction.HOLD,
                    confidence=0.0,
                    reason=f"cs_momentum_warmup {ticker} no_return",
                )
                continue

            ret = returns[ticker]
            rank_idx = sorted_tickers.index(ticker)  # 0 = best
            rank_pct = 1.0 - (rank_idx / max(1, n_ranked - 1))  # 1.0 = best

            if ticker in top_set and ticker not in bottom_set:
                result[ticker] = Signal(
                    timestamp_ms=ts,
                    action=SignalAction.ENTER_LONG,
                    confidence=round(rank_pct, 3),
                    target_size_usd=self.target_size_usd,
                    reason=(
                        f"cs_momentum_enter rank={rank_idx+1}/{n_ranked} "
                        f"ret30d={ret:+.3f} rank_pct={rank_pct:.2f}"
                    ),
                )
            elif ticker in bottom_set and ticker not in top_set:
                result[ticker] = Signal(
                    timestamp_ms=ts,
                    action=SignalAction.EXIT,
                    confidence=round(1.0 - rank_pct, 3),
                    reason=(
                        f"cs_momentum_exit rank={rank_idx+1}/{n_ranked} "
                        f"ret30d={ret:+.3f} rank_pct={rank_pct:.2f}"
                    ),
                )
            else:
                result[ticker] = Signal(
                    timestamp_ms=ts,
                    action=SignalAction.HOLD,
                    confidence=0.0,
                    reason=f"cs_momentum_hold rank={rank_idx+1}/{n_ranked} mid_zone",
                )

        return result
