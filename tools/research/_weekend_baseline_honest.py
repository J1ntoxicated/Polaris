"""HONEST re-run: random-weekend-entry baseline + win-rate/hit-target separation
+ exit-alternative OOS for the two weekend OKX maker strategies.

DEMO/PAPER research. flow_not_block: VALIDATES edge vs a regime/beta control.
Honest: every number printed here is computed by this script from real OKX data
(no hand-typed markdown numbers). Thin sample -> say inconclusive.

Reuses the VERBATIM live signal generators (gen_flush / gen_funding) from
_weekend_maker_oos.py so the signal logic is identical to LIVE.

Three deliverables the prior report lacked / faked:
  (1) RANDOM-WEEKEND-ENTRY BASELINE — for each real signal, draw R random
      weekend bars (same symbol, same forward horizon, same atrp-normalisation)
      and measure the forward-R distribution = the market-beta / regime a blind
      weekend long would have earned. EDGE = signal_fwd_R - baseline_fwd_R.
      (The prior report's "-0.052 / +0.085 baseline" existed in NO script.)
  (2) WIN-RATE (pnl>0 fraction) vs HIT-TARGET-RATE (target touched first) —
      labelled SEPARATELY under the deployed bracket. (The prior report's
      "win 64%" was hit_target_rate=0.736 mis-read; the real pnl>0 win-rate is
      computed here from the SAME bracket sim.)
  (3) EXIT ALTERNATIVES OOS — pure-horizon hold vs deployed bracket vs
      ATR-SCALED R-unit bracket (rail stays -1.0R per mandate; only the R-unit
      ATR multiple changes, widening the absolute stop distance). Which is OOS
      positive, in real net-R.

Fees: OKX real maker 8 bps/leg, taker 10 bps/leg (rail). Slip 15 bps/leg.
"""
from __future__ import annotations

import importlib.util
import random
import statistics as st
import sqlite3

_spec = importlib.util.spec_from_file_location(
    "woos", "/Users/jinyoon/Projects/Polaris/tools/research/_weekend_maker_oos.py"
)
woos = importlib.util.module_from_spec(_spec)
import sys  # noqa: E402

sys.modules["woos"] = woos
_spec.loader.exec_module(woos)

CANDLES = woos.CANDLES
MAKER_BPS = woos.MAKER_BPS  # 8.0
TAKER_BPS = woos.TAKER_BPS  # 10.0
SLIP_BPS = 15.0
RAIL_R = woos.RAIL_R  # -1.0
is_weekend = woos.is_weekend
load_bars = woos.load_bars
load_funding = woos.load_funding
gen_flush = woos.gen_flush
gen_funding = woos.gen_funding
atr_pct = woos.atr_pct

random.seed(20260628)  # deterministic baseline draws (reproducible)


def build(name):
    db = sqlite3.connect(CANDLES)
    syms = [r[0] for r in db.execute(
        "SELECT DISTINCT sym FROM candles WHERE bar='1H'").fetchall()]
    db.close()
    flush = name == "flush"
    packs = []
    bars_by_sym = {}
    for s in syms:
        bars = load_bars(s)
        if len(bars) < 60:
            continue
        bars_by_sym[s] = bars
        if flush:
            sigs = gen_flush(bars)
        else:
            rates = load_funding(s)
            if not rates:
                continue
            sigs = gen_funding(bars, rates)
        for sg in sigs:
            sg.sym = s
            packs.append((bars, sg))
    packs.sort(key=lambda x: x[1].ts)
    return packs, bars_by_sym


def weekend_idxs(bars, lo, hi):
    """Indices in [lo,hi) that are weekend bars with a valid atr_pct."""
    out = []
    for i in range(lo, hi):
        if not is_weekend(bars[i].ts):
            continue
        a = atr_pct(bars, i)
        if a is None or a <= 0:
            continue
        out.append(i)
    return out


def fwd_R(bars, i, H):
    """Unbracketed forward return in R at horizon H (R-unit = entry-bar atr%)."""
    j = i + H
    if j >= len(bars):
        return None
    a = atr_pct(bars, i)
    if a is None or a <= 0:
        return None
    return (bars[j].c / bars[i].c - 1) / a


# ---- (1) RANDOM-WEEKEND-ENTRY BASELINE -------------------------------------
def baseline_edge(name, H, draws=20):
    """For each real signal pick `draws` random weekend bars from the SAME
    symbol (regime/beta control: same market, same weekend session structure),
    measure forward-R at the SAME horizon, average -> the per-symbol weekend
    beta. EDGE = mean(signal fwd_R) - mean(baseline fwd_R). OOS uses the late
    40% of signals; baseline draws are confined to the same time-half so the
    control shares the regime.
    """
    packs, bars_by_sym = build(name)
    cut = int(len(packs) * 0.6)

    def collect(subset, lo_frac, hi_frac):
        sig_rs, base_rs = [], []
        for bars, sg in subset:
            sr = fwd_R(bars, sg.idx, H)
            if sr is None:
                continue
            sig_rs.append(sr)
            n = len(bars)
            lo = int(n * lo_frac)
            hi = int(n * hi_frac)
            cand = weekend_idxs(bars, max(lo, 25), min(hi, n - H - 1))
            if not cand:
                continue
            picks = random.sample(cand, min(draws, len(cand)))
            for pi in picks:
                br = fwd_R(bars, pi, H)
                if br is not None:
                    base_rs.append(br)
        return sig_rs, base_rs

    # FULL: baseline drawn from whole symbol history (full-period beta)
    sig_full, base_full = collect(packs, 0.0, 1.0)
    # OOS: signals from late 40%; baseline drawn from the SAME late 40% window
    sig_oos, base_oos = collect(packs[cut:], 0.6, 1.0)

    def m(x):
        return round(st.mean(x), 4) if x else None

    def md(x):
        return round(st.median(x), 4) if x else None

    return {
        "horizon_h": H,
        "n_signals_full": len(sig_full),
        "n_baseline_draws_full": len(base_full),
        "signal_fwd_R_mean_FULL": m(sig_full),
        "signal_fwd_R_median_FULL": md(sig_full),
        "baseline_fwd_R_mean_FULL": m(base_full),
        "baseline_fwd_R_median_FULL": md(base_full),
        "EDGE_over_baseline_FULL": round(st.mean(sig_full) - st.mean(base_full), 4)
        if sig_full and base_full else None,
        "n_signals_OOS": len(sig_oos),
        "signal_fwd_R_mean_OOS": m(sig_oos),
        "baseline_fwd_R_mean_OOS": m(base_oos),
        "EDGE_over_baseline_OOS": round(st.mean(sig_oos) - st.mean(base_oos), 4)
        if sig_oos and base_oos else None,
    }


# ---- (2) WIN-RATE vs HIT-TARGET under deployed bracket ----------------------
def fee_R(atrp, exit_taker):
    leg2 = TAKER_BPS if exit_taker else MAKER_BPS
    return ((MAKER_BPS + leg2 + 2 * SLIP_BPS) / 1e4) / atrp


def bracket_trade(bars, sg, target_r, max_hold, r_unit_mult=1.0):
    """Deployed bracket: target_r * (atrp*r_unit_mult) up / -1.0R rail.
    rail is ALWAYS -1.0R (mandate). r_unit_mult scales the ABSOLUTE R-unit
    (and thus both the target distance and the stop distance) WITHOUT changing
    the -1.0R rail coefficient. Returns (net_R, hit_target_bool, hit_rail_bool).
    """
    i = sg.idx
    r = sg.atrp * r_unit_mult
    if r <= 0:
        return None
    e = sg.entry_px
    tpx = e * (1 + target_r * r)
    rpx = e * (1 + RAIL_R * r)
    for k in range(i + 1, min(i + 1 + max_hold, len(bars))):
        b = bars[k]
        if b.l <= rpx:
            return (RAIL_R - fee_R(r, True), False, True)
        if b.h >= tpx:
            return (target_r - fee_R(r, False), True, False)
    j = min(i + max_hold, len(bars) - 1)
    return ((bars[j].c / e - 1) / r - fee_R(r, False), False, False)


def horizon_trade(bars, sg, H):
    i = sg.idx
    r = sg.atrp
    j = i + H
    if j >= len(bars):
        return None
    return (bars[j].c / sg.entry_px - 1) / r - fee_R(r, False)


def winrate_decomp(name):
    packs, _ = build(name)
    flush = name == "flush"
    target_r = 0.30 if flush else 1.0
    max_hold = 6 if flush else 48
    cut = int(len(packs) * 0.6)

    def stat(subset, r_unit_mult=1.0):
        nets, hit_t, hit_rail = [], 0, 0
        for bars, sg in subset:
            o = bracket_trade(bars, sg, target_r, max_hold, r_unit_mult)
            if o is None:
                continue
            net, ht, hr = o
            nets.append(net)
            hit_t += ht
            hit_rail += hr
        if not nets:
            return {}
        return {
            "n": len(nets),
            "mean_net_R": round(st.mean(nets), 4),
            "median_net_R": round(st.median(nets), 4),
            "WIN_RATE_pnl_gt_0": round(sum(1 for x in nets if x > 0) / len(nets), 3),
            "HIT_TARGET_rate": round(hit_t / len(nets), 3),
            "HIT_RAIL_rate": round(hit_rail / len(nets), 3),
        }

    return {
        "strategy": name, "target_r": target_r, "max_hold_h": max_hold,
        "deployed_bracket_FULL": stat(packs, 1.0),
        "deployed_bracket_OOS": stat(packs[cut:], 1.0),
    }


# ---- (3) EXIT ALTERNATIVES OOS ---------------------------------------------
def exit_alternatives(name):
    packs, _ = build(name)
    flush = name == "flush"
    target_r = 0.30 if flush else 1.0
    max_hold = 6 if flush else 48
    H = 6 if flush else 24
    cut = int(len(packs) * 0.6)

    def stat(L):
        L = [x for x in L if x is not None]
        if not L:
            return {}
        return {
            "n": len(L), "mean_net_R": round(st.mean(L), 4),
            "median_net_R": round(st.median(L), 4),
            "win_rate_pnl_gt_0": round(sum(1 for x in L if x > 0) / len(L), 3),
        }

    def br(sub, mult):
        return [bracket_trade(b, s, target_r, max_hold, mult)[0]
                for b, s in sub if bracket_trade(b, s, target_r, max_hold, mult)]

    out = {"strategy": name, "horizon_h": H, "target_r": target_r}
    for label, sub in (("FULL", packs), ("OOS", packs[cut:])):
        out[f"A_deployed_bracket_1.0xATR_{label}"] = stat(br(sub, 1.0))
        out[f"B_pure_horizon_{label}"] = stat(
            [horizon_trade(b, s, H) for b, s in sub])
        out[f"C_bracket_2.0xATR_Runit_{label}"] = stat(br(sub, 2.0))
        out[f"D_bracket_3.0xATR_Runit_{label}"] = stat(br(sub, 3.0))
    return out


if __name__ == "__main__":
    import json
    print("#" * 72)
    print("# HONEST RE-RUN — random-weekend baseline + win/hit split + exit alts")
    print("# all numbers below are this script's output on real OKX 1H data")
    print("#" * 72)
    for name, H in (("flush", 6), ("funding", 24)):
        print(f"\n{'='*72}\n## {name}  (baseline horizon H={H})\n{'='*72}")
        print("\n--- (1) RANDOM-WEEKEND-ENTRY BASELINE (regime/beta control) ---")
        print(json.dumps(baseline_edge(name, H), indent=1))
        print("\n--- (2) WIN-RATE (pnl>0) vs HIT-TARGET-RATE (deployed bracket) ---")
        print(json.dumps(winrate_decomp(name), indent=1))
        print("\n--- (3) EXIT ALTERNATIVES (rail fixed -1.0R; R-unit ATR mult varies) ---")
        print(json.dumps(exit_alternatives(name), indent=1))
