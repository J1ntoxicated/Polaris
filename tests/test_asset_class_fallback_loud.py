"""Hardening #5 (2026-06-23) — asset_class silent fallback → LOUD + validated.

When the universe LEFT JOIN yields a NULL/absent asset_class, the code used to
silently fall back to the group_id prefix (default flat 'crypto'). This made a
mis-prefixed or aged-out instrument invisible. Now:

- the resolved ':'-prefix is validated against the KNOWN set
  {crypto, forex, index, commodity, equity};
- a NULL universe class OR a garbage prefix → a WARN log + an idempotent
  ``asset_class_fallback`` risk_events row (so the snapshot can COUNT it);
- the default is venue-correct (Capital → forex, Alpaca → equity, OKX → crypto)
  instead of a flat 'crypto'.

Observability + correct-er default; flow unchanged (no entry blocked/sized).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from polaris.scripts._production_layers import open_position_targets
from polaris.storage.schema import init_db


@pytest.fixture()
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = init_db(tmp_path / "acfb.sqlite")
    yield c
    c.close()


def _open_pos(
    conn: sqlite3.Connection, *, pid: str, venue: str, symbol: str, group_id: str,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        " strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, ?, ?, ?, 's', 's', 's', 'long', 1.0, 'open', 1000, 0)",
        (pid, venue, symbol, group_id),
    )
    conn.commit()


def _fallback_count(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM risk_events WHERE event_type='asset_class_fallback'"
        ).fetchone()[0]
    )


def test_known_universe_class_no_fallback(conn: sqlite3.Connection) -> None:
    """A held name present in universe with a known class → no fallback row."""
    conn.execute(
        "INSERT INTO universe (venue, symbol, instrument_id, underlying_group_id, "
        " asset_class, product_class, stream_id, quote_ccy, state, vol_24h_usd, "
        " spread_bps, atr_24h_pct, depth_10bps_usd, signal_density_7d, "
        " last_seen_ts, is_active) "
        "VALUES ('okx', 'BTC', 'okx:BTC', 'crypto:BTC', 'crypto', 'spot', "
        " 'A_okx_crypto', 'USDT', 'live', 0, 0, 0, 0, 0, 1000, 1)",
    )
    _open_pos(conn, pid="p1", venue="okx", symbol="BTC", group_id="crypto:BTC")

    out = open_position_targets(conn)
    assert ("okx", "BTC", "crypto", "crypto:BTC") in out
    assert _fallback_count(conn) == 0


def test_null_universe_class_is_loud(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    """No universe row → fallback to a KNOWN group_id prefix is LOUD + counted."""
    _open_pos(conn, pid="p1", venue="okx", symbol="HYPE", group_id="crypto:HYPE")

    with caplog.at_level(logging.WARNING):
        out = open_position_targets(conn)

    assert ("okx", "HYPE", "crypto", "crypto:HYPE") in out
    assert _fallback_count(conn) == 1
    assert any("asset_class" in r.message and "HYPE" in r.message for r in caplog.records)


def test_garbage_prefix_counted_not_accepted(conn: sqlite3.Connection) -> None:
    """A garbage prefix is NOT silently accepted — counted + venue-correct default."""
    _open_pos(conn, pid="p1", venue="capital", symbol="GOLD", group_id="bogus:GOLD")

    out = open_position_targets(conn)
    classes = {(v, s): ac for (v, s, ac, _g) in out}
    # garbage 'bogus' rejected → venue-correct default for Capital = forex (NOT crypto).
    assert classes[("capital", "GOLD")] == "forex"
    assert _fallback_count(conn) == 1


def test_fallback_row_idempotent(conn: sqlite3.Connection) -> None:
    """Repeated reads for the same fallback instrument do NOT flood risk_events."""
    _open_pos(conn, pid="p1", venue="okx", symbol="HYPE", group_id="crypto:HYPE")

    for _ in range(5):
        open_position_targets(conn)

    assert _fallback_count(conn) == 1  # one distinct instrument → one row


def test_bar_known_prefix_is_silent(conn: sqlite3.Connection) -> None:
    """The bar path keeps a KNOWN group_id prefix as the SILENT primary SSOT."""
    from polaris.scripts._production_asset_class import resolve_bar_asset_class

    ac = resolve_bar_asset_class(
        venue="capital", symbol="EURUSD", group_id="forex:EURUSD", conn=conn,
    )
    assert ac == "forex"
    assert _fallback_count(conn) == 0  # well-formed group_id → no fallback


def test_bar_garbage_prefix_is_loud_venue_correct(conn: sqlite3.Connection) -> None:
    """A malformed bar group_id → LOUD, COUNTED, venue-correct fallback."""
    from polaris.scripts._production_asset_class import resolve_bar_asset_class

    ac = resolve_bar_asset_class(
        venue="alpaca", symbol="SPCE", group_id="SPCE", conn=conn,  # no prefix
    )
    assert ac == "equity"  # Alpaca venue-correct default, not flat 'crypto'
    assert _fallback_count(conn) == 1


def test_snapshot_exposes_fallback_count(conn: sqlite3.Connection) -> None:
    """The asset_class_fallback count is surfaced for the snapshot."""
    from polaris.scripts.dashboard.snapshot_sections import _rotation_telemetry

    _open_pos(conn, pid="p1", venue="okx", symbol="HYPE", group_id="crypto:HYPE")
    _open_pos(conn, pid="p2", venue="capital", symbol="GOLD", group_id="bogus:GOLD")
    open_position_targets(conn)

    tel = _rotation_telemetry(conn, now_s=3000)
    assert tel.asset_class_fallback_n == 2
