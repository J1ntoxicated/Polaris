"""Exit-policy attribution: is the live loss a SIGNAL problem or an EXIT problem?

DEMO/PAPER research. flow_not_block. For the SAME weekend maker signals, compare
three exit policies on the SAME real OKX data, all net of real maker fees:
  A. LIVE bracket  : profit_target_r (flush .30 / fund 1.0) + -1.0R rail (current)
  B. pure horizon  : hold H bars, exit at close (no rail truncation)
  C. trail-let-run : exit on a chandelier-style trail (capture trend, cap give-back)
If A<<B,C with the SAME entries => the signal has edge but the EXIT truncates it
(FIX-EXIT). If all three <0 => signal has no edge (KILL).
Also: fee-in-R sanity (median atrp) and the rail-first-in-bar conservatism check.
"""
from __future__ import annotations
import importlib.util, statistics as st, sqlite3
spec=importlib.util.spec_from_file_location("woos","/Users/jinyoon/Projects/Polaris/tools/research/_weekend_maker_oos.py")
m=importlib.util.module_from_spec(spec)
import sys; sys.modules["woos"]=m
# prevent the addendum __main__ from running on import
import builtins
_orig=builtins.__name__
spec.loader.exec_module(m)

CANDLES=m.CANDLES; MAKER_BPS=m.MAKER_BPS; TAKER_BPS=m.TAKER_BPS

def build(name):
    db=sqlite3.connect(CANDLES)
    syms=[r[0] for r in db.execute("SELECT DISTINCT sym FROM candles WHERE bar='1H'").fetchall()]
    db.close()
    flush=name=="flush"; packs=[]
    for s in syms:
        bars=m.load_bars(s)
        if len(bars)<60: continue
        if flush: sigs=m.gen_flush(bars)
        else:
            rates=m.load_funding(s)
            if not rates: continue
            sigs=m.gen_funding(bars,rates)
        for sg in sigs: sg.sym=s; packs.append((bars,sg))
    packs.sort(key=lambda x:x[1].ts)
    return packs

def fee_R(atrp, exit_taker):
    leg2=TAKER_BPS if exit_taker else MAKER_BPS
    return ((MAKER_BPS+leg2+2*15.0)/1e4)/atrp  # 15bps slip/leg

def exit_A_bracket(bars,sg,target_r,max_hold):
    i=sg.idx; r=sg.atrp; e=sg.entry_px
    tpx=e*(1+target_r*r); rpx=e*(1-r)
    for k in range(i+1,min(i+1+max_hold,len(bars))):
        b=bars[k]
        if b.l<=rpx: return -1.0-fee_R(r,True)   # rail = taker
        if b.h>=tpx: return target_r-fee_R(r,False)
    j=min(i+max_hold,len(bars)-1)
    return (bars[j].c/e-1)/r-fee_R(r,False)

def exit_B_horizon(bars,sg,H):
    i=sg.idx; r=sg.atrp; e=sg.entry_px; j=i+H
    if j>=len(bars): return None
    return (bars[j].c/e-1)/r-fee_R(r,False)

def exit_C_trail(bars,sg,max_hold,atr_mult=2.0):
    """Chandelier let-run: arm after +0.5R, trail stop atr_mult*ATR below peak,
    -1.0R initial rail. Captures trend, caps give-back. Exit=close on trail hit."""
    i=sg.idx; r=sg.atrp; e=sg.entry_px
    rail=e*(1-r); peak=e; armed=False; trail=rail
    for k in range(i+1,min(i+1+max_hold,len(bars))):
        b=bars[k]
        if b.h>peak: peak=b.h
        gain_r=(peak/e-1)/r
        if gain_r>=0.5: armed=True
        if armed:
            trail=max(trail, peak*(1-atr_mult*r))
        stop=max(rail,trail) if armed else rail
        taker = (b.l<=rail)  # only the hard rail is a taker
        if b.l<=stop:
            px=stop
            return (px/e-1)/r-fee_R(r,taker)
    j=min(i+max_hold,len(bars)-1)
    return (bars[j].c/e-1)/r-fee_R(r,False)

def report(name):
    packs=build(name)
    flush=name=="flush"
    target_r=0.30 if flush else 1.0
    max_hold=6 if flush else 48
    H=6 if flush else 24
    atrps=[sg.atrp for _,sg in packs]
    A=[exit_A_bracket(b,s,target_r,max_hold) for b,s in packs]
    B=[x for x in (exit_B_horizon(b,s,H) for b,s in packs) if x is not None]
    C=[exit_C_trail(b,s,max_hold) for b,s in packs]
    cut=int(len(packs)*0.6)
    A_oos=[exit_A_bracket(b,s,target_r,max_hold) for b,s in packs[cut:]]
    B_oos=[x for x in (exit_B_horizon(b,s,H) for b,s in packs[cut:]) if x is not None]
    C_oos=[exit_C_trail(b,s,max_hold) for b,s in packs[cut:]]
    def stat(L): return {"n":len(L),"mean_net_R":round(st.mean(L),4),"median":round(st.median(L),4),"win%":round(sum(1 for x in L if x>0)/len(L),3)} if L else {}
    print(f"\n##### {name}  (target_r={target_r}, max_hold={max_hold}h, median ATR%={round(st.median(atrps)*100,3)}%)")
    print("  fee-in-R @median ATR%:",round(fee_R(st.median(atrps),False),4),"(maker RT+slip)")
    print("  A LIVE-bracket (.30/1.0 target + -1R rail):", stat(A), " OOS:",stat(A_oos))
    print(f"  B pure-horizon (hold {H}h, exit close):     ", stat(B), " OOS:",stat(B_oos))
    print("  C trail-let-run (arm .5R, 2*ATR chandelier): ", stat(C), " OOS:",stat(C_oos))

if __name__=="__main__":
    for n in ("flush","funding"): report(n)
