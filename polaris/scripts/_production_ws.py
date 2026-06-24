"""P4 WS producers — build the 3 venue streaming clients sharing one writer.

Design SSOT: ``.claude/plans/p4_ws_realtime_price_2026-06-01.md`` (M3/M4/M5).

``start_ws_producers`` reads the current focus watchlist, partitions it by venue
(okx / capital / alpaca), builds one ``WSStreamClient`` per venue that has any
focus symbols, and spawns each client's ``run`` plus the shared
``QuoteTickWriter.run_flush_loop`` as asyncio tasks on the bot event loop. The
caller (``run_production_paper_loop``) keeps the returned task list and tears it
all down in its ``finally`` (M5): ``stop_evt.set()`` → cancel → ``gather(...,
return_exceptions=True)`` → ``writer.close()``.

Coexistence (no 2nd process / 2nd RW conn): the writer owns a dedicated sqlite
conn opened in the executor thread; the bot's main conn is never touched by the
flush. REST bar ingest stays the fallback — if a venue WS gives up
(``rest_only``) the consumers fall back to bar close. Never halts (AGGRESSIVE).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from typing import TYPE_CHECKING

from polaris.core.universe.schema import ws_budget_for_venue
from polaris.scripts._production_layers import get_focus_targets
from polaris.venues.alpaca.ws import AlpacaQuoteWS
from polaris.venues.capital.ws import CapitalMarketWS
from polaris.venues.okx.ws import OKXTickerWS

if TYPE_CHECKING:
    from polaris.core.data.quote_writer import QuoteTickWriter
    from polaris.venues.capital.session import CapitalSession
    from polaris.venues.ws_common import WSStreamClient

logger = logging.getLogger(__name__)

# Largest per-venue WS budget (Capital 40 / OKX·Alpaca 60 default, raisable) sets
# how deep to read the focus list before the per-venue cap below trims each venue.
_WS_READ_DEPTH_MULT = 3


def _focus_by_venue(
    conn: sqlite3.Connection,
) -> dict[str, list[str]]:
    """Read the latest focus cycle and group ``symbol`` by ``venue``.

    Returns ``{"okx": [...], "capital": [...], "alpaca": [...]}`` (only venues
    with at least one focus symbol appear). The focus list is ordered by merit
    rank (focus_rank asc), so the highest-tier (S) names lead each venue's slice.

    STAGE 1: each venue is capped at its OWN WS budget (``ws_budget_for_venue`` —
    Capital 40 genuine cap, OKX/Alpaca 60 default, env-raisable). flow_not_block:
    a name beyond the WS budget still bar-ingests via REST, it is not dropped.
    """
    # Read deep enough that the largest per-venue budget can be filled.
    max_budget = max(
        ws_budget_for_venue(v) for v in ("okx", "capital", "alpaca")
    )
    targets = get_focus_targets(conn, max_n=max_budget * _WS_READ_DEPTH_MULT)
    by_venue: dict[str, list[str]] = {}
    for venue, symbol, _asset_class, _group in targets:
        by_venue.setdefault(venue, [])
        if symbol not in by_venue[venue]:
            by_venue[venue].append(symbol)
    # Cap each venue's subscription to its own WS budget (Tier-S leads the list).
    return {v: syms[: ws_budget_for_venue(v)] for v, syms in by_venue.items()}


def build_ws_clients(
    conn: sqlite3.Connection,
    *,
    writer: QuoteTickWriter,
    capital_session: CapitalSession | None,
) -> list[WSStreamClient]:
    """Build one WS client per venue that has focus symbols (no tasks yet).

    Each client routes ticks to ``writer.on_quote`` (in-mem only; the writer's
    flush loop does all DB work). OKX is unauthenticated; Capital reuses the
    loop-owned session token; Alpaca uses paper creds + the RTH gate.
    """
    by_venue = _focus_by_venue(conn)
    clients: list[WSStreamClient] = []

    okx_syms = by_venue.get("okx", [])
    if okx_syms:
        clients.append(
            OKXTickerWS(
                symbols=okx_syms,
                on_quote=writer.on_quote,
                rest_env_value=os.environ.get("OKX_DEMO_BASE"),
            )
        )

    cap_syms = by_venue.get("capital", [])
    if cap_syms and capital_session is not None:
        clients.append(
            CapitalMarketWS(
                epics=cap_syms,
                session=capital_session,
                on_quote=writer.on_quote,
                # Weekend → no connect (FX/CFD closed). FX-hours nuance is left to
                # the gate; the coarse weekend gate avoids reconnect storms.
                is_gated=CapitalMarketWS._is_weekend,
            )
        )
    elif cap_syms:
        logger.info(
            "[ws] %d Capital focus symbols but no session — Capital WS disabled "
            "(REST bars remain the fallback)",
            len(cap_syms),
        )

    alp_syms = by_venue.get("alpaca", [])
    if alp_syms:
        from polaris.venues.alpaca import resolve_alpaca_credentials

        key, secret = resolve_alpaca_credentials()
        if key and secret:
            clients.append(
                AlpacaQuoteWS(
                    symbols=alp_syms,
                    api_key=key,
                    api_secret=secret,
                    on_quote=writer.on_quote,
                )
            )
        else:
            logger.info(
                "[ws] %d Alpaca focus symbols but no paper creds — Alpaca WS "
                "disabled (REST bars remain the fallback)",
                len(alp_syms),
            )

    return clients


def start_ws_producers(
    conn: sqlite3.Connection,
    *,
    writer: QuoteTickWriter,
    stop_evt: asyncio.Event,
    capital_session: CapitalSession | None = None,
) -> tuple[list[asyncio.Task[None]], list[WSStreamClient]]:
    """Spawn the writer flush loop + each venue client's ``run`` as tasks (M5).

    Returns ``(tasks, clients)``. The caller stores both in its loop scope and on
    teardown does: ``stop_evt.set()``; ``gather(*tasks, return_exceptions=True)``;
    then ``writer.close()``. ``clients`` is returned so the caller can re-subscribe
    on universe changes (``set_symbols`` / ``set_epics``).
    """
    clients = build_ws_clients(conn, writer=writer, capital_session=capital_session)
    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(writer.run_flush_loop(stop_evt))
    ]
    for client in clients:
        tasks.append(asyncio.create_task(client.run(stop_evt)))
    logger.info(
        "[ws] started %d venue WS client(s) + 1 flush loop: %s",
        len(clients),
        ", ".join(c.venue for c in clients) or "-",
    )
    return tasks, clients


async def resubscribe_ws_clients(
    conn: sqlite3.Connection, clients: list[WSStreamClient]
) -> None:
    """Push the latest focus symbols into the live clients (universe change).

    Increment 1 (incremental re-subscribe): after updating each client's desired
    set, ``apply_subscription_delta`` sends the (un)subscribe delta on the LIVE
    socket — so a focus churn reaches a HEALTHY socket WITHOUT waiting for a
    reconnect (the audit's 'frozen socket' fix). Best-effort + degrade-never-halt:
    a disconnected socket / send failure falls back to the next reconnect's full
    ``subscribe_messages`` (the prior behaviour). flow_not_block: a FLOW INCREASE
    (the socket FOLLOWS focus), never a forced disconnect/churn.
    """
    by_venue = _focus_by_venue(conn)
    for client in clients:
        syms = by_venue.get(client.venue, [])
        if not syms:
            continue
        if isinstance(client, OKXTickerWS):
            client.set_symbols(syms)
        elif isinstance(client, CapitalMarketWS):
            client.set_epics(syms)
        elif isinstance(client, AlpacaQuoteWS):
            client.set_symbols(syms)
        await client.apply_subscription_delta()
