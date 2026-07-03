"""``entry_fanout_active`` — session-aware NEW-ENTRY fanout skip (spec_b).

DEMO/PAPER · AGGRESSIVE bias preserved · flow_not_block · 9-stack ban untouched.

This is a NEW-ENTRY-ONLY compute-scheduling predicate (mirrors the shape of
``equity_session_entry_hold`` — an INTEGRITY-class gate, never a P&L throttle):
when a venue's book is closed it produces no new bars/orders, so re-running
``generate_raw_signal`` dispatch for it is a wasted CPU cycle, not a missed
opportunity (the venue would reject the order anyway). It answers ONLY "should
the bar-pipeline strategy dispatch fan-out run for this (venue, asset_class,
symbol) right now" — it is NEVER consulted by the exit/recalc lane, EOD
flatten, rotation, or WS resubscribe (those code paths do not call it at all —
structurally unaffected, so open-position management is untouched).

Routing (see the design doc):
  - alpaca equity  -> delegates to the existing #84 ``equity_fetch_active``
    (weekend + RTH + #66 warm window, byte-identical to the current gate).
  - capital index/commodity -> the cached regional session window
    (``session_group``/``_GROUP_WINDOW``), a HARD boolean (not the soft
    ``SESSION_DORMANT`` weight ``instrument_session_weight`` returns).
  - capital forex -> 24/5 (active any weekday, False only on the weekend).
  - okx / any unmapped venue -> unconditionally True (fail-open,
    flow_not_block: doubt never skips).
"""

from __future__ import annotations

import datetime as dt

from polaris.scripts._session_map import entry_fanout_active

_WED = (2026, 6, 24)
_SAT = (2026, 6, 27)


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(dt.datetime(year, month, day, hour, minute, tzinfo=dt.UTC).timestamp())


# ---------------------------------------------------------------------------
# OKX — always True (fail-open, crypto 24/7).
# ---------------------------------------------------------------------------


def test_okx_always_active_including_weekend() -> None:
    assert entry_fanout_active("okx", "crypto", "BTC-USDT", _utc(*_WED, 3))
    assert entry_fanout_active("okx", "crypto", "ETH-USDT", _utc(*_SAT, 12))


# ---------------------------------------------------------------------------
# Capital forex — 24/5.
# ---------------------------------------------------------------------------


def test_capital_forex_active_weekday_any_hour() -> None:
    assert entry_fanout_active("capital", "forex", "EURUSD", _utc(*_WED, 3))
    assert entry_fanout_active("capital", "forex", "GBPUSD", _utc(*_WED, 22))


def test_capital_forex_dormant_weekend() -> None:
    assert not entry_fanout_active("capital", "forex", "EURUSD", _utc(*_SAT, 12))


# ---------------------------------------------------------------------------
# Capital index/commodity — cached regional session window (hard boolean).
# ---------------------------------------------------------------------------


def test_capital_index_active_inside_its_cash_window() -> None:
    # Asia cash open (03:00 UTC).
    assert entry_fanout_active("capital", "index", "J225", _utc(*_WED, 3))
    # US cash RTH (15:00 UTC).
    assert entry_fanout_active("capital", "index", "US100", _utc(*_WED, 15))


def test_capital_index_inactive_outside_its_cash_window() -> None:
    # US100 at 03:00 UTC (Asia hour) — US cash is shut.
    assert not entry_fanout_active("capital", "index", "US100", _utc(*_WED, 3))
    # J225 at 15:00 UTC (US hour) — Asia cash long closed.
    assert not entry_fanout_active("capital", "index", "J225", _utc(*_WED, 15))


def test_capital_index_dormant_weekend() -> None:
    assert not entry_fanout_active("capital", "index", "US100", _utc(*_SAT, 15))


def test_capital_commodity_unmapped_symbol_active_weekday() -> None:
    """A commodity (no _SESSION_GROUP entry, e.g. GOLD) has no discrete cash
    session -> active any weekday hour (mirrors instrument_session_weight's
    unmapped=active default, flow_not_block)."""
    assert entry_fanout_active("capital", "commodity", "GOLD", _utc(*_WED, 3))
    assert entry_fanout_active("capital", "commodity", "GOLD", _utc(*_WED, 20))


def test_capital_commodity_dormant_weekend() -> None:
    assert not entry_fanout_active("capital", "commodity", "GOLD", _utc(*_SAT, 12))


# ---------------------------------------------------------------------------
# Alpaca equity — delegates to the existing #84 equity_fetch_active.
# ---------------------------------------------------------------------------


def test_alpaca_equity_active_during_rth() -> None:
    # 15:00 UTC = 11:00 ET on a weekday in June (EDT) -> RTH.
    assert entry_fanout_active("alpaca", "equity", "AAPL", _utc(*_WED, 15))


def test_alpaca_equity_inactive_deep_overnight_weekday() -> None:
    # 03:00 UTC = 23:00 ET the prior evening (EDT) -> deep closed, no warm.
    assert not entry_fanout_active("alpaca", "equity", "AAPL", _utc(*_WED, 3))


def test_alpaca_equity_inactive_weekend() -> None:
    assert not entry_fanout_active("alpaca", "equity", "AAPL", _utc(*_SAT, 15))


def test_alpaca_crypto_asset_class_not_equity_gated() -> None:
    """An Alpaca CRYPTO symbol (not the RTH-bound equity class) is never
    equity-gated -> always True, mirroring equity_fetch_active."""
    assert entry_fanout_active("alpaca", "crypto", "BTCUSD", _utc(*_SAT, 3))


# ---------------------------------------------------------------------------
# Unmapped venue -> fail-open True.
# ---------------------------------------------------------------------------


def test_unmapped_venue_defaults_active() -> None:
    assert entry_fanout_active("unknown_venue", "index", "XYZ", _utc(*_SAT, 3))


# ---------------------------------------------------------------------------
# Risk #4 (design doc) — Capital holiday: the deterministic weekday window
# does NOT know holidays, so a holiday weekday reads as "active" (fail-open,
# consistent with feedback_session_clock_must_be_weekday_holiday_aware: doubt
# never skips a fan-out). This is a documented, deliberate limitation, not a
# bug — the venue adapter itself is the authoritative reject path if an order
# is actually attempted on a holiday.
# ---------------------------------------------------------------------------


def test_capital_index_holiday_weekday_still_reads_active_fail_open() -> None:
    """A holiday weekday (e.g. US Independence Day, a Saturday-adjacent
    Friday observed) is NOT modeled — the deterministic clock still reports
    active inside the nominal cash window. Fail-open, never a false skip."""
    # 2026-07-03 is a Friday (a plausible US holiday-observed date) at US RTH.
    assert entry_fanout_active("capital", "index", "US100", _utc(2026, 7, 3, 15))
