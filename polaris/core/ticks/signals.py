"""Pure tick-decision intent type (P5).

Spec SSOT: ``.claude/plans/p5_tick_decision_engine_2026-06-03.md`` §"신규 모듈".

The three tick signal generators (``burst_rider`` / ``flow_pressure`` /
``micro_reversion``) were KILLed 2026-06-26 — gross-negative entry expectancy
(negative BEFORE fees, cross-validated over two windows). No tick signal is
emitted or dispatched any more: the regime gate's active set is empty and the
engine's ``_SIGNAL_FNS`` dispatch table is empty.

``TickIntent`` (the side + conviction + family carrier) is retained: the engine
loop, the intent→RawSignal adapter, and the exit-family routing still type
against it for any historical tick position still being closed out. PURE: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "TickIntent",
]

Side = Literal["long", "short"]
SignalFamily = Literal["momentum", "reversion"]


@dataclass(frozen=True, slots=True)
class TickIntent:
    """A directional tick-decision intent (pre-sizing, pre-risk-gate).

    The carrier type the engine loop turns into an order via the existing
    ``compute_size`` + executor — this carries NO notional / multiplier (the T4
    9-stack chain is untouched).

    Fields:
      - ``venue`` / ``symbol``: instrument identity (loop-supplied context).
      - ``side``: ``'long'`` | ``'short'`` — bidirectional.
      - ``conviction``: ``[0, 1]``, monotone in the trigger magnitude.
      - ``signal_id``: which signal produced it.
      - ``signal_family``: ``'momentum'`` | ``'reversion'`` — drives the hybrid
        exit horizon (momentum → ATR-trail, reversion → fast scalp).
      - ``ref_price``: the mid the decision was taken at (loop-supplied).
    """

    venue: str
    symbol: str
    side: Side
    conviction: float
    signal_id: str
    signal_family: SignalFamily
    ref_price: float
