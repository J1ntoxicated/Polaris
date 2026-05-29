"""One-off ops: liquidate OKX demo orphan altcoin bags back to USDT.

Context: the OKX demo account accumulated non-USDT balances from prior BUY
legs whose SELL/close never tracked (orphans), locking equity in altcoins and
starving SPOT longs of liquid USDT. This script sells every non-USDT, non-dust
balance to USDT via market sells so the paper loop has cash to size against.

DEMO/PAPER only — reversible. Dry-run by default; pass ``--live`` to submit.
Skips dust (< --min-usd) and reports rejects (e.g. 51155 compliance) without
aborting. NOT part of the trading loop.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

from polaris.scripts._smoke_roundtrip_shared import resolve_okx_base_url
from polaris.venues.okx import OKXAdapter


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def _run(*, live: bool, min_usd: float) -> int:
    _load_dotenv()
    base = resolve_okx_base_url(os.environ.get("OKX_DEMO_BASE"))
    adapter = OKXAdapter(
        api_key=os.environ["OKX_DEMO_API_KEY"],
        secret=os.environ["OKX_DEMO_SECRET"],
        passphrase=os.environ["OKX_DEMO_PASSPHRASE"],
        base_url=base,
    )
    sold_usd = 0.0
    try:
        bal = await adapter.fetch_balance()
        details = bal.get("data", [{}])[0].get("details", [])
        usdt_before = next(
            (_f(c.get("availBal")) for c in details if c.get("ccy") == "USDT"), 0.0
        )
        print(f"base={base}  mode={'LIVE' if live else 'DRY-RUN'}  min_usd=${min_usd}")
        print(f"USDT availBal before: ${usdt_before:,.2f}")
        print(f"{'CCY':<8} {'availBal':>20} {'eqUsd':>14}  {'inst':<14} action")
        targets = []
        for c in details:
            ccy = str(c.get("ccy"))
            if ccy in ("USDT", "USD", "USDC"):
                continue
            avail = _f(c.get("availBal"))
            eq_usd = _f(c.get("eqUsd"))
            if avail <= 0.0:
                continue
            inst = f"{ccy}-USDT"
            if eq_usd < min_usd:
                print(f"{ccy:<8} {avail:>20.8f} {eq_usd:>14.2f}  {inst:<14} skip(dust)")
                continue
            print(f"{ccy:<8} {avail:>20.8f} {eq_usd:>14.2f}  {inst:<14} "
                  f"{'SELL' if live else 'would-sell'}")
            targets.append((ccy, inst, avail, eq_usd))

        if not live:
            print(f"\n[dry-run] {len(targets)} sells planned, "
                  f"~${sum(t[3] for t in targets):,.2f} est. Re-run with --live.")
            return 0

        for ccy, inst, avail, eq_usd in targets:
            try:
                resp = await adapter.place_market_order(
                    inst_id=inst, side="sell", notional_usd=avail,
                    client_order_id=f"liq{ccy}{uuid.uuid4().hex[:8]}",
                    ord_type="market", tgt_ccy="base_ccy",
                )
            except Exception as exc:  # noqa: BLE001 — best-effort per coin
                print(f"  {inst}: EXC {exc!r}")
                continue
            if resp.ok:
                sold_usd += eq_usd
                print(f"  {inst}: OK ordId={resp.venue_order_id} (~${eq_usd:,.2f})")
            else:
                print(f"  {inst}: REJECT code={resp.code} msg={resp.msg[:80]}")

        bal2 = await adapter.fetch_balance()
        details2 = bal2.get("data", [{}])[0].get("details", [])
        usdt_after = next(
            (_f(c.get("availBal")) for c in details2 if c.get("ccy") == "USDT"), 0.0
        )
        print(f"\nUSDT availBal after:  ${usdt_after:,.2f}  "
              f"(Δ +${usdt_after - usdt_before:,.2f}; ~${sold_usd:,.2f} sold)")
        return 0
    finally:
        await adapter.aclose()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="liquidate_okx_orphans")
    p.add_argument("--live", action="store_true", default=False,
                   help="Actually submit sells (default: dry-run).")
    p.add_argument("--min-usd", type=float, default=5.0,
                   help="Skip balances worth less than this USD (dust).")
    args = p.parse_args(argv)
    return asyncio.run(_run(live=args.live, min_usd=args.min_usd))


if __name__ == "__main__":
    raise SystemExit(main())
