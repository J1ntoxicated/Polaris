"""Shared pure types for the tick-decision engine (P5 gap-a).

``TickSample`` is the microstructure projection of a stored ``QuoteTick`` that
both ``quote_writer.feature_window`` (the producer) and the pure feature module
(the consumer) read. It lives here — not in ``quote_writer`` — so the pure
feature code can import it without an import cycle through the impure writer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TickSample:
    """One tick's full microstructure, projected from a ``QuoteTick``.

    Fields are copied straight from the stored ``QuoteTick``; ``spread_bps`` is
    the stored value, or ``(ask - bid) / mid * 1e4`` recomputed when the stored
    value is missing (0.0).
    """

    ts: int
    bid: float
    ask: float
    mid: float
    bid_size: float
    ask_size: float
    last_trade_price: float
    last_trade_size: float
    spread_bps: float
