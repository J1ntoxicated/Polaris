"""P0a FIX 3 — no-inert-knob guard (the test that should have caught the ttl no-op).

OFFLINE harness only. Touches NO live trading / sizing / T4 chain / gate
thresholds. behavior-0: only runs the OFFLINE ReplayEngine over injected variant
instances on synthetic fixtures.

The KILL-spike's edge search is only meaningful if every searched knob actually
changes WHICH trades happen (the trade SET). This module asserts, for EVERY knob
in ``PARAM_BOUNDS``, that running the strategy's MIN vs MAX grid value through
``ReplayEngine.run_with_bars`` on a fixture that opens+closes trades yields a
DIFFERENT trade set (entry+exit ts). It also proves the REMOVED knobs
(``ttl_bars`` / ``momentum_gain``) are inert: varying them leaves the trade set
byte-identical — exactly the no-op that slipped through before FIX 2/3.

``session_breakout`` cannot fire through the generic replay (the engine never
opens a session window), so its ``atr_mult`` knob is exercised at the entry-set
level (which bars emit a signal) — the same trade-SET notion, one layer up.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.data.schema import Bar
from polaris.core.evolve.param_bounds import PARAM_BOUNDS
from polaris.core.evolve.variants import make_variant
from polaris.core.replay.engine import ReplayEngine
from polaris.core.replay.models import ReplayConfig
from polaris.scripts._production_indicators import build_real_market_view
from polaris.storage.schema import ALL_DDL
from polaris.strategies import STRATEGY_REGISTRY
from polaris.strategies.base import AltDataView, BarView, BaseStrategy, MarketView
from polaris.strategies.capital_macro_riskoff_catalyst import (
    CapitalMacroRiskoffCatalystStrategy,
)
from polaris.strategies.cfd_fx_range_fade_short import CFDFXRangeFadeShortStrategy
from polaris.strategies.okx_funding_carry_persist import OKXFundingCarryPersistStrategy
from polaris.strategies.session_breakout import SessionBreakoutStrategy
from polaris.strategies.spot_donchian import SpotDonchianStrategy
from polaris.strategies.tsmom import TSMOMStrategy
from polaris.strategies.volume_burst import VolumeBurstStrategy

BASE_TS = 1_700_000_000
HOUR = 3600


def _mk_sandbox() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def _bar(
    i: int, o: float, h: float, lo: float, c: float, vol: float,
    *, iid: str, venue: str, sym: str, interval: str,
) -> Bar:
    return Bar(
        instrument_id=iid, underlying_group_id="c", venue=venue, symbol=sym,
        bar_interval=interval, ts=BASE_TS + i * HOUR, open=o, high=h, low=lo,
        close=c, volume=vol, notional_usd=c * vol, spread_bps_close=4.0, source="test",
    )


def _flat_bar_views(n: int, *, close: float) -> list[BarView]:
    """``n`` identical flat BarViews — a warmup-satisfying MarketView.bars for
    an altdata-only strategy (funding/vix/hy_spread), which reads no
    bar-derived indicator (BG3 archetype-track entry-set tests below)."""
    return [
        BarView(
            ts=BASE_TS + i * HOUR, open=close, high=close * 1.001,
            low=close * 0.999, close=close, volume=1000.0,
        )
        for i in range(n)
    ]


def _replay_trade_set(
    strategy: BaseStrategy, bars: list[Bar], *, interval: str
) -> frozenset[tuple[int, int]]:
    """The trade SET (entry_ts, exit_ts) the engine produces for ``strategy``."""
    iid = bars[0].instrument_id
    cfg = ReplayConfig(
        instrument_ids=(iid,), bar_interval=interval, starting_equity=10_000.0
    )
    res = ReplayEngine(cfg, strategies=[strategy]).run_with_bars(
        _mk_sandbox(), {iid: bars}
    )
    return frozenset((t.entry_ts, t.exit_ts) for t in res.trades)


# ---------------------------------------------------------------------------
# Per-strategy fixtures engineered so MIN vs MAX of each knob discriminate.
# ---------------------------------------------------------------------------


def _volume_burst_bars() -> list[Bar]:
    """1m, periodic vol-spike breakouts. vol_z lands ~2.9 at spikes (between the
    2.0 and 3.0 grid points); atr_pct is high so vol_z is the discriminator."""
    out: list[Bar] = []
    p = 100.0
    for i in range(160):
        spike = i >= 30 and (i - 30) % 14 == 0
        v = 1300.0 if spike else (900.0 if i % 2 == 0 else 1100.0)
        if spike:
            nxt = p * 1.04
            out.append(_bar(i, p, p * 1.05, p * 0.999, nxt, v,
                            iid="okx:BTC-USDT", venue="okx", sym="BTC-USDT", interval="1m"))
        else:
            nxt = p * (1.0005 if i % 2 == 0 else 0.9997)
            out.append(_bar(i, p, p * 1.012, p * 0.988, nxt, v,
                            iid="okx:BTC-USDT", venue="okx", sym="BTC-USDT", interval="1m"))
        p = nxt
    return out


def _volume_burst_atr_bars() -> list[Bar]:
    """1m, tight-range breakouts where atr_pct lands ~0.0008 (between the 0.0003
    and 0.001 grid points): the 0.0003 floor admits the breakout, 0.001 blocks it."""
    out: list[Bar] = []
    p = 100.0
    hr = 0.0004
    for i in range(160):
        spike = i >= 30 and (i - 30) % 10 == 0
        v = 1300.0 if spike else (900.0 if i % 2 == 0 else 1100.0)
        if spike:
            nxt = p * (1 + 1.6 * hr)
            out.append(_bar(i, p, nxt, p * (1 - hr), nxt, v,
                            iid="okx:BTC-USDT", venue="okx", sym="BTC-USDT", interval="1m"))
        else:
            nxt = p
            out.append(_bar(i, p, p * (1 + hr), p * (1 - hr), nxt, v,
                            iid="okx:BTC-USDT", venue="okx", sym="BTC-USDT", interval="1m"))
        p = nxt
    return out


def _adx_trend_bars(*, iid: str, venue: str, sym: str, base: float) -> list[Bar]:
    """1H rising sawtooth (donchian breakouts with moderate ADX) — the adx
    threshold changes which breakouts qualify (entry timing differs)."""
    out: list[Bar] = []
    p = base
    for i in range(180):
        phase = i % 20
        step = 0.015 if phase < 13 else -0.012
        nxt = p * (1 + step)
        out.append(_bar(i, p, max(p, nxt) * 1.001, min(p, nxt) * 0.999, nxt, 1000.0 + i,
                        iid=iid, venue=venue, sym=sym, interval="1H"))
        p = nxt
    return out


def _rsi_dip_bars(*, iid: str, venue: str, sym: str, interval: str) -> list[Bar]:
    """Long uptrend (close>ma200) with frequent deep 3-bar crashes pushing RSI to
    the dip zone, then a fast 2-bar recovery so the position closes and re-opens.
    rsi_threshold 35 catches several dips that 25 blocks (>= 2 trades, differ)."""
    out: list[Bar] = []
    p = 100.0
    for i in range(460):
        ph = (i - 210) % 12 if i >= 210 else -1
        crash = i >= 210 and ph in (0, 1, 2)
        rec = i >= 210 and ph in (3, 4)
        if crash:
            nxt = p * 0.955
            out.append(_bar(i, p, p * 1.0005, nxt * 0.999, nxt, 1000.0 + i,
                            iid=iid, venue=venue, sym=sym, interval=interval))
        elif rec:
            nxt = p * 1.04
            out.append(_bar(i, p, nxt * 1.0005, p * 0.999, nxt, 1000.0 + i,
                            iid=iid, venue=venue, sym=sym, interval=interval))
        else:
            nxt = p * 1.004
            out.append(_bar(i, p, nxt * 1.0005, p * 0.999, nxt, 1000.0 + i,
                            iid=iid, venue=venue, sym=sym, interval=interval))
        p = nxt
    return out


def _fx_range_fade_short_bars(*, iid: str, venue: str, sym: str) -> list[Bar]:
    """1H range-then-overshoot cycles (BG3 archetype track). The phase-6 step is
    trimmed (0.0135 vs the normal 0.015) just enough that the phase-6 bar stays
    UNDER the rolling BB-upper; the phase-7 bar is then the FIRST bar each cycle
    to pierce it, landing at ADX~20.09 -- straddling the adx_range_max grid
    endpoints (20.0 blocks: 20.09>=20.0; 30.0 admits). Empirically tuned against
    ``build_real_market_view`` (see BG3 archetype design notes)."""
    out: list[Bar] = []
    p = 1.1000
    period = 20
    for i in range(period * 3):
        phase = i % period
        step = (0.0135 if phase == 6 else 0.015) if phase < 13 else -0.012
        nxt = p * (1 + step)
        h = max(p, nxt) * 1.001
        lo = min(p, nxt) * 0.999
        out.append(_bar(i, p, h, lo, nxt, 1000.0, iid=iid, venue=venue, sym=sym, interval="1H"))
        p = nxt
    return out


# (strategy_id, knob) -> (strategy_cls, bars, interval). Covers EVERY replay-firing
# knob in PARAM_BOUNDS. session_breakout (atr_mult) / okx_funding_carry_persist
# (funding_threshold) / capital_macro_riskoff_catalyst (vix_threshold,
# hy_spread_threshold) are tested separately (altdata-driven knobs never fire
# through the generic replay engine -- build_real_market_view never populates
# altdata in the engine's bar loop, same reason session_breakout is separate).
def _replay_knob_cases() -> list[tuple[str, str, list[Bar], str]]:
    return [
        ("volume_burst", "vol_z_threshold", _volume_burst_bars(), "1m"),
        ("volume_burst", "atr_floor_pct", _volume_burst_atr_bars(), "1m"),
        ("spot_donchian", "adx_threshold",
         _adx_trend_bars(iid="okx:BTC-USDT", venue="okx", sym="BTC-USDT", base=100.0), "1H"),
        ("fx_breakout_basket", "adx_threshold",
         _adx_trend_bars(iid="capital:EURUSD", venue="capital", sym="EURUSD", base=1.1), "1H"),
        ("rsi_bb_pullback", "rsi_threshold",
         _rsi_dip_bars(iid="okx:BTC-USDT", venue="okx", sym="BTC-USDT", interval="1H"), "1H"),
        ("cfd_fx_range_fade_short", "adx_range_max",
         _fx_range_fade_short_bars(iid="capital:EURUSD", venue="capital", sym="EURUSD"), "1H"),
    ]


@pytest.mark.parametrize(
    ("strategy_id", "knob", "bars", "interval"),
    _replay_knob_cases(),
    ids=[f"{s}.{k}" for s, k, _b, _i in _replay_knob_cases()],
)
def test_retained_knob_changes_trade_set_in_replay(
    strategy_id: str, knob: str, bars: list[Bar], interval: str
) -> None:
    """For EVERY replay-firing PARAM_BOUNDS knob: the MIN vs MAX grid value yields
    a DIFFERENT trade SET through ReplayEngine, on a fixture that opens+closes
    trades. This is the guard that would have caught an inert knob."""
    # volume_burst (#61 live-churn KILL) + spot_donchian (#56 stop-bleeders KILL)
    # were un-registered 2026-06-27 but their modules + offline grids are preserved
    # read-only, so resolve them from the module when the live registry no longer
    # carries them. cfd_fx_range_fade_short is a BG3 archetype-track thesis-first
    # base class PENDING the P0a honest-N gate -- never registered at all yet.
    unregistered: dict[str, type[BaseStrategy]] = {
        "volume_burst": VolumeBurstStrategy,
        "spot_donchian": SpotDonchianStrategy,
        "cfd_fx_range_fade_short": CFDFXRangeFadeShortStrategy,
    }
    if strategy_id in unregistered:
        cls: type[BaseStrategy] = unregistered[strategy_id]
    else:
        cls = STRATEGY_REGISTRY[strategy_id]
    grid = PARAM_BOUNDS[strategy_id][knob]
    lo_val, hi_val = grid[0], grid[-1]
    lo_var = make_variant(cls, {knob: lo_val})
    hi_var = make_variant(cls, {knob: hi_val})
    assert isinstance(lo_var, BaseStrategy) and isinstance(hi_var, BaseStrategy)
    lo_set = _replay_trade_set(lo_var, bars, interval=interval)
    hi_set = _replay_trade_set(hi_var, bars, interval=interval)
    # The fixture opens+closes real trades (at least one of the two settings does).
    assert lo_set or hi_set, f"{strategy_id}.{knob}: fixture produced NO trades"
    # ... and the knob genuinely changes WHICH trades happen (the load-bearing
    # proof — a retained knob must move the trade SET, unlike ttl/momentum_gain).
    assert lo_set != hi_set, (
        f"{strategy_id}.{knob}: MIN({lo_val}) vs MAX({hi_val}) produced the SAME "
        "trade set — the knob is inert (does not change the trade SET)"
    )


def test_session_breakout_atr_mult_changes_entry_set() -> None:
    """session_breakout cannot fire through the generic replay (no session window
    is opened), so its atr_mult knob is exercised at the entry-SET level: a
    flat-then-jump fixture where the jump lands between open+1.0*ATR and
    open+2.0*ATR — atr_mult=1.0 emits on more bars than atr_mult=2.0."""

    def entry_set(atr_mult: float, bars: list[Bar]) -> frozenset[int]:
        var = make_variant(SessionBreakoutStrategy, {"atr_mult": atr_mult})
        fired: set[int] = set()
        for i in range(len(bars)):
            mv = build_real_market_view(
                venue="capital", symbol="US100", timeframe="5m",
                bars=bars[: i + 1], spread_bps=4.0, session_open_window=True,
            )
            if var.generate_raw_signal(mv) is not None:
                fired.add(bars[i].ts)
        return frozenset(fired)

    bars: list[Bar] = []
    p = 5000.0
    # jump sized so it clears open+1.0*ATR (atr_mult=1.0 fires) but not always
    # open+2.0*ATR (atr_mult=2.0 fires on FEWER bars), measured from the TRUE
    # session-open anchor (the bar at/after the UTC session-open boundary), not
    # the legacy bar_views[-60] reference.
    rng, jump = 0.001, 0.005
    for i in range(140):
        bump = i >= 65 and (i - 65) % 6 == 0
        if bump:
            c = p * (1 + jump)
            bars.append(_bar(i, p, c * 1.0002, p * 0.9999, c, 1000.0 + i,
                             iid="capital:US100", venue="capital", sym="US100", interval="5m"))
            p = c * 0.998
        else:
            c = p * (1.00002 if i % 2 == 0 else 0.99998)
            bars.append(_bar(i, p, p * (1 + rng), p * (1 - rng), c, 1000.0 + i,
                             iid="capital:US100", venue="capital", sym="US100", interval="5m"))
            p = c
    lo = entry_set(1.0, bars)
    hi = entry_set(2.0, bars)
    assert len(lo) >= 2 and len(hi) >= 2  # both fire (multi-entry fixture)
    assert lo != hi  # the atr_mult level changes the entry set


def test_okx_funding_carry_persist_threshold_changes_entry_set() -> None:
    """okx_funding_carry_persist reads MarketView.altdata, which the generic
    replay engine never populates (build_real_market_view's engine call omits
    altdata entirely — same reason session_breakout is exercised separately),
    so funding_threshold is exercised at the entry-SET level: a representative
    funding-print sweep where the strict grid endpoint (-0.0005) admits FEWER
    prints than the loose endpoint (-0.0001)."""
    bars = _flat_bar_views(25, close=100.0)  # >= WARMUP_BARS (24); no indicator use

    def entry_set(threshold: float) -> frozenset[float]:
        var = make_variant(OKXFundingCarryPersistStrategy, {"funding_threshold": threshold})
        fired: set[float] = set()
        for funding in (
            -0.0008, -0.0007, -0.0006, -0.00045, -0.0004, -0.00035, -0.0002, -0.00005,
        ):
            mv = MarketView(
                symbol="BTC-USDT", venue="okx", timeframe="1H", bars=bars,
                last_price=100.0, spread_bps=2.0,
                altdata=AltDataView(funding_rate_symbol=funding),
            )
            if var.generate_raw_signal(mv) is not None:
                fired.add(funding)
        return frozenset(fired)

    grid = PARAM_BOUNDS["okx_funding_carry_persist"]["funding_threshold"]
    lo = entry_set(grid[0])   # strict: -0.0005
    hi = entry_set(grid[-1])  # loose: -0.0001
    assert len(lo) >= 2 and len(hi) >= 2  # both admit real prints
    assert lo != hi  # the threshold level changes the entry set
    assert lo < hi  # strict is a STRICT subset (never MORE permissive)


def test_capital_macro_riskoff_vix_threshold_changes_entry_set() -> None:
    """capital_macro_riskoff_catalyst reads MarketView.altdata (vix/hy_spread),
    same altdata-never-populates-through-replay reason as the funding carry
    knob above, so vix_threshold is exercised at the entry-SET level: hy_spread
    held fixed above its own threshold throughout, a VIX-print sweep where the
    strict grid endpoint (28.0) admits FEWER prints than the loose endpoint
    (24.0)."""
    bars = _flat_bar_views(5, close=2000.0)  # >= WARMUP_BARS (5); no indicator use

    def entry_set(threshold: float) -> frozenset[float]:
        var = make_variant(CapitalMacroRiskoffCatalystStrategy, {"vix_threshold": threshold})
        fired: set[float] = set()
        for vix in (35.0, 30.0, 27.0, 25.0, 22.0):
            mv = MarketView(
                symbol="GOLD", venue="capital", timeframe="1H", bars=bars,
                last_price=2000.0, spread_bps=2.0,
                altdata=AltDataView(vix=vix, hy_spread=600.0),
            )
            if var.generate_raw_signal(mv) is not None:
                fired.add(vix)
        return frozenset(fired)

    grid = PARAM_BOUNDS["capital_macro_riskoff_catalyst"]["vix_threshold"]
    lo = entry_set(grid[-1])  # strict: 28.0
    hi = entry_set(grid[0])   # loose: 24.0
    assert len(lo) >= 2 and len(hi) >= 2
    assert lo != hi
    assert lo < hi


def test_capital_macro_riskoff_hy_spread_threshold_changes_entry_set() -> None:
    """Same altdata-entry-SET rationale as the sibling VIX test above, for the
    2nd knob: vix held fixed above its own threshold throughout, an HY-spread
    sweep where the strict grid endpoint (550.0 bps) admits FEWER prints than
    the loose endpoint (450.0 bps)."""
    bars = _flat_bar_views(5, close=2000.0)  # >= WARMUP_BARS (5); no indicator use

    def entry_set(threshold: float) -> frozenset[float]:
        var = make_variant(
            CapitalMacroRiskoffCatalystStrategy, {"hy_spread_threshold": threshold}
        )
        fired: set[float] = set()
        for hy in (650.0, 570.0, 520.0, 470.0, 420.0):
            mv = MarketView(
                symbol="GOLD", venue="capital", timeframe="1H", bars=bars,
                last_price=2000.0, spread_bps=2.0,
                altdata=AltDataView(vix=35.0, hy_spread=hy),
            )
            if var.generate_raw_signal(mv) is not None:
                fired.add(hy)
        return frozenset(fired)

    grid = PARAM_BOUNDS["capital_macro_riskoff_catalyst"]["hy_spread_threshold"]
    lo = entry_set(grid[-1])  # strict: 550.0
    hi = entry_set(grid[0])   # loose: 450.0
    assert len(lo) >= 2 and len(hi) >= 2
    assert lo != hi
    assert lo < hi


# ---------------------------------------------------------------------------
# The inert-proof: the REMOVED knobs do NOT change the trade set (the no-op
# that slipped through before — varying ttl_bars / momentum_gain via a direct
# subclass, NOT make_variant which now rejects them, leaves the set identical).
# ---------------------------------------------------------------------------


def _subclass(base: type[BaseStrategy], attr: str, value: float) -> BaseStrategy:
    cls = type(f"{base.__name__}_{attr}_{value}", (base,), {attr: value})
    inst = cls()
    assert isinstance(inst, BaseStrategy)
    return inst


def test_ttl_bars_is_inert_same_trade_set() -> None:
    """ttl_bars (the signal TTL) is NEVER read by the precise-exit FSM in replay,
    so 5 vs 15 produce a byte-identical trade set. This is the no-op FIX 2 removed
    from PARAM_BOUNDS — and the guard that should have caught it."""
    bars = _volume_burst_bars()
    set5 = _replay_trade_set(_subclass(VolumeBurstStrategy, "ttl_bars", 5), bars, interval="1m")
    set15 = _replay_trade_set(_subclass(VolumeBurstStrategy, "ttl_bars", 15), bars, interval="1m")
    assert set5  # the fixture opens+closes a trade
    assert set5 == set15  # INERT: the signal TTL does not move the trade set


def test_momentum_gain_is_sizing_only_same_trade_set() -> None:
    """momentum_gain scales RawSignal.strength -> sizing_hint -> notional, NOT
    which bars emit a signal. The trade SET (entry/exit ts) is identical for
    2.0 vs 8.0 — off-target for an ENTRY search (FIX 2 removed it)."""
    bars: list[Bar] = []
    p = 100.0
    for i in range(200):
        phase = i % 11
        step = 0.02 if phase < 8 else -0.05
        nxt = p * (1 + step)
        bars.append(_bar(i, p, max(p, nxt), min(p, nxt), nxt, 1000.0 + i,
                         iid="okx:BTC-USDT", venue="okx", sym="BTC-USDT", interval="1H"))
        p = nxt
    set2 = _replay_trade_set(_subclass(TSMOMStrategy, "momentum_gain", 2.0), bars, interval="1H")
    set8 = _replay_trade_set(_subclass(TSMOMStrategy, "momentum_gain", 8.0), bars, interval="1H")
    assert len(set2) >= 2
    assert set2 == set8  # INERT for the trade SET (sizing-only)


_REPLAY_FIRING_KNOBS = {
    (s, k) for s, k, _b, _i in _replay_knob_cases()
} | {
    ("session_breakout", "atr_mult"),
    ("okx_funding_carry_persist", "funding_threshold"),
    ("capital_macro_riskoff_catalyst", "vix_threshold"),
    ("capital_macro_riskoff_catalyst", "hy_spread_threshold"),
}


def test_fix3_covers_every_param_bounds_knob() -> None:
    """Guard the guard: every knob in PARAM_BOUNDS is exercised by a case above."""
    declared = {(s, k) for s, params in PARAM_BOUNDS.items() for k in params}
    assert declared == _REPLAY_FIRING_KNOBS, (
        f"FIX 3 coverage gap: {declared ^ _REPLAY_FIRING_KNOBS}"
    )
