"""Day 6 — real demo fill round-trip helpers (open + immediate close).

Spec source:
- vault/30_components/layer-1-canonical-baseline.md (Fill normalizer)
- vault/10_decisions/ADR-003-8-layer-architecture.md (REST polling)

Each ``run_*_round_trip`` helper:

1. Calls the real demo venue adapter for an entry order at the
   minimum-allowable size (OKX = 1 USDT notional / Capital = 1 lot).
2. Polls / confirms the fill.
3. Normalizes via :mod:`polaris.core.data.fill_normalizer`.
4. Persists the entry Fill row through :mod:`polaris.core.data.fills_persist`.
5. Closes the position immediately (round-trip).
6. Persists the close Fill row (with PnL = 0 best-effort; round-trip
   slippage may produce small ± in production).

Returns a dict with the raw venue payloads + persisted fill_ids so the
caller can vault-summarize a successful 2-leg round-trip.

Defensive: every function returns ``{"ok": False, "error": "..."}`` on
any exception so the smoke loop never aborts. Dry-run mode is supported
by passing ``dry_run=True`` — the function returns a synthetic Fill pair
(useful for CI without venue creds).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
import uuid
from typing import Any

from polaris.core.data.fill_normalizer import (
    Fill,
    normalize_capital_confirm,
    normalize_okx_fill,
)
from polaris.core.data.fills_persist import persist_fill
from polaris.venues.capital import CapitalAdapter, CapitalSession
from polaris.venues.okx import OKXAdapter

__all__ = [
    "MIN_OKX_NOTIONAL_USD",
    "MIN_CAPITAL_LOT",
    "resolve_okx_base_url",
    "run_capital_round_trip",
    "run_okx_round_trip",
]


MIN_OKX_NOTIONAL_USD: float = 10.0  # OKX SPOT minimum (BTC-USDT minSz × px ≥ 5 USDT)
MIN_CAPITAL_LOT: float = 1.0  # Capital min_deal_size for FX majors


def resolve_okx_base_url(env_value: str | None) -> str:
    """Force the OKX base URL onto the US region for Jin's demo keys.

    Codex Day 5 fix ([[feedback_okx_region_endpoint]]): keys live on
    ``us.okx.com``; if `.env` ships ``www.okx.com`` (international) we
    must override before the adapter constructs its ``httpx.AsyncClient``.

    Codex Day 7 R3 nit: parse with ``urllib.parse`` and require the
    netloc to **equal** ``us.okx.com`` (or be a sub-domain ending in
    ``.us.okx.com``) so malformed values like ``https://us.okx.com.evil``
    or ``https://evil.example/us.okx.com`` cannot bypass the override.
    """
    from urllib.parse import urlparse

    if not env_value:
        return "https://us.okx.com"
    parsed = urlparse(env_value)
    host = (parsed.netloc or "").lower().split(":")[0]  # strip optional port
    if host == "us.okx.com" or host.endswith(".us.okx.com"):
        return env_value
    return "https://us.okx.com"


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


def _synthetic_capital_fill(*, is_close: bool) -> Fill:
    direction = "sell" if is_close else "buy"
    return Fill(
        venue="capital",
        instrument_id="capital:EURUSD",
        strategy_id="day6_smoke",
        side=direction,  # type: ignore[arg-type]
        size_usd=MIN_CAPITAL_LOT * 10.0,
        fill_price=1.1052 if is_close else 1.1050,
        fee_usd=0.0,
        slippage_bps=1.0,
        ts_ms=int(time.time() * 1000),
        order_id=f"dry_{uuid.uuid4().hex[:12]}",
        client_order_id=f"polD6dry{uuid.uuid4().hex[:6]}",
        base_qty=MIN_CAPITAL_LOT,
        quote_qty=MIN_CAPITAL_LOT * 10.0,
        state="filled",
    )


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


# ---------------------------------------------------------------------------
# Capital CFD round-trip
# ---------------------------------------------------------------------------


async def run_capital_round_trip(
    *,
    conn: sqlite3.Connection,
    epic: str = "EURUSD",
    size: float = MIN_CAPITAL_LOT,
    pip_value_usd: float = 10.0,
    leverage: float = 1.0,
    strategy_id: str = "day6_smoke",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Real Capital demo open + close round-trip (1-lot EURUSD by default).

    Persists 2 fills rows. ``dry_run=True`` writes synthetic fills.
    """
    if dry_run:
        open_fill = _synthetic_capital_fill(is_close=False)
        close_fill = _synthetic_capital_fill(is_close=True)
        open_id = persist_fill(conn, open_fill, is_close=False)
        pnl = (close_fill.fill_price - open_fill.fill_price) * open_fill.base_qty * pip_value_usd
        close_id = persist_fill(conn, close_fill, is_close=True, pnl_usd=pnl)
        return {
            "ok": True,
            "dry_run": True,
            "open_fill_id": open_id,
            "close_fill_id": close_id,
            "pnl_usd": pnl,
        }

    api_key = os.environ.get("CAP_API_KEY", "")
    email = os.environ.get("CAP_EMAIL", "")
    password = os.environ.get("CAP_PASSWORD", "")
    base_url = os.environ.get(
        "CAP_BASE_DEMO", "https://demo-api-capital.backend-capital.com"
    )
    if not (api_key and email and password):
        return {"ok": False, "error": "missing CAP_* env"}
    open_id_outer: str = ""
    last_step: str = "init"
    try:
        async with CapitalSession(
            api_key=api_key, identifier=email, password=password, base_url=base_url
        ) as sess:
            last_step = "login"
            await sess.login()
            adapter = CapitalAdapter(sess)
            last_step = "open_place"
            open_resp = await adapter.open_position(
                epic=epic, direction="BUY", size=size
            )
            if not open_resp.ok or not open_resp.deal_reference:
                return {
                    "ok": False,
                    "step": "open",
                    "status": open_resp.status,
                    "reason": open_resp.reason,
                }
            last_step = "open_confirm"
            await asyncio.sleep(0.5)
            confirm = await adapter.confirm(open_resp.deal_reference)
            try:
                open_fill = normalize_capital_confirm(
                    confirm,
                    strategy_id=strategy_id,
                    pip_value_usd=pip_value_usd,
                    leverage=leverage,
                )
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "step": "open_normalize", "error": repr(exc)}
            open_id = persist_fill(conn, open_fill, is_close=False)
            open_id_outer = open_id
            last_step = "close_place"
            # Resolve the actual deal_id from affectedDeals[0].
            deal_id = None
            affected = confirm.get("affectedDeals") or []
            if isinstance(affected, list) and affected:
                first = affected[0] if isinstance(affected[0], dict) else {}
                deal_id = first.get("dealId")
            deal_id = deal_id or confirm.get("dealId") or open_resp.deal_id
            if not deal_id or confirm.get("dealStatus") not in ("ACCEPTED", "OPEN"):
                return {
                    "ok": False,
                    "step": "close_no_deal_id",
                    "msg": str(confirm.get("dealStatus")),
                    "open_fill_id": open_id,
                }
            close_resp = await adapter.close_position(str(deal_id))
            if not close_resp.ok or not close_resp.deal_reference:
                return {
                    "ok": False,
                    "step": "close_place",
                    "status": close_resp.status,
                    "reason": close_resp.reason,
                    "open_fill_id": open_id,
                }
            last_step = "close_confirm"
            await asyncio.sleep(0.5)
            close_payload = await adapter.confirm(close_resp.deal_reference)
            try:
                close_fill = normalize_capital_confirm(
                    close_payload,
                    strategy_id=strategy_id,
                    pip_value_usd=pip_value_usd,
                    leverage=leverage,
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
            ) * open_fill.base_qty * pip_value_usd
            close_id = persist_fill(
                conn, close_fill, is_close=True, pnl_usd=pnl
            )
            return {
                "ok": True,
                "open_fill_id": open_id,
                "close_fill_id": close_id,
                "open_deal_ref": open_resp.deal_reference,
                "close_payload": close_payload,
            }
    except Exception as exc:  # noqa: BLE001
        envelope: dict[str, Any] = {
            "ok": False,
            "step": last_step,
            "error": repr(exc),
        }
        if open_id_outer:
            envelope["open_fill_id"] = open_id_outer
        return envelope
