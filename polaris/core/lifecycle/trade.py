"""``SimulatedTrade`` — the in-memory tracked-open-trade record (core layer).

Moved here from ``polaris.scripts._smoke_fills`` to break a layer inversion:
``polaris.core.lifecycle.recover`` (this layer) used to import the dataclass UP
from ``polaris.scripts``. The struct is a pure dataclass (no scripts/venue deps —
only ``RawSignal`` from ``polaris.strategies.base``), so it belongs in core.
``_smoke_fills`` re-exports it, so every existing
``from polaris.scripts._smoke_fills import SimulatedTrade`` keeps working
byte-for-byte. Behaviour is unchanged — this is a move only.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SimulatedTrade"]


@dataclass(slots=True)
class SimulatedTrade:
    """Tracked open trade in the smoke / production loop.

    ``position_id`` (Day 8 codex P0 fix) carries the persisted ``positions``
    row id so the close path can match the exact entry fill (replaces the
    `(strategy_id, instrument_id) → latest open` heuristic which broke on
    multiple concurrent trades).
    """

    signal_id: str
    venue: str
    symbol: str
    strategy_id: str
    side: str
    entry_price: float
    notional_usd: float
    open_ts: int
    closed: bool = False
    pnl_r: float = 0.0
    position_id: str | None = None
    correlation_group: str = ""
    underlying_group_id: str = ""
    # Real-roundtrip venue refs (P0 venue wire). ``venue_order_id`` is the
    # OKX ``ordId`` / Capital ``dealId`` of the entry fill; ``deal_id`` is the
    # Capital position id the close leg needs (OKX closes by base_qty instead).
    venue_order_id: str | None = None
    deal_id: str | None = None
    base_qty: float = 0.0
    # BUG E: venue ref of an UNCONFIRMED close order (Alpaca order_id / Capital
    # close deal_reference). While set, the close path CONFIRMS this ref first
    # instead of firing a duplicate close order. In-memory only (a crash right
    # after order submit loses it — the hydrate remaining-qty restore plus the
    # venue available/over-count clamps then block the double sell).
    pending_close_ref: str | None = None
    # OKX venue-resting conditional stop. ``okx_stop_algo_id`` is the live OKX
    # ``algoId`` of the resting stop that triggers venue-side in the inter-tick
    # gap; ``okx_stop_px`` is the trigger price it was placed at (so the trailing
    # ratchet only cancels+replaces when the stop materially tightens). In-memory
    # only — a crash drops the ref and the software stop remains the backstop.
    okx_stop_algo_id: str | None = None
    okx_stop_px: float | None = None
    # Monotonic deadline (``time.monotonic`` seconds) until which the OKX algo
    # endpoint is treated as unavailable for this symbol — set when a conditional
    # stop place returns ``ok=False`` so the arm backs off instead of re-attempting
    # every tick (no algo-order rate-limit spam). In-memory; cleared on a healthy
    # place. ``None`` = no cooldown active.
    okx_stop_unavail_until: float | None = None
