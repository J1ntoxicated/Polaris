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
from polaris.strategies.base import BaseStrategy
from polaris.strategies.session_breakout import SessionBreakoutStrategy
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


def _equity_gap_bars() -> list[Bar]:
    """alpaca 1D, periodic ~2.2% gap-ups (between the 0.015 and 0.03 grid points):
    the 0.015 floor admits the gaps, 0.03 blocks them."""
    out: list[Bar] = []
    p = 100.0
    g = 0.022
    for i in range(40):
        gapup = i >= 3 and i % 4 == 0
        if gapup:
            o = p * (1 + g)
            c = o * 1.001
            out.append(_bar(i, o, c * 1.001, p * 0.999, c, 1000.0 + i,
                            iid="alpaca:AAPL", venue="alpaca", sym="AAPL", interval="1D"))
        else:
            o = p * 0.999
            c = p * 0.999
            out.append(_bar(i, o, p * 1.0005, c * 0.999, c, 1000.0 + i,
                            iid="alpaca:AAPL", venue="alpaca", sym="AAPL", interval="1D"))
        p = c
    return out


# (strategy_id, knob) -> (strategy_cls, bars, interval). Covers EVERY replay-firing
# knob in PARAM_BOUNDS. session_breakout (atr_mult) is tested separately (cannot
# fire through the generic replay engine).
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
        ("equity_rsi_bb_pullback", "rsi_threshold",
         _rsi_dip_bars(iid="alpaca:AAPL", venue="alpaca", sym="AAPL", interval="1D"), "1D"),
        ("equity_gap_go", "gap_pct", _equity_gap_bars(), "1D"),
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
    rng, jump = 0.001, 0.003
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
} | {("session_breakout", "atr_mult")}


def test_fix3_covers_every_param_bounds_knob() -> None:
    """Guard the guard: every knob in PARAM_BOUNDS is exercised by a case above."""
    declared = {(s, k) for s, params in PARAM_BOUNDS.items() for k in params}
    assert declared == _REPLAY_FIRING_KNOBS, (
        f"FIX 3 coverage gap: {declared ^ _REPLAY_FIRING_KNOBS}"
    )
