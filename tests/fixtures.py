"""Explicit factory functions for test fixtures."""
from __future__ import annotations
from typing import Any


def mock_position(ticker="BTC", direction="long", exchange="okx",
                  group="crypto", entry=100.0, size=1000.0,
                  mp=0.0, pnl=0.0, age_sec=0, **extra):
    """Create a mock Position for tests."""
    from invasion.trade.position import Position
    from invasion.trade.exit_types import ExitState
    import time
    state = extra.pop('state', None) or ExitState.OPEN
    pos = Position(
        ticker=ticker, exchange=exchange, asset_group=group,
        direction=direction, entry_price=entry, size_usd=size,
    )
    pos.max_profit_pct = mp
    pos.pnl_pct = pnl
    pos.ts_entry = time.time() - age_sec
    pos.state = state
    return pos


def mock_signal_result(name="test", score=30.0, conf=0.8, ttl=900):
    """Create a mock SignalResult."""
    from invasion.signals.base import SignalResult
    return SignalResult(
        name=name, score=score, confidence=conf, ttl=ttl,
    )


def mock_verdict(score=40, direction="long", confidence=0.7, regime="neutral"):
    """Create a mock entry verdict (simple object with attrs)."""
    class _V:
        pass
    v = _V()
    v.score = score
    v.direction = direction
    v.confidence = confidence
    v.regime = regime
    v.composite = type('C', (), {
        'score': score, 'confidence': confidence,
        'edge_prob': 0.55, 'reversal_horizon': 900,
        'execution_risk': 0.001,
    })()
    return v


def mock_market_data(group="crypto", price=100.0, ts=None, **extra):
    """Create a mock market_data dict."""
    import time
    return {
        'asset_group': group, 'price': price,
        'ts': ts if ts is not None else time.time(),
        **extra,
    }
