"""Smoke test — Phase 0 P0 Day 2 (Layer 7 + Layer 4 + Capital proxy).

Three pieces:
  1. Capital CFD proxy → 4-axis filter pass count (target ≥ 30 with proxy ON).
  2. Cell matrix → mock 25 cells × 25 trades, verify quartile routing dist.
  3. Allocator fence → asyncio.gather race smoke (2 strategies, no race).

Run: ``python3 -m polaris.scripts.smoke_day2``.
Capital proxy step is skipped if CAP_API_KEY/EMAIL/PASSWORD missing.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import httpx

from polaris.core.cell_matrix import (
    CELL_MIN_POOL_SIZE,
    ROUTING_BOTTOM_MULT,
    ROUTING_TOP_MULT,
    CellContext,
    CellKeyP0,
    TradeClose,
    load_eligible_scores_decayed,
    update_on_trade_close,
)
from polaris.core.isolation import (
    AllocationRequest,
    AllocatorFence,
    build_order_key,
)
from polaris.core.sizing import SizingInput, apply_cell_routing_mult
from polaris.core.universe.discovery import (
    apply_active_filters,
    fetch_capital_instruments,
    fetch_okx_instruments,
)
from polaris.core.universe.schema import default_thresholds
from polaris.storage.schema import init_db
from polaris.venues.capital.market_proxy import (
    CAPITAL_BASE_DEMO,
    CAPITAL_DEPTH_PROXY_FLOOR_USD,
    CAPITAL_VOL_PROXY_FLOOR_USD,
    populate_capital_proxies,
)

DOTENV_PATH = Path(".env")


def _load_dotenv(path: Path = DOTENV_PATH) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# ---------------------------------------------------------------------------
# 1. Capital proxy smoke
# ---------------------------------------------------------------------------


async def _capital_proxy_smoke(now_ts: int) -> tuple[int, int, int]:
    """Returns (capital_raw_count, capital_with_proxy_count, capital_filter_pass)."""
    api_key = os.environ.get("CAP_API_KEY")
    email = os.environ.get("CAP_EMAIL")
    password = os.environ.get("CAP_PASSWORD")

    capital_raw = await fetch_capital_instruments(now_ts=now_ts)
    print(f"[capital] raw nav fetch                 : {len(capital_raw)}")

    if not (api_key and email and password):
        print("[capital] proxy populate skipped (creds missing)")
        return len(capital_raw), 0, 0

    # Re-establish a session to obtain CST/X-SECURITY-TOKEN for chart endpoint.
    async with httpx.AsyncClient(base_url=CAPITAL_BASE_DEMO, timeout=15.0) as cli:
        sess = await cli.post(
            "/api/v1/session",
            headers={"X-CAP-API-KEY": api_key, "Content-Type": "application/json"},
            json={"identifier": email, "password": password},
        )
        if sess.status_code != 200:
            print(f"[capital] session create failed: HTTP {sess.status_code}")
            return len(capital_raw), 0, 0
        cst = sess.headers.get("CST", "")
        sec = sess.headers.get("X-SECURITY-TOKEN", "")
        if not cst or not sec:
            print("[capital] missing CST/X-SECURITY-TOKEN")
            return len(capital_raw), 0, 0
        # Default budget: up to 200 epics to balance smoke runtime vs coverage.
        # Override with CAPITAL_PROXY_SAMPLE env if needed.
        sample_n = int(os.environ.get("CAPITAL_PROXY_SAMPLE", "200"))
        sample = capital_raw[:sample_n]
        with_proxy = await populate_capital_proxies(
            sample,
            cst=cst,
            security_token=sec,
            base_url=CAPITAL_BASE_DEMO,
            client=cli,
        )

    th = default_thresholds()
    filtered = apply_active_filters(with_proxy, th)
    by_class: dict[str, int] = {}
    for f in filtered:
        by_class[f.asset_class] = by_class.get(f.asset_class, 0) + 1
    print(
        f"[capital] proxy populated (sample={len(sample)}) : "
        f"vol≥${CAPITAL_VOL_PROXY_FLOOR_USD:,.0f} OR depth≥${CAPITAL_DEPTH_PROXY_FLOOR_USD:,.0f}"
    )
    print(f"[capital] 4-axis filter pass             : {len(filtered)} {by_class}")
    return len(capital_raw), len(with_proxy), len(filtered)


# ---------------------------------------------------------------------------
# 2. Cell matrix routing smoke
# ---------------------------------------------------------------------------


def _cell_matrix_smoke(db_path: Path, now_ts: int) -> dict[str, int]:
    conn = init_db(db_path)
    try:
        # Clear prior state for repeatable smoke.
        conn.execute("DELETE FROM cell_matrix_p0")
        conn.execute("DELETE FROM cell_matrix_parent3")
        conn.execute("DELETE FROM cell_matrix_parent2")
        conn.execute("DELETE FROM cell_matrix_shadow_context")
        ctx = CellContext(group="spot_intraday", session="asia", direction="long", liquidity_tier="high")
        n_cells = 25
        n_trades = 25
        for i in range(n_cells):
            ticker = f"SMK{i:02d}-USDT"
            for j in range(n_trades):
                pnl = (i - 12) * 0.1  # spread -1.2 .. +1.2
                tr = TradeClose(
                    key=CellKeyP0("okx", "volume_burst", ticker, "bull_trend"),
                    context=ctx,
                    pnl_r=pnl,
                    won=pnl > 0,
                    closed_ts=now_ts + j,
                )
                update_on_trade_close(conn, tr)
        pool = load_eligible_scores_decayed(conn, now_ts=now_ts + n_trades)
        dist = {"top": 0, "bottom": 0, "mid": 0}
        for i in range(n_cells):
            ticker = f"SMK{i:02d}-USDT"
            # Route through Layer 3 sizing entry-point (the only production
            # caller of the L4 routing SSOT) so the smoke exercises the same
            # path strategies will use.
            sizing = SizingInput(
                pre_cell_notional=1000.0,
                cell_key=CellKeyP0("okx", "volume_burst", ticker, "bull_trend"),
                now_ts=now_ts + n_trades,
            )
            decision = apply_cell_routing_mult(conn, sizing)
            mult = float(decision["cell_routing_mult"])
            if mult == ROUTING_TOP_MULT:
                dist["top"] += 1
            elif mult == ROUTING_BOTTOM_MULT:
                dist["bottom"] += 1
            else:
                dist["mid"] += 1
        print(f"[cell]    eligible pool size           : {len(pool)} (gate ≥ {CELL_MIN_POOL_SIZE})")
        print(
            f"[cell]    routing dist                  : top={dist['top']} "
            f"bottom={dist['bottom']} mid={dist['mid']}"
        )
        return dist
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. Allocator fence smoke (asyncio race)
# ---------------------------------------------------------------------------


async def _allocator_fence_smoke(db_path: Path, now_ts: int) -> bool:
    conn = init_db(db_path)
    try:
        conn.execute("DELETE FROM allocator_reservations")
        fence = AllocatorFence(conn)

        async def reserve(strategy_id: str, symbol: str) -> dict[str, object]:
            req = AllocationRequest(
                strategy_id=strategy_id,
                venue="okx",
                symbol=symbol,
                side="buy",
                correlation_group=f"{symbol}-cluster",
                underlying_group_id=f"crypto:{symbol.split('-')[0]}",
                requested_notional=1000.0,
                requested_risk=20.0,
                signal_id=f"sig-{strategy_id}",
                order_key=build_order_key(
                    strategy_id=strategy_id,
                    venue="okx",
                    symbol=symbol,
                    timeframe="1m",
                    signal_ts=now_ts,
                    side="buy",
                ),
            )
            return await fence.check_and_reserve(req, now_ts=now_ts)

        results = await asyncio.gather(
            reserve("A", "BTC-USDT"),
            reserve("B", "ETH-USDT"),
        )
        ids = {r["reservation_id"] for r in results}
        ok = len(ids) == 2 and all(not r["dedupe_hit"] for r in results)
        print(f"[fence]   asyncio race (A + B)         : {'PASS' if ok else 'FAIL'} "
              f"(reservations={len(ids)})")
        return ok
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def run() -> None:
    _load_dotenv()
    now_ts = int(time.time())
    db_path = Path("data/polaris.sqlite")

    print("=" * 60)
    print("Polaris P0 Day 2 — Layer 7 + Layer 4 + Capital proxy smoke")
    print(f"now_ts = {now_ts}")
    print("=" * 60)

    # OKX baseline (sanity).
    try:
        okx = await fetch_okx_instruments(now_ts=now_ts)
        okx_filtered = apply_active_filters(okx)
        print(f"[okx]     raw                            : {len(okx)}")
        print(f"[okx]     4-axis pass                    : {len(okx_filtered)}")
    except Exception as exc:  # noqa: BLE001
        print(f"[okx]     fetch failed                   : {exc!r}")

    # Capital proxy.
    try:
        await _capital_proxy_smoke(now_ts)
    except Exception as exc:  # noqa: BLE001
        print(f"[capital] smoke failed                   : {exc!r}")

    # Cell matrix.
    dist = _cell_matrix_smoke(db_path, now_ts)
    assert dist["top"] >= 1 and dist["bottom"] >= 1, "quartile routing distribution missing"

    # Allocator fence.
    ok = await _allocator_fence_smoke(db_path, now_ts)
    assert ok, "allocator fence asyncio race failed"

    print("=" * 60)
    print("smoke OK")
    print("=" * 60)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
