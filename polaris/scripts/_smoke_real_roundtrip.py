"""Day 6 — OKX SPOT real demo fill round-trip (open + immediate close).

Spec source:
- vault/30_components/layer-1-canonical-baseline.md (Fill normalizer)
- vault/10_decisions/ADR-003-8-layer-architecture.md (REST polling)

The OKX round-trip + single-leg fill helpers. Shared helpers
(``record_venue_orphan`` / ``resolve_okx_base_url`` / min-size constants) live
in ``_smoke_roundtrip_shared``; the Capital round-trip lives in
``_smoke_roundtrip_capital``. Both are re-exported here so existing import paths
(``from polaris.scripts._smoke_real_roundtrip import run_capital_round_trip``)
keep working.

Each ``run_*_round_trip`` helper opens at the minimum-allowable size, confirms
the fill, normalizes + persists it, then closes immediately. Defensive: every
function returns ``{"ok": False, "error": "..."}`` on any exception so the smoke
loop never aborts; ``dry_run=True`` returns synthetic fills (CI without creds).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
import uuid
from typing import Any

from polaris.core.data.fill_normalizer import Fill, normalize_okx_fill
from polaris.core.data.fills_persist import persist_fill
from polaris.scripts._smoke_roundtrip_capital import (
    real_capital_close_fill,
    real_capital_open_fill,
    run_capital_round_trip,
)
from polaris.scripts._smoke_roundtrip_shared import (
    MIN_CAPITAL_LOT,
    MIN_OKX_NOTIONAL_USD,
    record_venue_orphan,
    resolve_okx_base_url,
)
from polaris.venues.okx import OKXAdapter

logger = logging.getLogger(__name__)

__all__ = [
    "MIN_OKX_NOTIONAL_USD",
    "MIN_CAPITAL_LOT",
    "real_capital_close_fill",
    "real_capital_open_fill",
    "real_okx_close_fill",
    "real_okx_open_fill",
    "record_venue_orphan",
    "resolve_okx_base_url",
    "run_capital_round_trip",
    "run_okx_round_trip",
]


# ---------------------------------------------------------------------------
# Synthetic dry-run fills (for CI / no creds / mock smoke)
# ---------------------------------------------------------------------------


def _synthetic_okx_fill(*, is_close: bool) -> Fill:
    side = "sell" if is_close else "buy"
    return Fill(
        venue="okx",
        instrument_id="okx:BTC-USDT",
        strategy_id="day6_smoke",
        side=side,  # type: ignore[arg-type]
        size_usd=MIN_OKX_NOTIONAL_USD,
        fill_price=80_000.0 + (0.2 if is_close else 0.0),
        fee_usd=MIN_OKX_NOTIONAL_USD * 0.001,
        slippage_bps=2.0,
        ts_ms=int(time.time() * 1000),
        order_id=f"dry_{uuid.uuid4().hex[:12]}",
        client_order_id=f"polD6dry{uuid.uuid4().hex[:6]}",
        base_qty=MIN_OKX_NOTIONAL_USD / 80_000.0,
        quote_qty=MIN_OKX_NOTIONAL_USD,
        state="filled",
    )


# ---------------------------------------------------------------------------
# Single-leg adapter helpers (reused by the production paper loop real wire).
#
# Each helper drives one leg (open or close) on an *already-constructed*
# adapter and returns a normalized ``Fill`` (or ``None`` on reject / no-fill).
# The adapter is injected so the production loop and the unit tests can pass a
# mock; no network call happens unless the caller hands in a live adapter.
# ---------------------------------------------------------------------------


async def real_okx_open_fill(
    adapter: Any,
    *,
    inst_id: str,
    notional_usd: float,
    strategy_id: str,
    last_price: float | None = None,
    poll_delay_sec: float = 0.5,
) -> Fill | None:
    """OKX entry leg: place buy → poll order → normalize. ``None`` on no-fill."""
    buy_resp = await adapter.place_market_order(
        inst_id=inst_id,
        side="buy",
        notional_usd=notional_usd,
        client_order_id=f"polLbuy{uuid.uuid4().hex[:8]}",
    )
    if not buy_resp.ok or not buy_resp.venue_order_id:
        return None
    await asyncio.sleep(poll_delay_sec)
    state = await adapter.fetch_order(inst_id=inst_id, ord_id=buy_resp.venue_order_id)
    rows = state.get("data", []) or []
    if not rows:
        return None
    return normalize_okx_fill(
        rows[0], strategy_id=strategy_id, expected_price=last_price,
    )


async def real_okx_close_fill(
    adapter: Any,
    *,
    inst_id: str,
    base_qty: float,
    strategy_id: str,
    poll_delay_sec: float = 0.5,
) -> Fill | None:
    """OKX close leg: sell the entry ``base_qty`` → poll → normalize."""
    if base_qty <= 0.0:
        return None
    sell_resp = await adapter.place_market_order(
        inst_id=inst_id,
        side="sell",
        notional_usd=base_qty,  # interpreted as base qty by OKX (tgt=base_ccy)
        client_order_id=f"polLsell{uuid.uuid4().hex[:8]}",
        tgt_ccy="base_ccy",
        ord_type="market",
    )
    if not sell_resp.ok or not sell_resp.venue_order_id:
        return None
    await asyncio.sleep(poll_delay_sec)
    state = await adapter.fetch_order(inst_id=inst_id, ord_id=sell_resp.venue_order_id)
    rows = state.get("data", []) or []
    if not rows:
        return None
    return normalize_okx_fill(rows[0], strategy_id=strategy_id)


# ---------------------------------------------------------------------------
# OKX SPOT round-trip
# ---------------------------------------------------------------------------


async def run_okx_round_trip(
    *,
    conn: sqlite3.Connection,
    inst_id: str = "BTC-USDT",
    notional_usd: float = MIN_OKX_NOTIONAL_USD,
    strategy_id: str = "day6_smoke",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Real OKX BTC-USDT IOC entry + immediate IOC sell close.

    Persists 2 fills rows (open + close). Returns dict with fill_ids and
    raw venue payloads. ``dry_run=True`` skips REST and writes 2 synthetic
    fills (used by CI + the Day 6 unit tests).
    """
    if dry_run:
        open_fill = _synthetic_okx_fill(is_close=False)
        close_fill = _synthetic_okx_fill(is_close=True)
        open_id = persist_fill(conn, open_fill, is_close=False)
        # PnL = (close - open) * base_qty for long.
        pnl = (close_fill.fill_price - open_fill.fill_price) * open_fill.base_qty
        close_id = persist_fill(conn, close_fill, is_close=True, pnl_usd=pnl)
        return {
            "ok": True,
            "dry_run": True,
            "open_fill_id": open_id,
            "close_fill_id": close_id,
            "pnl_usd": pnl,
        }

    api_key = os.environ.get("OKX_DEMO_API_KEY", "")
    secret = os.environ.get("OKX_DEMO_SECRET", "")
    passphrase = os.environ.get("OKX_DEMO_PASSPHRASE", "")
    base_url = resolve_okx_base_url(os.environ.get("OKX_DEMO_BASE"))
    if not (api_key and secret and passphrase):
        return {"ok": False, "error": "missing OKX_DEMO_* env"}

    # Track open-leg state at function scope so an exception during the
    # close leg surfaces ``open_fill_id`` + ``step`` in the error envelope
    # (codex Day 6 round 2 P1 fix).
    open_id_outer: str = ""
    last_step: str = "init"
    try:
        async with OKXAdapter(
            api_key=api_key, secret=secret, passphrase=passphrase, base_url=base_url
        ) as adapter:
            last_step = "open_place"
            # 1. Entry — IOC buy at quote_ccy notional.
            buy_resp = await adapter.place_market_order(
                inst_id=inst_id,
                side="buy",
                notional_usd=notional_usd,
                client_order_id=f"polD6buy{uuid.uuid4().hex[:8]}",
            )
            if not buy_resp.ok or not buy_resp.venue_order_id:
                return {
                    "ok": False,
                    "step": "open",
                    "code": buy_resp.code,
                    "msg": buy_resp.msg,
                }
            last_step = "open_query"
            await asyncio.sleep(0.5)
            buy_state = await adapter.fetch_order(
                inst_id=inst_id, ord_id=buy_resp.venue_order_id
            )
            buy_rows = buy_state.get("data", []) or []
            if not buy_rows:
                return {
                    "ok": False,
                    "step": "open_query",
                    "msg": "no fill row",
                    "raw": buy_state,
                }
            try:
                open_fill = normalize_okx_fill(
                    buy_rows[0], strategy_id=strategy_id
                )
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "step": "open_normalize", "error": repr(exc)}
            open_id = persist_fill(conn, open_fill, is_close=False)
            open_id_outer = open_id
            last_step = "close_place"
            # 2. Close — sell back the base qty just received.
            base_qty = open_fill.base_qty
            if base_qty <= 0.0:
                return {
                    "ok": False,
                    "step": "close",
                    "msg": "open base_qty=0",
                    "open_fill_id": open_id,
                }
            # OKX SPOT close: when tgtCcy=base_ccy the venue interprets ``sz``
            # as base qty, so we must derive base_qty from the entry fill and
            # pass it via a base-ccy market order. We reuse the adapter's
            # market path with a synthetic notional that the adapter converts
            # back to ``sz=base_qty`` (see ``place_market_order``: market +
            # tgt_ccy=base_ccy → sz = notional_usd literal). Therefore we
            # pass ``notional_usd=base_qty`` to satisfy the wire contract.
            sell_resp = await adapter.place_market_order(
                inst_id=inst_id,
                side="sell",
                notional_usd=base_qty,  # interpreted as base qty by OKX
                client_order_id=f"polD6sell{uuid.uuid4().hex[:8]}",
                tgt_ccy="base_ccy",
                ord_type="market",
            )
            close_id = ""
            close_fill_row: dict[str, Any] = {}
            if not sell_resp.ok or not sell_resp.venue_order_id:
                return {
                    "ok": False,
                    "step": "close_place",
                    "code": sell_resp.code,
                    "msg": sell_resp.msg,
                    "open_fill_id": open_id,
                }
            last_step = "close_query"
            await asyncio.sleep(0.5)
            close_state = await adapter.fetch_order(
                inst_id=inst_id, ord_id=sell_resp.venue_order_id
            )
            close_rows = close_state.get("data", []) or []
            if not close_rows:
                return {
                    "ok": False,
                    "step": "close_query",
                    "msg": "no fill row",
                    "open_fill_id": open_id,
                    "close_order_id": sell_resp.venue_order_id,
                }
            try:
                close_fill = normalize_okx_fill(
                    close_rows[0], strategy_id=strategy_id
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "step": "close_normalize",
                    "error": repr(exc),
                    "open_fill_id": open_id,
                }
            pnl = (
                close_fill.fill_price - open_fill.fill_price
            ) * open_fill.base_qty - open_fill.fee_usd - close_fill.fee_usd
            close_id = persist_fill(
                conn, close_fill, is_close=True, pnl_usd=pnl
            )
            close_fill_row = close_rows[0]
            return {
                "ok": True,
                "open_fill_id": open_id,
                "close_fill_id": close_id,
                "open_order_id": buy_resp.venue_order_id,
                "close_order_id": sell_resp.venue_order_id,
                "open_raw": buy_rows[0],
                "close_raw": close_fill_row,
            }
    except Exception as exc:  # noqa: BLE001
        # Surface the last step + open_fill_id so callers can resume / dispatch
        # the orphaned position (codex Day 6 round 2 P1 fix).
        envelope: dict[str, Any] = {
            "ok": False,
            "step": last_step,
            "error": repr(exc),
        }
        if open_id_outer:
            envelope["open_fill_id"] = open_id_outer
        return envelope


