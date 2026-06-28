"""Weekend OKX maker OOS edge validation — flush + funding capitulation.

DEMO/PAPER research. Aggressive bias preserved. flow_not_block: VALIDATES whether
the two weekend maker edges survive OOS + real maker fee + real fill rate. Honest:
no edge -> say so; thin sample -> inconclusive. No rejection-keyword logic.

Signal logic matches LIVE exactly:
  flush   = weekend bar, RSI(14)<25 (Wilder), bar.low<bb_lower(20,2)  -> SPOT long
  funding = weekend bar, funding<=own_p10 (no-lookahead) AND funding<0 -> SPOT long
Exit: profit_target_r (flush 0.30R / funding 1.0R) OR -1.0R rail (taker), whichever
first within holding window, else exit at horizon close.

SIGNAL vs EXECUTION separation:
  raw_gross_r = entry predicts favourable move? (fill@touch, fee-free)
  emp_fill    = does a post-only bid at the touch actually fill (later low<=bid)?
  exp_R       = fill_rate * net_R_iffilled  (unfilled = 0 cost, 0 edge)
Fees: OKX real maker 8bps/leg, taker 10bps/leg (rail). Slippage 10/15/20 sweep.
"""
from __future__ import annotations
import math, sqlite3, statistics as st
from dataclasses import dataclass, field
from datetime import UTC, datetime

CANDLES="/Users/jinyoon/Projects/Polaris/data/okx_candles_cache.sqlite"
FUNDING="/Users/jinyoon/Projects/Polaris/data/research_funding.sqlite"
MAKER_BPS=8.0; TAKER_BPS=10.0; RSI_FLUSH=25.0
FLUSH_TARGET_R=0.30; FUND_TARGET_R=1.0; RAIL_R=-1.0; ATR_N=14
WEEKEND=frozenset({5,6})

@dataclass
class Bar: ts:int; o:float; h:float; l:float; c:float; v:float

def load_bars(s):
    db=sqlite3.connect(CANDLES)
    rows=db.execute("SELECT ts,o,h,l,c,v FROM candles WHERE sym=? AND bar='1H' ORDER BY ts",(s,)).fetchall()
    db.close(); return [Bar(*r) for r in rows]

def load_funding(s):
    db=sqlite3.connect(FUNDING)
    rows=db.execute("SELECT ts,rate FROM funding WHERE sym=? ORDER BY ts",(s,)).fetchall()
    db.close(); return rows

def is_weekend(ts): return datetime.fromtimestamp(ts/1000,UTC).weekday() in WEEKEND

def rsi14(c):
    if len(c)<15: return 50.0
    g=ll=0.0
    for i in range(-14,0):
        d=c[i]-c[i-1]
        if d>0: g+=d
        else: ll-=d
    if g+ll<=0: return 50.0
    ag,al=g/14.0,ll/14.0
    if al<=0: return 100.0
    return 100.0-100.0/(1.0+ag/al)

def bb_lower(c,n=20,k=2.0):
    if len(c)<n: return None
    w=c[-n:]; mid=sum(w)/n; sd=math.sqrt(sum((v-mid)**2 for v in w)/n)
    return mid-k*sd

def atr_pct(bars,i,n=ATR_N):
    if i<n: return None
    trs=[]
    for j in range(i-n+1,i+1):
        b,p=bars[j],bars[j-1]
        trs.append(max(b.h-b.l,abs(b.h-p.c),abs(b.l-p.c)))
    atr=sum(trs)/n
    if bars[i].c<=0: return None
    return atr/bars[i].c

def funding_p10_asof(rates,ts):
    hist=[r for t,r in rates if t<ts]
    if len(hist)<10: return None
    vals=sorted(hist); pos=0.10*(len(vals)-1); lo=int(pos); frac=pos-lo
    if lo+1>=len(vals): return float(vals[lo])
    return float(vals[lo]+(vals[lo+1]-vals[lo])*frac)

def funding_asof(rates,ts):
    cur=None
    for t,r in rates:
        if t<=ts: cur=r
        else: break
    return cur

@dataclass
class Signal: sym:str; idx:int; ts:int; entry_px:float; atrp:float

def gen_flush(bars):
    sigs=[]; closes=[b.c for b in bars]
    for i in range(max(24,ATR_N+1),len(bars)):
        if not is_weekend(bars[i].ts): continue
        if rsi14(closes[:i+1])>=RSI_FLUSH: continue
        bl=bb_lower(closes[:i+1])
        if bl is None or bars[i].l>bl: continue
        a=atr_pct(bars,i)
        if a is None or a<=0: continue
        sigs.append(Signal("",i,bars[i].ts,bars[i].c,a))
    return sigs

def gen_funding(bars,rates):
    sigs=[]
    for i in range(max(24,ATR_N+1),len(bars)):
        if not is_weekend(bars[i].ts): continue
        f=funding_asof(rates,bars[i].ts); p10=funding_p10_asof(rates,bars[i].ts)
        if f is None or p10 is None: continue
        if f>=0.0 or f>p10: continue
        a=atr_pct(bars,i)
        if a is None or a<=0: continue
        sigs.append(Signal("",i,bars[i].ts,bars[i].c,a))
    return sigs

@dataclass
class TO: raw:float; net:float; filled:bool; mfe:float; mae:float; hit_t:bool; hit_rail:bool

def simulate(bars,sg,target_r,max_hold,fill_window,slip):
    i=sg.idx; r=sg.atrp
    if r<=0: return None
    entry=sg.entry_px; tpx=entry*(1+target_r*r); rpx=entry*(1+RAIL_R*r)
    filled=False
    for k in range(i+1,min(i+1+fill_window,len(bars))):
        if bars[k].l<=entry: filled=True; break
    mfe=mae=0.0; exit_r=None; ht=hr=False
    for k in range(i+1,min(i+1+max_hold,len(bars))):
        b=bars[k]
        mfe=max(mfe,(b.h/entry-1)/r); mae=min(mae,(b.l/entry-1)/r)
        if b.l<=rpx: exit_r=RAIL_R; hr=True; break
        if b.h>=tpx: exit_r=target_r; ht=True; break
    if exit_r is None:
        j=min(i+max_hold,len(bars)-1); exit_r=(bars[j].c/entry-1)/r
    exit_fee=TAKER_BPS if hr else MAKER_BPS
    net=exit_r-((MAKER_BPS+exit_fee+2*slip)/1e4)/r
    return TO(exit_r,net,filled,mfe,mae,ht,hr)

@dataclass
class Agg:
    n:int=0; raw:list=field(default_factory=list); net:list=field(default_factory=list)
    filled:int=0; mfe:list=field(default_factory=list); ht:int=0; hr:int=0

def summ(label,a,fr_over=None):
    if a.n==0: return {"label":label,"n":0}
    emp=a.filled/a.n; fr=fr_over if fr_over is not None else emp
    mnf=st.mean(a.net) if a.net else 0.0
    wins=sum(1 for x in a.net if x>0)
    return {"label":label,"n":a.n,"raw_gross_r_mean":round(st.mean(a.raw),4),
        "raw_gross_r_median":round(st.median(a.raw),4),"signal_win_rate":round(wins/a.n,3),
        "emp_fill_rate":round(emp,3),"net_R_iffilled":round(mnf,4),
        "mfe_r_median":round(st.median(a.mfe),4),
        "mfe_minus_realized_gap":round(st.median(a.mfe)-st.median(a.raw),4),
        "hit_target_rate":round(a.ht/a.n,3),"hit_rail_rate":round(a.hr/a.n,3),
        "exp_R_per_signal":round(fr*mnf,5)}

def agg_for(subset,target_r,max_hold,fill_window,slip):
    a=Agg()
    for bars,sg in subset:
        o=simulate(bars,sg,target_r,max_hold,fill_window,slip)
        if o is None: continue
        a.n+=1; a.raw.append(o.raw); a.net.append(o.net); a.mfe.append(o.mfe)
        if o.filled: a.filled+=1
        if o.hit_t: a.ht+=1
        if o.hit_rail: a.hr+=1
    return a

def run_strategy(name,slip=15.0):
    db=sqlite3.connect(CANDLES)
    syms=[r[0] for r in db.execute("SELECT DISTINCT sym FROM candles WHERE bar='1H'").fetchall()]
    db.close()
    flush=name=="flush"
    target_r=FLUSH_TARGET_R if flush else FUND_TARGET_R
    max_hold=6 if flush else 48; fill_window=3 if flush else 6
    all_sigs=[]
    for s in syms:
        bars=load_bars(s)
        if len(bars)<60: continue
        if flush: sigs=gen_flush(bars)
        else:
            rates=load_funding(s)
            if not rates: continue
            sigs=gen_funding(bars,rates)
        for sg in sigs: sg.sym=s; all_sigs.append((bars,sg))
    all_sigs.sort(key=lambda x:x[1].ts)
    A=lambda sub:agg_for(sub,target_r,max_hold,fill_window,slip)
    full=A(all_sigs)
    cut=int(len(all_sigs)*0.6)
    isA=A(all_sigs[:cut]); oosA=A(all_sigs[cut:])
    K=6; blocks=[all_sigs[j::K] for j in range(K)]
    bnet=[st.mean(A(b).net) for b in blocks if A(b).net]
    pos=sum(1 for x in bnet if x>0)
    return {"strategy":name,"slip_bps":slip,"FULL":summ("full",full),
        "IS_60":summ("in_sample",isA),"OOS_40":summ("oos",oosA),
        "block_net_means":[round(x,4) for x in bnet],
        "blocks_net_positive":f"{pos}/{len(bnet)}","_full":full}

def fill_sweep(full):
    out=[]
    for fr,lab in ((0.008,"live_0.8%"),(0.06,"maxlev_6%"),(None,"empirical")):
        s=summ(lab,full,fr_over=fr)
        out.append({"scenario":lab,"exp_R_per_signal":s.get("exp_R_per_signal"),
            "net_R_iffilled":s.get("net_R_iffilled"),"emp_fill_rate":s.get("emp_fill_rate")})
    return out

if __name__=="__main__":
    import json
    for name in ("flush","funding"):
        print("="*72)
        for slip in (10.0,15.0,20.0):
            res=run_strategy(name,slip)
            full=res.pop("_full")
            print(f"\n### {name} | slippage={slip}bps")
            print(json.dumps(res,indent=1))
            if slip==15.0:
                print("FILL-RATE SWEEP (signal vs execution):")
                print(json.dumps(fill_sweep(full),indent=1))


# ---- ADDENDUM: pure signal-edge (unbracketed forward return) + realistic fill ----
# The bracketed sim above mixes signal with exit policy. To isolate the SIGNAL
# edge cleanly we also measure the UNBRACKETED forward return at fixed horizons
# (no target, no rail) — "does the entry predict a favourable forward move?" —
# net of one maker round trip. And a realistic post-only fill model: a bid posted
# 1 tick below the touch (passive) fills only if the NEXT bar trades strictly
# below the touch; deeper bids fill less often.

def pure_forward(name, horizons=(3,6,12,24)):
    db=sqlite3.connect(CANDLES)
    syms=[r[0] for r in db.execute("SELECT DISTINCT sym FROM candles WHERE bar='1H'").fetchall()]
    db.close()
    flush=name=="flush"
    rows=[]
    sig_packs=[]
    for s in syms:
        bars=load_bars(s)
        if len(bars)<60: continue
        if flush: sigs=gen_flush(bars)
        else:
            rates=load_funding(s)
            if not rates: continue
            sigs=gen_funding(bars,rates)
        for sg in sigs: sg.sym=s; sig_packs.append((bars,sg))
    sig_packs.sort(key=lambda x:x[1].ts)
    cut=int(len(sig_packs)*0.6)
    def fwd_stats(subset,H):
        rs=[]
        for bars,sg in subset:
            i=sg.idx; j=i+H
            if j>=len(bars): continue
            r=(bars[j].c/sg.entry_px-1)/sg.atrp  # forward return in R, no bracket
            rs.append(r)
        if not rs: return None
        net=[x-(2*MAKER_BPS+2*15.0)/1e4/0.01 for x in rs]  # rough; recompute below
        return rs
    out={}
    for H in horizons:
        all_r=fwd_stats(sig_packs,H)
        oos_r=fwd_stats(sig_packs[cut:],H)
        if not all_r: continue
        # net per-trade R subtracts maker RT fee in R using each trade's own atrp
        def net_list(subset):
            nl=[]
            for bars,sg in subset:
                i=sg.idx; j=i+H
                if j>=len(bars): continue
                gr=(bars[j].c/sg.entry_px-1)/sg.atrp
                feeR=((2*MAKER_BPS+2*15.0)/1e4)/sg.atrp
                nl.append(gr-feeR)
            return nl
        allnet=net_list(sig_packs); oosnet=net_list(sig_packs[cut:])
        out[f"H{H}"]={
            "n":len(all_r),
            "raw_fwd_R_mean":round(st.mean(all_r),4),
            "raw_fwd_R_median":round(st.median(all_r),4),
            "win_rate":round(sum(1 for x in all_r if x>0)/len(all_r),3),
            "net_R_mean_iffilled":round(st.mean(allnet),4),
            "OOS_raw_fwd_R_mean":round(st.mean(oos_r),4) if oos_r else None,
            "OOS_net_R_mean":round(st.mean(oosnet),4) if oosnet else None,
        }
    return out

def realistic_fill(name, depth_bps_list=(0,5,10,20)):
    """Post-only bid posted depth_bps BELOW touch; fills if a later bar low<=bid
    within the resting window. Returns fill rate per depth (the EXECUTION axis)."""
    db=sqlite3.connect(CANDLES)
    syms=[r[0] for r in db.execute("SELECT DISTINCT sym FROM candles WHERE bar='1H'").fetchall()]
    db.close()
    flush=name=="flush"; win=3 if flush else 6
    packs=[]
    for s in syms:
        bars=load_bars(s)
        if len(bars)<60: continue
        if flush: sigs=gen_flush(bars)
        else:
            rates=load_funding(s)
            if not rates: continue
            sigs=gen_funding(bars,rates)
        for sg in sigs: sg.sym=s; packs.append((bars,sg))
    out={}
    for d in depth_bps_list:
        filled=tot=0
        for bars,sg in packs:
            bid=sg.entry_px*(1-d/1e4); i=sg.idx; tot+=1
            for k in range(i+1,min(i+1+win,len(bars))):
                if bars[k].l<=bid: filled+=1; break
        out[f"depth_{d}bps"]=round(filled/tot,3) if tot else None
    return out

if __name__=="__main__":
    import json
    print("\n"+"#"*72+"\n# ADDENDUM: pure unbracketed signal edge + realistic fill\n"+"#"*72)
    for name in ("flush","funding"):
        print(f"\n=== {name}: PURE FORWARD-RETURN signal edge (net 1 maker RT @15bps slip) ===")
        print(json.dumps(pure_forward(name),indent=1))
        print(f"=== {name}: realistic post-only FILL RATE by bid depth ===")
        print(json.dumps(realistic_fill(name),indent=1))
