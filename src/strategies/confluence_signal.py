"""Confluence Signal — meta-strategy combining multiple sub-strategies (P6 pure).

HYPOTHESIS-018/019/020: single indicator negative EV → combination multi-signal
confluence may yield positive alpha.

Phase 2h: ADR-014 combination grid approach.

Logic:
- Collect evaluate(window) from all sub-strategies.
- EXIT from any sub → meta EXIT (conservative; position safety first).
- require_all=True  (AND): all subs must emit ENTER_LONG.
- require_all=False (N-of-M): at least min_confluence subs emit ENTER_LONG.
- On ENTER_LONG: use ConfluenceSignal.target_size_usd (not sub's own size).
- confidence = mean(confidence of agreeing ENTER_LONG subs).

Pure: no I/O, deterministic.
"""
from __future__ import annotations

from statistics import mean

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy


class ConfluenceSignal(Strategy):
    """N-of-M sub-strategy confluence meta-strategy.

    Args:
        sub_strategies: list of Strategy instances (at least 1).
        require_all: True = AND (all must agree), False = N-of-M.
        min_confluence: minimum agreeing subs for N-of-M mode.
                        Defaults to len(sub_strategies) (= require_all behaviour).
        target_size_usd: position size for meta ENTER_LONG signal.
    """

    name = "confluence_signal"

    def __init__(
        self,
        sub_strategies: list[Strategy],
        require_all: bool = True,
        min_confluence: int | None = None,
        target_size_usd: float = 200.0,
    ) -> None:
        if not sub_strategies:
            raise ValueError("sub_strategies must be non-empty list")
        if target_size_usd <= 0:
            raise ValueError(f"target_size_usd must be > 0, got {target_size_usd}")

        self.subs = sub_strategies
        self.require_all = require_all
        self.target_size_usd = target_size_usd

        # Determine effective min_confluence
        if require_all:
            self._min_confluence = len(sub_strategies)
        else:
            eff = min_confluence if min_confluence is not None else len(sub_strategies)
            if eff <= 0:
                raise ValueError(f"min_confluence must be >= 1, got {eff}")
            self._min_confluence = eff

        # min_window = max of all sub min_windows
        self.min_window = max(s.min_window for s in sub_strategies)

        # Expose descriptive name including sub names
        self.name = (
            "confluence["
            + ",".join(s.name for s in sub_strategies)
            + f" {'AND' if require_all else f'{self._min_confluence}of{len(sub_strategies)}'}]"
        )

    def evaluate(self, window: list[Candle]) -> Signal:
        """Evaluate all subs, apply AND / N-of-M logic.

        Args:
            window: candle window (length >= self.min_window).

        Returns:
            Signal — EXIT | ENTER_LONG | HOLD.
        """
        self._ensure_window(window)
        ts = window[-1].timestamp_ms

        signals = [s.evaluate(window) for s in self.subs]

        # EXIT from ANY sub → meta EXIT (position safety first)
        exits = [sig for sig in signals if sig.action == SignalAction.EXIT]
        if exits:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=max(sig.confidence for sig in exits),
                reason=f"confluence EXIT (any sub): {exits[0].reason}",
            )

        # Count ENTER_LONG agreements
        long_sigs = [sig for sig in signals if sig.action == SignalAction.ENTER_LONG]
        n_long = len(long_sigs)
        n_total = len(self.subs)

        confluence_met = n_long >= self._min_confluence

        if confluence_met:
            avg_conf = mean(sig.confidence for sig in long_sigs)
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=min(avg_conf, 1.0),
                target_size_usd=self.target_size_usd,
                reason=f"confluence ENTER_LONG {n_long}/{n_total} (min={self._min_confluence})",
            )

        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=f"confluence HOLD {n_long}/{n_total} < min={self._min_confluence}",
        )
