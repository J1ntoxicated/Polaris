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


# ---------------------------------------------------------------------------
# session_warm_active — pre-open bar-warming predicate (#66, Jin "장 열기 전부터
# 거래가능 바 알아서 채워야"). DATA WARMING ONLY: this picks which symbols get
# their 1m bars pre-fetched in the [open - WARM_LEAD_MIN, close) window so the
# recency gate sees fresh bars at the cash open. It NEVER touches the TRADE
# weight (``instrument_session_weight``) — a symbol is FETCH-active T-X before
# open while still TRADE-dormant until the open itself.
# ---------------------------------------------------------------------------


def test_warm_active_lead_window_before_us_open() -> None:
    """A US index is warm-active in the [open - LEAD, open) pre-open window.

    With the default 30-min lead, US cash opens at 13:30 UTC → warming starts at
    13:00 UTC. At 13:10 the symbol is FETCH-active (warm) even though it is not
    yet TRADE-active (still dormant by the weight) until 13:30.
    """
    pre_open = _utc(*_WED, 13, 10)  # 13:10 UTC — 20 min before the 13:30 US open
    assert sm.session_warm_active("capital", "index", "US100", pre_open) is True
    # TRADE weight is UNTOUCHED — still dormant before the open (no lead concept).
    assert sm.instrument_session_weight("capital", "index", "US100", pre_open) < 1.0


def test_warm_active_just_before_lead_window_is_false() -> None:
    """Outside the lead window (before open - LEAD) the symbol is NOT warm yet."""
    too_early = _utc(*_WED, 12, 50)  # 12:50 UTC — 40 min before open, lead=30
    assert sm.session_warm_active("capital", "index", "US100", too_early) is False


def test_warm_active_during_session_then_fresh_at_open() -> None:
    """At and through the cash open the symbol is warm-active (bars stay fresh)."""
    at_open = _utc(*_WED, 13, 30)  # exactly the US open
    mid = _utc(*_WED, 15)          # mid-session
    assert sm.session_warm_active("capital", "index", "US100", at_open) is True
    assert sm.session_warm_active("capital", "index", "US100", mid) is True
    # And TRADE-active flips to 1.0 at the open (warming handed off cleanly).
    assert sm.instrument_session_weight("capital", "index", "US100", at_open) == 1.0


def test_warm_active_after_close_is_false() -> None:
    """After the cash close the symbol drops out of warming automatically."""
    after = _utc(*_WED, 21)  # 21:00 UTC — past the 20:00 US close
    assert sm.session_warm_active("capital", "index", "US100", after) is False


def test_warm_active_europe_pre_open() -> None:
    """A Europe index warms before its 07:00 UTC open (06:30 with the 30-min lead)."""
    pre = _utc(*_WED, 6, 40)  # 06:40 UTC — 20 min before the 07:00 Europe open
    assert sm.session_warm_active("capital", "index", "DE40", pre) is True
    # US index is nowhere near its open at 06:40 → not warming.
    assert sm.session_warm_active("capital", "index", "US100", pre) is False


def test_warm_active_alpaca_equity_absorbed_into_us_window() -> None:
    """Alpaca US equities (the core 미장 gap) warm in the US pre-open window.

    Alpaca stock symbols have no ``_SESSION_GROUP`` entry — they must be absorbed
    into the 'us' window FOR WARMING so the 미장 case (open 13:30 UTC, 1m bars
    0.5h stale → recency gate skip) is covered. The TRADE weight's existing
    None-group behavior is unchanged (warming-only mapping).
    """
    pre_open = _utc(*_WED, 13, 10)  # 20 min before the US open
    assert sm.session_warm_active("alpaca", "equity", "AAPL", pre_open) is True
    assert sm.session_warm_active("alpaca", "us_equity", "TSLA", pre_open) is True
    # But NOT outside the US lead window.
    assert sm.session_warm_active("alpaca", "equity", "AAPL",
                                  _utc(*_WED, 3)) is False


def test_warm_active_alpaca_equity_does_not_touch_trade_weight() -> None:
    """The Alpaca→'us' warming map must NOT alter ``instrument_session_weight``.

    The weight's prior behavior for an unmapped Alpaca equity (group=None →
    weekday-active 1.0) is preserved — warming reads a separate predicate.
    """
    # 03:00 UTC weekday: warming says NO (US not near open), but the TRADE weight
    # keeps its existing unmapped-equity weekday-active 1.0 (behavior-invariant).
    early = _utc(*_WED, 3)
    assert sm.session_warm_active("alpaca", "equity", "AAPL", early) is False
    assert sm.instrument_session_weight("alpaca", "equity", "AAPL", early) == 1.0


def test_warm_active_crypto_never_warmed() -> None:
    """OKX crypto is 24/7 — never a pre-open warm target (hot path fills its 1m).

    crypto has no cash open to warm toward; the predicate is False at every hour
    so the warming fetch never adds a redundant 1m pull for crypto.
    """
    for hour in range(0, 24, 4):
        assert sm.session_warm_active("okx", "crypto", "BTC-USDT",
                                      _utc(*_WED, hour)) is False
    # Weekend too.
    assert sm.session_warm_active("okx", "crypto", "ETH-USDT",
                                  _utc(*_SAT, 3)) is False


def test_warm_active_fx_and_commodity_not_warmed() -> None:
    """FX (24/5) and commodities (24/5, no cash open) are not pre-open warm targets.

    They have no discrete cash open; the existing background grind keeps them
    fresh. Only mapped cash-session indices + Alpaca US equities are warmed.
    """
    now = _utc(*_WED, 13, 10)  # inside the US lead window — irrelevant for FX/commodity
    assert sm.session_warm_active("capital", "forex", "EURUSD", now) is False
    assert sm.session_warm_active("capital", "commodity", "GOLD", now) is False


def test_warm_active_unmapped_index_not_warmed() -> None:
    """An index with no session-group mapping has no known open → not warmed.

    We cannot compute open - LEAD without a window, so an unmapped symbol is not
    a warm target (the grind still covers it). This is warming-only; the TRADE
    weight's unmapped→weekday-active behavior is untouched.
    """
    assert sm.session_warm_active("capital", "index", "ZZ999",
                                  _utc(*_WED, 13, 10)) is False


def test_warm_active_weekend_off() -> None:
    """Weekend cash books are shut → warming is OFF (matches weekend-dormant)."""
    sat = _utc(*_SAT, 13, 10)  # would be a US lead window on a weekday
    assert sm.session_warm_active("capital", "index", "US100", sat) is False
    assert sm.session_warm_active("alpaca", "equity", "AAPL", sat) is False


def test_warm_lead_min_env_override(monkeypatch: object) -> None:
    """``POLARIS_SESSION_WARM_LEAD_MIN`` widens/narrows the lead (no-hardcode).

    Reloading the module with a 60-min lead makes 12:40 UTC (50 min before the
    13:30 US open) warm-active, whereas the default 30-min lead leaves it cold.
    """
    import importlib

    import polaris.scripts._session_map as sm_mod
    monkeypatch.setenv("POLARIS_SESSION_WARM_LEAD_MIN", "60")  # type: ignore[attr-defined]
    reloaded = importlib.reload(sm_mod)
    try:
        assert reloaded.WARM_LEAD_MIN == 60
        twelve_forty = _utc(*_WED, 12, 40)  # 50 min before US open
        assert reloaded.session_warm_active("capital", "index", "US100",
                                            twelve_forty) is True
    finally:
        monkeypatch.delenv("POLARIS_SESSION_WARM_LEAD_MIN", raising=False)  # type: ignore[attr-defined]
        importlib.reload(reloaded)  # restore module-level default for other tests


# ---------------------------------------------------------------------------
# #84 equity_fetch_active — DATA-FETCH session gate (NOT a trade gate).
#
# Gates ONLY Alpaca US-equity bar fetches when the equity market is fully closed
# (weekend / overnight) AND no pre-open warm window is active. flow_not_block:
# this is "don't fetch data the closed market is not producing", never a trade
# block. OKX crypto (24/7) and Capital FX/index/commodity are NEVER gated by it.
# ---------------------------------------------------------------------------


# 2026-06-24 is a Wed in EDT (UTC-4) → RTH 13:30-20:00 UTC, pre-market 08:00 UTC,
# after-hours runs to 00:00 UTC next day. 03:00 UTC Wed = 23:00 ET Tue = CLOSED.
def test_equity_fetch_active_rth_true() -> None:
    """Alpaca equity DURING RTH (15:00 UTC Wed) → fetch active."""
    assert sm.equity_fetch_active("alpaca", "equity", "AAPL", _utc(*_WED, 15)) is True


def test_equity_fetch_active_closed_overnight_false() -> None:
    """Alpaca equity deep-closed overnight (03:00 UTC Wed = 23:00 ET Tue) → SKIP."""
    assert sm.equity_fetch_active("alpaca", "equity", "AAPL", _utc(*_WED, 3)) is False


def test_equity_fetch_active_weekend_false() -> None:
    """Alpaca equity on Saturday (market shut all day) → SKIP fetch."""
    assert sm.equity_fetch_active("alpaca", "equity", "MSFT", _utc(*_SAT, 12)) is False
    assert sm.equity_fetch_active("alpaca", "equity", "MSFT", _utc(*_SAT, 18)) is False


def test_equity_fetch_active_extended_hours_true() -> None:
    """Pre-market (08:00 UTC) + after-hours (23:00 UTC) → fetch active.

    Paid SIP delivers extended-hours bars, so the data IS changing; only the
    fully-closed window is skipped. ``us_equity_session_state != 'closed'``.
    """
    assert sm.equity_fetch_active("alpaca", "equity", "AAPL", _utc(*_WED, 8)) is True
    assert sm.equity_fetch_active("alpaca", "equity", "AAPL", _utc(*_WED, 23)) is True


def test_equity_fetch_active_okx_crypto_never_gated() -> None:
    """OKX crypto is 24/7 — fetch ALWAYS active, every hour, weekend included."""
    for hour in range(0, 24, 3):
        assert sm.equity_fetch_active("okx", "crypto", "BTC-USDT", _utc(*_WED, hour)) is True
    assert sm.equity_fetch_active("okx", "crypto", "ETH-USDT", _utc(*_SAT, 3)) is True


def test_equity_fetch_active_capital_never_gated() -> None:
    """Capital FX / index / commodity are NOT equity-gated — always fetch active.

    (FX/index session de-prioritisation lives in ``instrument_session_weight``,
    NOT in this equity-only data-fetch gate. This task is equity-scoped only.)
    """
    closed_equity_ts = _utc(*_WED, 3)  # US equity closed here
    assert sm.equity_fetch_active("capital", "forex", "EURUSD", closed_equity_ts) is True
    assert sm.equity_fetch_active("capital", "index", "DE40", closed_equity_ts) is True
    assert sm.equity_fetch_active("capital", "commodity", "GOLD", _utc(*_SAT, 12)) is True


def test_equity_fetch_active_alpaca_crypto_not_equity_gated() -> None:
    """An Alpaca CRYPTO symbol (asset_class=crypto) is 24/7 — never equity-gated.

    Only ``asset_class ∈ {equity,stock,us_equity}`` is gated; Alpaca crypto
    (BTC/USD on the equity venue) keeps fetching through the equity-closed window.
    """
    assert sm.equity_fetch_active("alpaca", "crypto", "BTCUSD", _utc(*_WED, 3)) is True
    assert sm.equity_fetch_active("alpaca", "crypto", "BTCUSD", _utc(*_SAT, 12)) is True


def test_equity_fetch_active_warm_window_overrides_closed(monkeypatch) -> None:
    """When a pre-open WARM window is active, a CLOSED equity still fetches.

    Forward-compat with #66 pre-warm: ``equity_fetch_active`` must OR in
    ``session_warm_active`` so the gate never kills the pre-open backfill. We
    stub the warm predicate True to prove the OR is wired even on a base without
    #66 merged (where the live ``session_warm_active`` is absent / returns False).
    """
    monkeypatch.setattr(
        sm, "_session_warm_active", lambda *a, **k: True, raising=True
    )
    # 03:00 UTC Wed = equity CLOSED, but warm=True → fetch active (pre-warm alive).
    assert sm.equity_fetch_active("alpaca", "equity", "AAPL", _utc(*_WED, 3)) is True


def test_equity_fetch_active_unparseable_ts_active() -> None:
    """A non-finite clock degrades to ACTIVE (flow_not_block: never skip on doubt)."""
    assert sm.equity_fetch_active("alpaca", "equity", "AAPL", float("nan")) is True


# ---------------------------------------------------------------------------
# #66 ⟷ #84 INTEGRATION (silent-kill regression). At integration the #66
# ``session_warm_active`` (this module) and the #84 ``equity_fetch_active`` gate
# (this module) coexist. ``equity_fetch_active`` ORs in the LIVE warm predicate
# via the module-level ``_session_warm_active`` binding, which resolves at import:
#     try:    _session_warm_active = session_warm_active   # the real #66 fn
#     except NameError: _session_warm_active = _warm_inactive  # always-False stub
# The binding is load-bearing on DEFINITION ORDER: ``def session_warm_active`` MUST
# precede the binding block. If it followed, the ``try`` would NameError, bind the
# stub (always-False), and ``equity_fetch_active`` would SILENTLY KILL the #66
# pre-open warm in the closed-but-warm window — and NO pre-existing test catches
# the import-order regression (each side's suite passes either way). These two
# tests pin the live behaviour so the regression can never re-land unnoticed.
# ---------------------------------------------------------------------------


def test_warm_binding_resolves_to_real_session_warm_active() -> None:
    """``_session_warm_active`` binds the REAL #66 fn, not the always-False stub.

    Direct proof of the import-order: a wrong order (def AFTER the binding) would
    bind ``_warm_inactive`` and this identity would fail.
    """
    assert sm._session_warm_active is sm.session_warm_active  # the real #66 fn
    assert sm._session_warm_active is not sm._warm_inactive   # never the stub


def test_equity_closed_but_warm_survives_gate_via_live_binding(
    monkeypatch: object,
) -> None:
    """closed equity + LIVE warm window True → ``equity_fetch_active`` True.

    The exact silent-kill scenario, exercised through the REAL binding (NOT a
    monkeypatched stub — that would mask the very bug). With a 360-min warm lead
    the US warm window opens at 07:30 UTC = 03:30 ET, BEFORE pre-market (04:00 ET),
    so 07:30-08:00 UTC Wed is genuinely ``us_equity_session_state == 'closed'`` yet
    inside the #66 warm window. The gate's first branch (not-closed) is False here,
    so ONLY the warm OR — fed by the live ``_session_warm_active`` — can keep fetch
    alive. If the binding were the stub, this would be False (the #66 pre-open
    backfill silently killed). flow_not_block: the pre-open warm must survive.
    """
    import importlib

    import polaris.scripts._session_map as sm_mod
    monkeypatch.setenv("POLARIS_SESSION_WARM_LEAD_MIN", "360")  # type: ignore[attr-defined]
    reloaded = importlib.reload(sm_mod)
    try:
        # Re-prove the binding survived the reload (still the real fn, not stub).
        assert reloaded._session_warm_active is reloaded.session_warm_active
        closed_warm = _utc(*_WED, 7, 45)  # 07:45 UTC Wed = closed (03:45 ET) + warm
        from polaris.venues.alpaca.equity_session_gate import (
            us_equity_session_state,
        )
        assert us_equity_session_state(closed_warm) == "closed"  # premise: closed
        assert reloaded.session_warm_active(
            "alpaca", "equity", "AAPL", closed_warm
        ) is True  # premise: #66 warm window active
        # The integration claim: the #84 gate keeps fetch ALIVE via the warm OR.
        assert reloaded.equity_fetch_active(
            "alpaca", "equity", "AAPL", closed_warm
        ) is True
    finally:
        monkeypatch.delenv("POLARIS_SESSION_WARM_LEAD_MIN", raising=False)  # type: ignore[attr-defined]
        importlib.reload(reloaded)  # restore module-level default for other tests
