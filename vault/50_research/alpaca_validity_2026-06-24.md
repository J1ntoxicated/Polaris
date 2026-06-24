---
type: research
status: findings-only
topic: alpaca-universe-validity
date_created: 2026-06-24
verdict: data fix needed — placeholder vol masquerades as liquid; real sources available
related: [[rank_attention_gradient_2026-06-24]], [[layer-0-universe-discovery]], [[feedback_flow_not_block]]
tags: [research, layer-0, alpaca, validity, attention-gradient]
---

# Alpaca Universe Validity — Placeholder Vol vs Rank-Attention Gradient

DEMO/PAPER · read-only (0 code/DB change) · file:line + 측정값 (data/polaris_live.sqlite, venue=alpaca, 2026-06-24).

## 1. Placeholder cause (ROOT)
`_alpaca.py:188-203` `_alpaca_asset_row_to_instrument` stamps EVERY tradable `us_equity` row
(`GET /v2/assets`, which carries NO vol/price) with a class-constant `vol_24h_usd=50_000_000.0`
(`:70`), `spread_bps=2.0` (`:71`), `atr=1.5` (`:72`), `last_price=0` (`:199`). Real vol is
injected ONLY for a bounded candidate set: `_fetch_alpaca_liquidity :206-240` = most-actives
screener (`_MOST_ACTIVES_TOP=100`, `:50`) ∩ universe ∪ curated `LIQUID_EQUITY_SYMBOLS` (47,
`schema.py:330`) → batched `/v2/stocks/snapshots` → real dollar-vol close×volume (`:291-311`).
`_apply_liquidity :324-364` overwrites only those rows; rest keep 50e6. Ceiling = 100+47−overlap
≈ **131**, matches DB exactly. `passes_liquidity_floor :395-426` excludes only KNOWN-bad
(`vol>0 AND vol<floor`); 50e6 > 5M floor (`schema.py:102`) so all pass, and `last_price=0` on
13153 rows disables the $1 price axis. The placeholder slab is structurally un-discriminable —
the gradient "watches" 13151 identical phantom z=0 rows with zero real liquidity signal.

## 2. Real volume IS available (gap = candidate cap, not data)
- `/v2/stocks/snapshots` dailyBar {c,v,h,l} → real dollar-vol + price + ATR proxy; already
  parsed (`:291-311`), N bounded only by symbol list (loops at `_SNAPSHOT_BATCH=100`, `:271`).
- `/v1beta1/screener/stocks/most-actives?by=volume&top=N` — `top=100` today (`:50`), raisable.
- `/v2/stocks/{symbol}/bars` (`adapter.py:296-340`, feed=iex real-time) → close×volume.
- `/v2/assets` row carries `tradable`+`status`+`exchange`, but adapter reads only
  symbol/class/tradable/status (`:178-185`) and **discards exchange**.
All wired; one full snapshot sweep (~133 batched calls of 100) grades every row.

## 3. Distribution (measured)
total=13282 · vol==50e6=13151 · vol>10M=13246 · vol>50M=62 · real(≠50e6,>0)=131 (>50M=62,
10-50M=33, <10M=36) · zero=0. last_price: unknown/0=13153 · sub-$1=12 · [1,5)=20 · ≥$5=97.
is_active=120 → 16 still on placeholder (fake-watch), 104 real. Contrast: OKX 189 (real vol
max $187.7B), Capital 235 (spread-native). Top real: SPY $1398M MU $1153M SPCX $881M NVDA $793M.

## 4. Validity boundary proposal (no build)
- **Data fix needed (yes).** (a) widen enrichment: `_MOST_ACTIVES_TOP 100→~1000` + a `by=trades`
  axis, OR (b) one full snapshot sweep over all ~13.3k tradable per discovery cycle (cheap at
  5-10min cadence) → every row real vol+price+ATR.
- **Make placeholder a SENTINEL**: set un-enriched `vol_24h_usd=0.0` (unknown) not 50e6. Floor
  still treats unknown as non-floored (flow_not_block) BUT the gradient stops seeing 13151
  phantom z=0 "liquid" rows — unknowns rank LAST instead of tying at a fake median.
- **Plumb `primary_exchange`** (already in `/v2/assets` row, `:178`); restrict gradable to
  {NYSE,NASDAQ,ARCA,AMEX/BATS} → drops OTC/pink.
- **Apply EXISTING real floors** once data is real: `min_price=$1` + `min_vol=5M` (`schema.py:102`)
  → 13282 collapses to genuinely liquid/tradable names (hundreds–low-thousands; 62 already >$50M,
  104 real vol>10M). WATCH still sees everything real (WATCH/TRADE decouple `discovery.py:593-624`);
  sub-floor names WATCH, just don't pollute rank. 9-stack untouched (vol = rank input, never a
  sizing multiplier, `_alpaca.py:19`). OKX/Capital paths no-op. GPT=0.
