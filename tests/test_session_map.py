"""#48 global session-aware focus rotation — per-instrument session map.

DEMO/PAPER. AGGRESSIVE / flow_not_block: the session map produces a per-instrument
session WEIGHT (1.0 = active-session, < 1.0 = dormant) used ONLY to RE-ALLOCATE
focus seats — never to block an entry, cut a size, or gate an exit. A dormant
instrument is DEPRIORITIZED (fewer seats), NEVER excluded; when its session
returns the weight flips back to 1.0 (automatic restore). OKX crypto is ALWAYS
1.0 (24/7, never gated). A symbol with no session mapping → 1.0 (flow_not_block:
unknown = active, never silently deprioritized). cold-start (a freshly-opened
session) is ACTIVE, not penalized. These tests pin the exchange-fact windows:

  - OKX crypto 24/7 → 1.0 at every hour (Jin mandate ③);
  - Asia UTC hour → J225/HK50/AU200AU active, US100/DE40 dormant;
  - Europe UTC hour → DE40/UK100/FR40 active, J225/US100 dormant;
  - US UTC hour → US100/US500/US30 active, J225/DE40 dormant;
  - FX major (forex) 24/5 → active on a weekday, dormant on the weekend;
  - commodity (GOLD) → 1.0 (global 24/5, no single cash session);
  - unknown index symbol → 1.0 (flow_not_block, unknown = active);
  - weekend non-crypto → dormant (the cash book is shut).
"""

from __future__ import annotations

import datetime as dt

from polaris.scripts import _session_map as sm


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(dt.datetime(year, month, day, hour, minute, tzinfo=dt.UTC).timestamp())


# A known weekday (Wed 2026-06-24) and a weekend day (Sat 2026-06-27).
_WED = (2026, 6, 24)
_SAT = (2026, 6, 27)


# ---------------------------------------------------------------------------
# OKX crypto 24/7 — ALWAYS active (Jin mandate ③).
# ---------------------------------------------------------------------------


def test_okx_crypto_always_active_every_hour() -> None:
    """OKX crypto is never session-gated — 1.0 at every hour, weekend included."""
    for hour in range(0, 24, 3):
        w_wed = sm.instrument_session_weight("okx", "crypto", "BTC-USDT",
                                             _utc(*_WED, hour))
        assert w_wed == 1.0, f"okx must be 1.0 at UTC {hour:02d}:00 weekday"
    # Weekend too (crypto never closes).
    assert sm.instrument_session_weight("okx", "crypto", "ETH-USDT",
                                        _utc(*_SAT, 3)) == 1.0


# ---------------------------------------------------------------------------
# Asia session — J225 / HK50 / AU200AU active in the Asia UTC window.
# ---------------------------------------------------------------------------


def test_asia_session_active_asia_hour() -> None:
    """At 03:00 UTC (Asia cash open) the Asia indices are active, others dormant."""
    now = _utc(*_WED, 3)
    assert sm.instrument_session_weight("capital", "index", "J225", now) == 1.0
    assert sm.instrument_session_weight("capital", "index", "HK50", now) == 1.0
    assert sm.instrument_session_weight("capital", "index", "AU200AU", now) == 1.0
    # US / Europe indices are OUTSIDE their cash session at 03:00 UTC → dormant.
    assert sm.instrument_session_weight("capital", "index", "US100", now) < 1.0
    assert sm.instrument_session_weight("capital", "index", "DE40", now) < 1.0


# ---------------------------------------------------------------------------
# Europe session — DE40 / UK100 / FR40 active in the Europe UTC window.
# ---------------------------------------------------------------------------


def test_europe_session_active_europe_hour() -> None:
    """At 10:00 UTC (London/Frankfurt cash) the Europe indices are active."""
    now = _utc(*_WED, 10)
    assert sm.instrument_session_weight("capital", "index", "DE40", now) == 1.0
    assert sm.instrument_session_weight("capital", "index", "UK100", now) == 1.0
    assert sm.instrument_session_weight("capital", "index", "FR40", now) == 1.0
    # Asia closed by 10:00 UTC; US not open yet → both dormant.
    assert sm.instrument_session_weight("capital", "index", "J225", now) < 1.0
    assert sm.instrument_session_weight("capital", "index", "US100", now) < 1.0


# ---------------------------------------------------------------------------
# US session — US100 / US500 / US30 active in the US RTH window.
# ---------------------------------------------------------------------------


def test_us_session_active_us_hour() -> None:
    """At 15:00 UTC (US cash RTH) the US indices are active, Asia/Europe dormant."""
    now = _utc(*_WED, 15)
    assert sm.instrument_session_weight("capital", "index", "US100", now) == 1.0
    assert sm.instrument_session_weight("capital", "index", "US500", now) == 1.0
    assert sm.instrument_session_weight("capital", "index", "US30", now) == 1.0
    # Asia long closed; Europe near/after its close → dormant.
    assert sm.instrument_session_weight("capital", "index", "J225", now) < 1.0
    assert sm.instrument_session_weight("capital", "index", "AU200AU", now) < 1.0


# ---------------------------------------------------------------------------
# FX majors — 24/5: active any weekday hour, dormant on the weekend.
# ---------------------------------------------------------------------------


def test_fx_major_weekday_active_weekend_dormant() -> None:
    """FX is 24/5 — active at any weekday hour, dormant on the weekend book-shut."""
    # Weekday, an off-hour for every equity session (e.g. 03:00 UTC) — still active.
    assert sm.instrument_session_weight("capital", "forex", "EURUSD",
                                        _utc(*_WED, 3)) == 1.0
    assert sm.instrument_session_weight("capital", "forex", "GBPUSD",
                                        _utc(*_WED, 22)) == 1.0
    # Weekend → dormant (the FX book is shut).
    assert sm.instrument_session_weight("capital", "forex", "EURUSD",
                                        _utc(*_SAT, 12)) < 1.0


# ---------------------------------------------------------------------------
# Commodity — global 24/5, no single cash session → always 1.0.
# ---------------------------------------------------------------------------


def test_commodity_always_active_weekday() -> None:
    """A commodity (GOLD) has no single national cash session → 1.0 any weekday hour."""
    assert sm.instrument_session_weight("capital", "commodity", "GOLD",
                                        _utc(*_WED, 3)) == 1.0
    assert sm.instrument_session_weight("capital", "commodity", "GOLD",
                                        _utc(*_WED, 15)) == 1.0


# ---------------------------------------------------------------------------
# Unknown symbol — flow_not_block: unknown = active (never silently deprioritized).
# ---------------------------------------------------------------------------


def test_unknown_index_symbol_defaults_active() -> None:
    """An index symbol with no session-group mapping defaults to 1.0 (flow_not_block)."""
    assert sm.instrument_session_weight("capital", "index", "ZZ999",
                                        _utc(*_WED, 3)) == 1.0
    assert sm.instrument_session_weight("capital", "index", "ZZ999",
                                        _utc(*_WED, 15)) == 1.0


# ---------------------------------------------------------------------------
# Weekend — non-crypto cash books are shut → dormant; crypto stays active.
# ---------------------------------------------------------------------------


def test_weekend_non_crypto_dormant_crypto_active() -> None:
    """Saturday: every Capital index is dormant; OKX crypto stays 1.0."""
    sat = _utc(*_SAT, 10)
    assert sm.instrument_session_weight("capital", "index", "DE40", sat) < 1.0
    assert sm.instrument_session_weight("capital", "index", "US100", sat) < 1.0
    assert sm.instrument_session_weight("okx", "crypto", "BTC-USDT", sat) == 1.0


# ---------------------------------------------------------------------------
# Dormant weight is a DEPRIORITIZE floor, never zero (exclusion ban).
# ---------------------------------------------------------------------------


def test_dormant_weight_is_positive_floor_not_zero() -> None:
    """A dormant instrument keeps a POSITIVE weight (deprioritize, never excluded)."""
    w = sm.instrument_session_weight("capital", "index", "US100", _utc(*_WED, 3))
    assert 0.0 < w < 1.0  # strictly between 0 and 1 — present but deprioritized
