"""Day 6 — Capital CFD real demo fill round-trip (open + immediate close).

Split out of ``_smoke_real_roundtrip`` to keep each module ≤500 LOC. The OKX
module re-exports the public Capital names so existing import paths keep
working. Defensive: returns ``{"ok": False, ...}`` on any exception so the
smoke loop never aborts; ``dry_run=True`` writes synthetic fills (CI no-creds).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
import uuid
from typing import Any

from polaris.core.data.fill_normalizer import Fill, normalize_capital_confirm
from polaris.core.data.fills_persist import persist_fill
from polaris.scripts._smoke_roundtrip_shared import MIN_CAPITAL_LOT
from polaris.venues.capital import CapitalAdapter, CapitalSession

logger = logging.getLogger(__name__)

__all__ = [
    "real_capital_close_fill",
    "real_capital_open_fill",
    "run_capital_round_trip",
]


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


def _capital_deal_id_from_confirm(confirm: dict[str, Any], open_resp: Any) -> str | None:
    affected = confirm.get("affectedDeals") or []
    deal_id: Any = None
    if isinstance(affected, list) and affected:
        first = affected[0] if isinstance(affected[0], dict) else {}
        deal_id = first.get("dealId")
    deal_id = deal_id or confirm.get("dealId") or getattr(open_resp, "deal_id", None)
    return str(deal_id) if deal_id else None


async def real_capital_open_fill(
    adapter: Any,
    *,
    epic: str,
    direction: str,
    size: float,
    strategy_id: str,
    pip_value_usd: float = 10.0,
    leverage: float = 1.0,
    last_price: float | None = None,
    poll_delay_sec: float = 0.5,
) -> tuple[Fill, str | None] | None:
    """Capital entry leg: open → confirm → normalize.

    Returns ``(fill, deal_id)`` where ``deal_id`` is the position id the close
    leg needs. ``None`` on reject / unconfirmed.
    """
    open_resp = await adapter.open_position(epic=epic, direction=direction, size=size)
    if not open_resp.ok or not open_resp.deal_reference:
        return None
    await asyncio.sleep(poll_delay_sec)
    confirm = await adapter.confirm(open_resp.deal_reference)
    if str(confirm.get("dealStatus")) not in ("ACCEPTED", "OPEN"):
        return None
    fill = normalize_capital_confirm(
        confirm, strategy_id=strategy_id, pip_value_usd=pip_value_usd,
        leverage=leverage, expected_price=last_price,
    )
    return fill, _capital_deal_id_from_confirm(confirm, open_resp)


async def real_capital_close_fill(
    adapter: Any,
    *,
    deal_id: str,
    strategy_id: str,
    pip_value_usd: float = 10.0,
    leverage: float = 1.0,
    poll_delay_sec: float = 0.5,
) -> Fill | None:
    """Capital close leg: close_position(deal_id) → confirm → normalize."""
    close_resp = await adapter.close_position(deal_id)
    if not close_resp.ok or not close_resp.deal_reference:
        return None
    await asyncio.sleep(poll_delay_sec)
    confirm = await adapter.confirm(close_resp.deal_reference)
    return normalize_capital_confirm(
        confirm, strategy_id=strategy_id, pip_value_usd=pip_value_usd,
        leverage=leverage,
    )


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
