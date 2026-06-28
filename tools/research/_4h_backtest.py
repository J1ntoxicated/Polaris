"""4h-trend backtest on real OKX 4H data with real fees + OOS split + slippage.

DEMO/PAPER research. Aggressive bias preserved. flow_not_block: this VALIDATES
whether 4H trend continuation beats fees (edge), not a defensive block. No
rejection-keyword logic. Honest net: if fee-fatal, report fee-fatal.

Fee model (per task):
  taker  = 70 bps demo round-trip-relevant per LEG (OKX demo flat 70bps)
  maker  = 8 bps real-fee per LEG (maker_fill_shadow real-fee)
Round trip = entry leg + exit leg. We charge per leg.

Strategy variants (long-only, OKX SPOT):
  tsmom      : enter long when close > close[N] (N-bar momentum positive)
  ma_cross   : enter long when fast MA > slow MA (just crossed up)
  donchian   : enter long on close > prior Donchian-D high (breakout)
  confluence : donchian 4H breakout AND 1D regime uptrend (close>SMA_R on 1D)

Exit = let-run: hold H bars then exit at close (fixed horizon, matches
'trend continuation forward-return' measurement). We also report the raw
forward-return distribution (continuation vs reversal) independent of fees.
"""
from __future__ import annotations

import sqlite3
import statistics as st
from dataclasses import dataclass

DB = "/Users/jinyoon/Projects/Polaris/data/research_4h.sqlite"

# Fees in bps PER LEG.
TAKER_BPS = 70.0
MAKER_BPS = 8.0


@dataclass
class Bar:
    ts: int
    o: float
    h: float
    l: float
    c: float


def load(symbol: str, bar: str) -> list[Bar]:
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT ts,o,h,l,cl FROM c WHERE symbol=? AND bar=? ORDER BY ts",
        (symbol, bar),
    ).fetchall()
    db.close()
    return [Bar(*r) for r in rows]


def sma(vals: list[float], i: int, n: int) -> float | None:
    if i + 1 < n:
        return None
    return sum(vals[i - n + 1 : i + 1]) / n


def donchian_high(bars: list[Bar], i: int, d: int) -> float | None:
    if i < d:
        return None
    return max(b.h for b in bars[i - d : i])  # prior d highs (excl current)


def regime_up_1d(d1: list[Bar], ts: int, r: int) -> bool | None:
    """Is the 1D close above its SMA_r as of the most recent 1D bar <= ts?"""
    idx = -1
    for j, b in enumerate(d1):
        if b.ts <= ts:
            idx = j
        else:
            break
    if idx < 0:
        return None
    closes = [b.c for b in d1]
    s = sma(closes, idx, r)
    if s is None:
        return None
    return d1[idx].c > s


# ---- signal generators: return list of entry indices on the 4H series ----

def sig_tsmom(bars: list[Bar], n: int) -> list[int]:
    sigs = []
    for i in range(n, len(bars)):
        if bars[i].c > bars[i - n].c and bars[i - 1].c <= bars[i - 1 - n].c:
            sigs.append(i)  # fresh cross of momentum into positive
    return sigs


def sig_ma_cross(bars: list[Bar], fast: int, slow: int) -> list[int]:
    closes = [b.c for b in bars]
    sigs = []
    for i in range(slow, len(bars)):
        f, s = sma(closes, i, fast), sma(closes, i, slow)
        fp, sp = sma(closes, i - 1, fast), sma(closes, i - 1, slow)
        if None in (f, s, fp, sp):
            continue
        if f > s and fp <= sp:  # fresh cross up
            sigs.append(i)
    return sigs


def sig_donchian(bars: list[Bar], d: int) -> list[int]:
    sigs = []
    for i in range(d, len(bars)):
        dh = donchian_high(bars, i, d)
        if dh is not None and bars[i].c > dh:
            # fresh breakout: prior bar was NOT above its donchian high
            pdh = donchian_high(bars, i - 1, d)
            if pdh is None or bars[i - 1].c <= pdh:
                sigs.append(i)
    return sigs


@dataclass
class Result:
    name: str
    n: int
    win_rate: float
    gross_bps: float
    net_taker_bps: float
    net_maker_bps: float
    fwd_mean_bps: float
    fwd_median_bps: float
    cont_rate: float  # fraction with positive forward return (continuation)


def run_variant(
    name: str,
    bars4: list[Bar],
    entries: list[int],
    horizon: int,
) -> Result | None:
    fwd: list[float] = []  # forward return in bps, gross
    for i in entries:
        j = i + horizon
        if j >= len(bars4):
            continue
        entry_px = bars4[i].c
        exit_px = bars4[j].c
        ret_bps = (exit_px / entry_px - 1.0) * 1e4
        fwd.append(ret_bps)
    if not fwd:
        return None
    n = len(fwd)
    gross = sum(fwd) / n
    wins = sum(1 for x in fwd if x > 0)
    net_taker = gross - 2 * TAKER_BPS
    net_maker = gross - 2 * MAKER_BPS
    return Result(
        name=name,
        n=n,
        win_rate=wins / n,
        gross_bps=gross,
        net_taker_bps=net_taker,
        net_maker_bps=net_maker,
        fwd_mean_bps=gross,
        fwd_median_bps=st.median(fwd),
        cont_rate=wins / n,
    )


def collect_returns(
    symbols: list[str],
    variant: str,
    horizon: int,
    params: dict,
    ts_lo: int = 0,
    ts_hi: int = 10**13,
) -> list[float]:
    """Pool gross forward-returns (bps) across symbols for a variant."""
    pooled: list[float] = []
    for sym in symbols:
        b4 = [b for b in load(sym, "4H") if ts_lo <= b.ts < ts_hi]
        if len(b4) < horizon + 60:
            continue
        if variant == "tsmom":
            ents = sig_tsmom(b4, params["n"])
        elif variant == "ma_cross":
            ents = sig_ma_cross(b4, params["fast"], params["slow"])
        elif variant == "donchian":
            ents = sig_donchian(b4, params["d"])
        elif variant == "confluence":
            d1_all = load(sym, "1D")
            raw = sig_donchian(b4, params["d"])
            ents = [
                i for i in raw
                if regime_up_1d(d1_all, b4[i].ts, params["r"]) is True
            ]
        else:
            raise ValueError(variant)
        for i in ents:
            j = i + horizon
            if j >= len(b4):
                continue
            pooled.append((b4[j].c / b4[i].c - 1.0) * 1e4)
    return pooled


def summarize(name: str, fwd: list[float], slippage_bps: float = 0.0) -> Result:
    n = len(fwd)
    if n == 0:
        return Result(name, 0, 0, 0, 0, 0, 0, 0, 0)
    gross = sum(fwd) / n
    wins = sum(1 for x in fwd if x > 0)
    # slippage applied to both legs
    net_taker = gross - 2 * TAKER_BPS - 2 * slippage_bps
    net_maker = gross - 2 * MAKER_BPS - 2 * slippage_bps
    return Result(
        name=name, n=n, win_rate=wins / n, gross_bps=gross,
        net_taker_bps=net_taker, net_maker_bps=net_maker,
        fwd_mean_bps=gross, fwd_median_bps=st.median(fwd), cont_rate=wins / n,
    )
