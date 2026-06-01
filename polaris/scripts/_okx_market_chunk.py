"""Shared OKX SPOT market-order chunking helpers (entry + close).

OKX SPOT rejects (51201) any single market order whose quote value exceeds
``OKX_MAX_MARKET_NOTIONAL_USDT`` (1000 USDT). Both the entry leg
(``_okx_limit_open``) and the close leg (``_smoke_real_roundtrip``) must deliver
a larger order as SEQUENTIAL child market orders each <= the cap, then aggregate
the child fills into ONE :class:`Fill`. Factored here so the two paths share one
implementation and can never diverge (the close-chunking bug was the close path
NOT mirroring the entry's split — this module is the single source of truth).

Pure: ``_split_market_notional`` (quote → list of <=cap quote chunks) and
``_aggregate_child_fills`` (size-weighted-avg-price aggregate of child fills).
No I/O.
"""

from __future__ import annotations

from polaris.core.data.fill_normalizer import Fill
from polaris.scripts._limit_exec_constants import OKX_MAX_MARKET_NOTIONAL_USDT
from polaris.scripts._smoke_roundtrip_shared import MIN_OKX_NOTIONAL_USD

__all__ = ["_aggregate_child_fills", "_split_market_notional"]


def _split_market_notional(
    notional_usd: float, *, cap: float = OKX_MAX_MARKET_NOTIONAL_USDT,
) -> list[float]:
    """Split a quote notional into child chunks each <= the OKX market cap.

    OKX SPOT rejects (51201) any single market order whose quote value exceeds
    ``cap`` (the 1000-USDT venue cap for entry; the close passes a buffered
    cap = 1000 × (1 - OKX_CLOSE_CAP_BUFFER_FRAC) for upward-drift headroom). We
    deliver the FULL notional in sequential <=cap chunks so the order fills
    (flow_not_block) — the total is unchanged (NOT a size cut). e.g. (cap=1000)
    1468 → [1000, 468]; 3000 → [1000,1000,1000].

    Min-order tail fold (51020 fix): the bare remainder after the cap loop can
    be below the OKX minimum notional (e.g. 1001 → [1000, 1] → a 1-USDT child
    < min → rejected → dust forever). When that happens we FOLD the sub-min tail
    into the previous chunk and re-split the pair in half so the last two
    children are each >= the min AND each <= the cap (1001 → [500.5, 500.5]).
    Non-dust tails are untouched (1468 → [1000, 468] since 468 >= min); an exact
    multiple has no tail (3000 → [1000,1000,1000]).
    """
    if notional_usd <= cap:
        return [notional_usd]
    chunks: list[float] = []
    remaining = notional_usd
    while remaining > cap:
        chunks.append(cap)
        remaining -= cap
    if remaining <= 0.0:
        return chunks
    if remaining < MIN_OKX_NOTIONAL_USD and chunks:
        # Sub-min tail → fold into the previous chunk + halve so both children
        # clear the min and stay under the cap (merged ∈ (cap, cap+min)).
        merged = chunks.pop() + remaining
        half = merged / 2.0
        chunks.extend((half, half))
    else:
        chunks.append(remaining)
    return chunks


def _aggregate_child_fills(fills: list[Fill]) -> Fill:
    """Aggregate filled market child-orders into one ``Fill``.

    ``base_qty`` / ``quote_qty`` / ``size_usd`` / ``fee_usd`` sum across
    children; ``fill_price`` is the size-weighted average (weighted by each
    child's base_qty). Carries the first child's identity fields so the
    position is tracked under a single order ref.
    """
    base_total = sum(f.base_qty for f in fills)
    quote_total = sum(f.quote_qty for f in fills)
    fee_total = sum(f.fee_usd for f in fills)
    if base_total > 0.0:
        avg_px = sum(f.fill_price * f.base_qty for f in fills) / base_total
    else:
        avg_px = fills[0].fill_price
    head = fills[0]
    return Fill(
        venue=head.venue,
        instrument_id=head.instrument_id,
        strategy_id=head.strategy_id,
        side=head.side,
        size_usd=quote_total,
        fill_price=avg_px,
        fee_usd=fee_total,
        slippage_bps=head.slippage_bps,
        ts_ms=fills[-1].ts_ms,
        order_id=head.order_id,
        client_order_id=head.client_order_id,
        base_qty=base_total,
        quote_qty=quote_total,
        state="partially_filled" if len(fills) > 1 and any(
            f.state == "partially_filled" for f in fills
        ) else head.state,
    )
