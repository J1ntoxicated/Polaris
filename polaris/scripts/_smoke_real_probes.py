"""Day 5 paper-loop smoke — real-venue probe helpers.

Split out from ``smoke_paper_loop.py`` to keep that script under the 500-line
guideline. Each function returns a JSON-serialisable dict so the caller can
embed it in the daily vault summary.

DEMO ONLY: ``us.okx.com`` + ``x-simulated-trading: 1`` for OKX, and
``demo-api-capital.backend-capital.com`` for Capital. Never hits live capital.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from polaris.venues.capital import CapitalAdapter, CapitalSession
from polaris.venues.okx import OKXAdapter

__all__ = ["real_capital_probe", "real_okx_probe"]


async def real_okx_probe(
    *, notional_usd: float = 10.0, slippage_bps: float = 5.0, inst_id: str = "BTC-USDT"
) -> dict[str, Any]:
    """Place a tiny IOC BUY on OKX SPOT demo + fetch the order back."""
    print("[real-okx] starting probe ...")
    api_key = os.environ.get("OKX_DEMO_API_KEY", "")
    secret = os.environ.get("OKX_DEMO_SECRET", "")
    passphrase = os.environ.get("OKX_DEMO_PASSPHRASE", "")
    raw_base = os.environ.get("OKX_DEMO_BASE", "https://us.okx.com")
    # ``feedback_okx_region_endpoint``: Jin's keys live on us.okx.com.
    if "us.okx.com" not in raw_base:
        print(f"[real-okx] WARN: OKX_DEMO_BASE={raw_base} → overriding to https://us.okx.com")
        base_url = "https://us.okx.com"
    else:
        base_url = raw_base
    if not (api_key and secret and passphrase):
        return {"ok": False, "error": "missing OKX_DEMO_* env"}
    try:
        async with OKXAdapter(
            api_key=api_key, secret=secret, passphrase=passphrase, base_url=base_url
        ) as adapter:
            tk = await adapter.fetch_ticker(inst_id)
            print(f"[real-okx] ticker last={tk.get('last')} bid={tk.get('bidPx')}")
            resp = await adapter.place_market_order(
                inst_id=inst_id,
                side="buy",
                notional_usd=notional_usd,
                client_order_id=f"polarisD5{uuid.uuid4().hex[:8]}",
                slippage_bps=slippage_bps,
            )
            print(
                f"[real-okx] order ok={resp.ok} ordId={resp.venue_order_id} "
                f"code={resp.code} msg={resp.msg}"
            )
            order_state: dict[str, Any] = {}
            if resp.venue_order_id:
                order_state = await adapter.fetch_order(
                    inst_id=inst_id, ord_id=resp.venue_order_id
                )
                print(f"[real-okx] fill query → {order_state.get('data')}")
            return {
                "ok": resp.ok,
                "code": resp.code,
                "msg": resp.msg,
                "order_id": resp.venue_order_id,
                "fill_query": order_state,
            }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": repr(exc)}


async def real_capital_probe(
    *, epic: str = "EURUSD", size: float = 100.0
) -> dict[str, Any]:
    """Open + close a min-lot CFD position on Capital demo."""
    api_key = os.environ.get("CAP_API_KEY", "")
    email = os.environ.get("CAP_EMAIL", "")
    password = os.environ.get("CAP_PASSWORD", "")
    base_url = os.environ.get(
        "CAP_BASE_DEMO", "https://demo-api-capital.backend-capital.com"
    )
    if not (api_key and email and password):
        return {"ok": False, "error": "missing CAP_* env"}
    print("[real-capital] starting probe ...")
    try:
        async with CapitalSession(
            api_key=api_key, identifier=email, password=password, base_url=base_url
        ) as sess:
            await sess.login()
            tok_acc = sess.tokens.account_id if sess.tokens else "NONE"
            print(f"[real-capital] logged in account={tok_acc}")
            adapter = CapitalAdapter(sess)
            open_resp = await adapter.open_position(
                epic=epic, direction="BUY", size=size
            )
            print(
                f"[real-capital] open ok={open_resp.ok} "
                f"ref={open_resp.deal_reference} status={open_resp.status}"
            )
            confirm: dict[str, Any] = {}
            if open_resp.deal_reference:
                # Brief pause so the deal propagates server-side.
                await asyncio.sleep(0.5)
                confirm = await adapter.confirm(open_resp.deal_reference)
                print(
                    f"[real-capital] confirm dealStatus={confirm.get('dealStatus')} "
                    f"level={confirm.get('level')}"
                )
            close_resp: dict[str, Any] = {}
            # Capital reports the actual position dealId in
            # ``affectedDeals[0]``, not the request envelope's ``dealId``.
            position_deal_id: str | None = None
            affected = confirm.get("affectedDeals") or []
            if isinstance(affected, list) and affected:
                first = affected[0] if isinstance(affected[0], dict) else {}
                position_deal_id = first.get("dealId")
            if not position_deal_id:
                position_deal_id = confirm.get("dealId") or open_resp.deal_id
            if position_deal_id and confirm.get("dealStatus") in ("ACCEPTED", "OPEN"):
                close_attempt = await adapter.close_position(str(position_deal_id))
                close_resp = {
                    "ok": close_attempt.ok,
                    "ref": close_attempt.deal_reference,
                    "status": close_attempt.status,
                    "deal_id": position_deal_id,
                }
                print(f"[real-capital] close → {close_resp}")
            return {
                "ok": open_resp.ok,
                "open_ref": open_resp.deal_reference,
                "open_status": open_resp.status,
                "confirm": confirm,
                "close": close_resp,
            }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": repr(exc)}
