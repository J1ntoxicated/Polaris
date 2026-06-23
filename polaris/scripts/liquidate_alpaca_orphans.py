"""Free Alpaca buying power: liquidate UNTRACKED venue orphan positions.

ROOT CAUSE (2026-06-23): the Alpaca PAPER venue held 37 untracked long positions
— opened by an earlier bot generation whose fills were never persisted, then
lost on a reset/reconcile that wiped the internal Alpaca book but not the venue.
They consumed the full ~$55.7k initial margin → ``buying_power = $0`` → every new
entry was rejected ``insufficient_buying_power``. The DB tracks ZERO Alpaca
positions, so the exit engine is BLIND to them and can never close them.

This is the VENUE-side sibling of ``reconcile_alpaca_zombies`` (which only marks
DB ``status='open'`` rows terminal — a no-op here since the DB has 0 Alpaca
rows). It fetches LIVE venue positions, subtracts the bot's tracked-open set
(HARD GUARD), and SELLS each remaining orphan at the exact venue qty to free
buying power.

GUARD CONTRACT (the load-bearing invariant — re-checked at RUN TIME, never
hard-assumed):
* tracked-open set = ``positions`` rows where ``venue='alpaca'`` and
  ``status IN ('open','active')``. A venue position whose symbol is in that set
  is NEVER closed — it is a live position owned by the exit engine.
* Only venue positions NOT in the tracked set are liquidated.
* Each close sells the EXACT venue qty (no dust, no oversell) with an idempotent
  ``client_order_id`` (a retried boot never double-submits). A short orphan
  (negative qty) is BUY-to-closed for the abs qty.
* Market-closed → submit nothing (transient; the next boot/open retries) so a
  market order actually fills rather than queuing blind.

Wired into the production loop startup so it SELF-HEALS every boot (the bot does
a daily restart): a fresh orphan set that re-accumulates is swept on the next
boot. Best-effort + idempotent: a per-symbol reject is logged and skipped, the
sweep never aborts (flow_not_block). A second run after a clean sweep closes 0.

flow_not_block: this FREES capital to TRADE — bot hygiene, never a defensive
throttle / size-cut / entry-block. DEMO/PAPER only; virtual funds.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["liquidate_alpaca_orphans"]


def _tracked_open_symbols(conn: sqlite3.Connection) -> set[str]:
    """The bot's live Alpaca symbols — these are NEVER liquidated.

    A symbol is 'tracked' only while it has an ``open``/``active`` DB row; a
    ``closed``/``reconciled`` row does NOT protect a same-symbol venue orphan.
    """
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM positions "
        "WHERE venue = 'alpaca' AND status IN ('open', 'active')"
    ).fetchall()
    return {str(r[0]) for r in rows}


def _venue_qty(pos: dict[str, Any]) -> float:
    """Signed venue share count (long > 0, short < 0); 0.0 if unparseable."""
    raw = pos.get("qty")
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


async def _market_is_open(adapter: Any) -> bool:
    """True if the US equity session is open. On any clock error, assume CLOSED
    (do not blind-submit a market order that would just queue)."""
    try:
        clock = await adapter.fetch_clock()
    except Exception as exc:  # noqa: BLE001 — clock is best-effort; closed-default
        logger.warning("[alpaca/liq] clock fetch failed %r — treating as closed", exc)
        return False
    return bool(getattr(clock, "is_open", False))


async def liquidate_alpaca_orphans(
    conn: sqlite3.Connection,
    adapter: Any,
    *,
    dry_run: bool = False,
    now_ts: int | None = None,
) -> int:
    """Close every UNTRACKED Alpaca venue position; return the count closed.

    GUARD: only positions whose symbol is NOT in the bot's tracked-open set are
    touched (a tracked/live position is never sold). Returns 0 on a clean venue,
    a closed market, or ``dry_run`` (preview). Best-effort: a per-symbol reject
    is logged + skipped, never raised.
    """
    now = now_ts if now_ts is not None else int(time.time())
    try:
        positions = await adapter.fetch_positions()
    except Exception as exc:  # noqa: BLE001 — no positions fetched → nothing to do
        logger.warning("[alpaca/liq] fetch_positions failed %r — skip sweep", exc)
        return 0
    if not positions:
        return 0

    tracked = _tracked_open_symbols(conn)
    orphans: list[tuple[str, float, bool]] = []
    for pos in positions:
        symbol = str(pos.get("symbol") or "")
        qty = _venue_qty(pos)
        if not symbol or qty == 0.0:
            continue
        if symbol in tracked:
            # HARD GUARD: a live tracked position is owned by the exit engine.
            logger.info(
                "[alpaca/liq] %s is TRACKED (open/active) — NOT liquidated", symbol,
            )
            continue
        is_crypto = str(pos.get("asset_class") or "").lower() == "crypto"
        orphans.append((symbol, qty, is_crypto))

    if not orphans:
        logger.info("[alpaca/liq] no untracked Alpaca venue orphans — nothing to free")
        return 0

    logger.warning(
        "[alpaca/liq] %d untracked Alpaca venue orphans found "
        "(tracked-open set size=%d) — %s",
        len(orphans), len(tracked),
        "DRY-RUN (no sells)" if dry_run else "liquidating to free buying power",
    )
    if dry_run:
        for symbol, qty, _is_crypto in orphans:
            logger.info("[alpaca/liq] would-close %s qty=%.9f", symbol, qty)
        return len(orphans)

    # Market-closed only blocks the EQUITY legs (crypto trades 24/7). A market
    # SELL on a closed equity session would only queue, so defer equities to the
    # next open; crypto orphans still close now.
    equity_market_open = await _market_is_open(adapter)

    closed = 0
    for symbol, qty, is_crypto in orphans:
        if not is_crypto and not equity_market_open:
            logger.info(
                "[alpaca/liq] %s equity market closed — deferred to next "
                "boot/open (no blind queue)", symbol,
            )
            continue
        # Long orphan → SELL the shares; short orphan → BUY to flatten. Always
        # the EXACT abs venue qty (no dust, no oversell, no wrong-side double).
        # Crypto requires TIF=gtc (equity 'day' is rejected for crypto).
        side = "sell" if qty > 0.0 else "buy"
        close_qty = abs(qty)
        tif = "gtc" if is_crypto else "day"
        cl_ord_id = f"polAliq{uuid.uuid4().hex[:10]}"
        try:
            resp = await adapter.place_market_order(
                symbol=symbol, side=side, qty=close_qty,
                client_order_id=cl_ord_id, time_in_force=tif,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort per symbol
            logger.warning("[alpaca/liq] %s close EXC %r — skip", symbol, exc)
            continue
        if not getattr(resp, "ok", False) or not getattr(resp, "venue_order_id", None):
            logger.warning(
                "[alpaca/liq] %s close rejected code=%s msg=%s — skip (no fault)",
                symbol, getattr(resp, "code", "?"),
                (getattr(resp, "msg", "") or "")[:80],
            )
            continue
        closed += 1
        audit = json.dumps(
            {
                "venue": "alpaca",
                "symbol": symbol,
                "side": side,
                "qty": close_qty,
                "venue_order_id": str(getattr(resp, "venue_order_id", "")),
                "reason": "untracked venue orphan liquidated to free buying power",
            },
            separators=(",", ":"),
        )
        with contextlib.suppress(sqlite3.Error):
            conn.execute(
                "INSERT INTO risk_events "
                "(risk_event_id, strategy_id, event_type, created_ts, payload_json) "
                "VALUES (?, 'equity', 'alpaca_orphan_liquidated', ?, ?)",
                (uuid.uuid4().hex, now, audit),
            )
            conn.commit()
        logger.warning(
            "[alpaca/liq] %s CLOSED %s qty=%.9f ordId=%s — freed buying power "
            "(untracked venue orphan, flow_not_block)",
            symbol, side, close_qty, getattr(resp, "venue_order_id", "?"),
        )

    logger.warning(
        "[alpaca/liq] sweep done — %d/%d orphans closed (buying power freed "
        "toward equity)", closed, len(orphans),
    )
    return closed


# ---------------------------------------------------------------------------
# CLI entrypoint (one-shot manual run; the loop wires the function directly)
# ---------------------------------------------------------------------------


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


async def _run(*, db_path: str, dry_run: bool) -> int:
    from polaris.venues.alpaca.adapter import (
        AlpacaAdapter,
        resolve_alpaca_credentials,
    )

    _load_dotenv()
    api_key, secret = resolve_alpaca_credentials()
    if not (api_key and secret):
        print("ALPACA_PAPER_* / ARCHIVE_ALPACA_PAPER_* creds missing — abort")
        return 1
    conn = sqlite3.connect(db_path)
    adapter = AlpacaAdapter(api_key=api_key, secret=secret)
    try:
        n = await liquidate_alpaca_orphans(conn, adapter, dry_run=dry_run)
        print(f"{'[dry-run] would close' if dry_run else 'closed'} {n} orphan(s)")
        return 0
    finally:
        await adapter.aclose()
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="liquidate_alpaca_orphans")
    p.add_argument("--db", default="data/polaris_live.sqlite")
    p.add_argument("--dry-run", action="store_true", default=False,
                   help="Plan + log orphans, submit no sells.")
    args = p.parse_args(argv)
    return asyncio.run(_run(db_path=args.db, dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
