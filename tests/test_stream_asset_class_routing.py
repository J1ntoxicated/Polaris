"""STEP 2/3/4 — stream asset-class routing + session-wait + coherence guard.

Jin 2026-05-30 STEP 0 (a): A=OKX crypto-only, B=Capital forex/index/commodity.
This is an INTENDED asset-class routing correction (crypto edge → OKX track A),
NOT a defensive throttle — the active set is corrected to the venue's stream
whitelist; closed-session CFD names WAIT for the next session (not deleted).

Spec source: .claude/plans/stream_regime_integration_2026-05-30.md (STEP 2/3/4).
"""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from polaris.core.streams import (
    asset_class_allowed_for_venue,
    resolve_stream,
)
from polaris.core.universe._capital import (
    CAPITAL_NAV_PATH,
    CAPITAL_SESSION_PATH,
    fetch_capital_instruments,
)
from polaris.core.universe._ranking import (
    apply_stream_asset_class_filter,
    rank_active_universe,
)
from polaris.core.universe.discovery import persist_universe
from polaris.core.universe.schema import UniverseInstrument

NOW = 1_780_000_000


def _inst(
    symbol: str,
    *,
    venue: str,
    asset_class: str,
    state: str = "live",
    quote_ccy: str = "USD",
    vol: float = 5e8,
    spread_bps: float = 2.0,
    atr_pct: float = 3.0,
    depth: float = 200_000.0,
) -> UniverseInstrument:
    return UniverseInstrument(
        venue=venue,
        symbol=symbol,
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"{asset_class}:{symbol}",
        asset_class=asset_class,
        quote_ccy=quote_ccy,
        state=state,
        vol_24h_usd=vol,
        spread_bps=spread_bps,
        atr_24h_pct=atr_pct,
        depth_10bps_usd=depth,
        signal_density_7d=0.0,
        listing_ts=None,
        last_seen_ts=NOW,
    )


# ---------------------------------------------------------------------------
# STEP 2 (SSOT) — asset_class_allowed_for_venue
# ---------------------------------------------------------------------------


def test_ssot_capital_whitelist_excludes_crypto() -> None:
    assert asset_class_allowed_for_venue("capital", "crypto") is False
    assert asset_class_allowed_for_venue("capital", "forex") is True
    # universe plural spellings normalize onto the canonical singular tokens.
    assert asset_class_allowed_for_venue("capital", "indices") is True
    assert asset_class_allowed_for_venue("capital", "commodity") is True


def test_ssot_okx_and_alpaca_unaffected() -> None:
    # A=OKX crypto-only; C=Alpaca equity-only — each keeps its own whitelist.
    assert asset_class_allowed_for_venue("okx", "crypto") is True
    assert asset_class_allowed_for_venue("okx", "forex") is False
    assert asset_class_allowed_for_venue("alpaca", "equity") is True
    assert asset_class_allowed_for_venue("alpaca", "crypto") is False


def test_ssot_unregistered_venue_is_permissive() -> None:
    # Fabricated test/smoke venues must not be silently emptied.
    assert asset_class_allowed_for_venue("fakevenue", "crypto") is True


# ---------------------------------------------------------------------------
# STEP 2 (enforcement) — Capital active universe drops crypto rows
# ---------------------------------------------------------------------------


def test_capital_active_universe_has_zero_crypto() -> None:
    rows = [
        _inst("EURUSD", venue="capital", asset_class="forex"),
        _inst("US500", venue="capital", asset_class="indices"),
        _inst("GOLD", venue="capital", asset_class="commodity"),
        _inst("BTCUSD", venue="capital", asset_class="crypto"),
        _inst("ETHUSD", venue="capital", asset_class="crypto"),
    ]
    out = rank_active_universe(rows, top_n=40)
    classes = {ins.asset_class for ins in out}
    assert "crypto" not in classes
    assert classes == {"forex", "indices", "commodity"}
    assert {ins.symbol for ins in out} == {"EURUSD", "US500", "GOLD"}


def test_filter_helper_drops_only_wrong_venue_class() -> None:
    rows = [
        _inst("EURUSD", venue="capital", asset_class="forex"),
        _inst("BTCUSD", venue="capital", asset_class="crypto"),
    ]
    kept = apply_stream_asset_class_filter(rows)
    assert [ins.symbol for ins in kept] == ["EURUSD"]


def test_okx_crypto_survives_filter() -> None:
    rows = [_inst("BTC-USDT", venue="okx", asset_class="crypto", quote_ccy="USDT")]
    assert apply_stream_asset_class_filter(rows) == rows


def test_capital_crypto_persisted_with_off_venue_reason(
    memdb: sqlite3.Connection,
) -> None:
    # A leftover Capital crypto-CFD row is persisted is_active=0 with an audit
    # reason that names the routing cause (not a misleading "below_rank").
    rows = [
        _inst("EURUSD", venue="capital", asset_class="forex"),
        _inst("BTCUSD", venue="capital", asset_class="crypto"),
    ]
    active = rank_active_universe(rows, top_n=40)
    persist_universe(memdb, rows, is_active_set={i.instrument_id for i in active})
    row = memdb.execute(
        "SELECT is_active, active_reason FROM universe WHERE instrument_id = ?",
        ("capital:BTCUSD",),
    ).fetchone()
    assert row[0] == 0
    assert str(row[1]).startswith("off_venue_class")


# ---------------------------------------------------------------------------
# STEP 3 (session asymmetry) — CFD closed = session-wait, not permanent drop
# ---------------------------------------------------------------------------


def test_cfd_closed_excluded_from_active_but_persisted_for_next_session(
    memdb: sqlite3.Connection,
) -> None:
    # Off-session: the only FX name is closed → active set is empty (Capital
    # trades only when open; OKX 24/7 carries off-session). Row still persists.
    closed = _inst("EURUSD", venue="capital", asset_class="forex", state="closed")
    active = rank_active_universe([closed], top_n=40)
    assert active == []

    active_ids = {ins.instrument_id for ins in active}
    persist_universe(memdb, [closed], is_active_set=active_ids)
    row = memdb.execute(
        "SELECT is_active, active_reason FROM universe WHERE instrument_id = ?",
        ("capital:EURUSD",),
    ).fetchone()
    assert row is not None
    assert row[0] == 0  # waiting, not active
    assert row[1] is not None
    assert "session" in str(row[1])  # session-wait reason, not a hard reject


def test_cfd_revives_next_session_when_open() -> None:
    # Same epic, now TRADEABLE (live) → re-enters the active set next refresh.
    live = _inst("EURUSD", venue="capital", asset_class="forex", state="live")
    out = rank_active_universe([live], top_n=40)
    assert [ins.symbol for ins in out] == ["EURUSD"]


def test_okx_crypto_unaffected_by_session_routing() -> None:
    # 24/7 crypto: still hard-dropped only when genuinely halted.
    live = _inst("BTC-USDT", venue="okx", asset_class="crypto", quote_ccy="USDT")
    halt = _inst(
        "DEAD-USDT", venue="okx", asset_class="crypto", quote_ccy="USDT", state="halt"
    )
    out = rank_active_universe([live, halt], top_n=40)
    assert {ins.symbol for ins in out} == {"BTC-USDT"}


# ---------------------------------------------------------------------------
# STEP 4 (coherence guard) — runtime crypto re-entry into B-stream is rejected
# ---------------------------------------------------------------------------


def test_coherence_guard_rejects_capital_crypto() -> None:
    from polaris.scripts._production_run_signal import _assert_stream_asset_class_coherent

    stream = resolve_stream("capital")
    # forex/index/commodity are coherent.
    assert _assert_stream_asset_class_coherent(stream, "forex") is True
    assert _assert_stream_asset_class_coherent(stream, "commodity") is True
    # crypto leaking back into the Capital B-stream is rejected.
    assert _assert_stream_asset_class_coherent(stream, "crypto") is False


def test_coherence_guard_allows_okx_crypto() -> None:
    from polaris.scripts._production_run_signal import _assert_stream_asset_class_coherent

    stream = resolve_stream("okx")
    assert _assert_stream_asset_class_coherent(stream, "crypto") is True
    assert _assert_stream_asset_class_coherent(stream, "forex") is False


# ---------------------------------------------------------------------------
# STEP 2a (walk-level efficiency) — crypto_currencies node skipped at fetch time
# ---------------------------------------------------------------------------


class _NavResponder:
    """Mock Capital nav-tree: top-level Currencies (FX) + Crypto Currencies."""

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == CAPITAL_SESSION_PATH:
            return httpx.Response(
                200, json={}, headers={"CST": "cst", "X-SECURITY-TOKEN": "sec"}
            )
        if path == CAPITAL_NAV_PATH:
            # "currenc" matches BOTH nodes; the walk skip must drop the crypto one.
            return httpx.Response(
                200,
                json={
                    "nodes": [
                        {"id": "fx", "name": "Currencies"},
                        {"id": "cc", "name": "Crypto Currencies"},
                    ]
                },
            )
        if path == f"{CAPITAL_NAV_PATH}/fx":
            return httpx.Response(
                200,
                json={
                    "markets": [
                        {"epic": "EURUSD", "bid": 1.10, "offer": 1.1001,
                         "high": 1.11, "low": 1.09, "marketStatus": "TRADEABLE"}
                    ]
                },
            )
        if path == f"{CAPITAL_NAV_PATH}/cc":
            return httpx.Response(
                200,
                json={
                    "markets": [
                        {"epic": "BTCUSD", "bid": 60000.0, "offer": 60010.0,
                         "high": 61000.0, "low": 59000.0, "marketStatus": "TRADEABLE"}
                    ]
                },
            )
        return httpx.Response(404, json={})


class _NavTransport(httpx.AsyncBaseTransport):
    def __init__(self, responder: _NavResponder) -> None:
        self.responder = responder

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self.responder(request)


@pytest.mark.asyncio
async def test_fetch_skips_crypto_currencies_node() -> None:
    client = httpx.AsyncClient(
        transport=_NavTransport(_NavResponder()), base_url="https://demo-api"
    )
    try:
        out = await fetch_capital_instruments(
            api_key="K", email="e@x", password="pw", client=client, now_ts=NOW
        )
    finally:
        await client.aclose()
    symbols = {ins.symbol for ins in out}
    classes = {ins.asset_class for ins in out}
    assert symbols == {"EURUSD"}  # FX kept
    assert "BTCUSD" not in symbols  # crypto_currencies node skipped at walk time
    assert "crypto" not in classes
