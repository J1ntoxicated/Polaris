# Macro Context Intel — collection (FX · bonds · regime · themes)

DEMO/PAPER (virtual funds). Same law: this feed only ADDS context / RAISES
rank in the tailwind direction — never a block/skip/reject (flow_not_block).
Four axes populate the top-level `fx_context` / `bond_context` /
`market_regime` blocks + `candidates[].theme` — all collected-only today.

## Axes → collect + judge (verified free / no-key sources only)
1. FX / DXY — dollar direction:
   - Primary DXY: Yahoo chart `query1.finance.yahoo.com/v8/finance/chart/
     DX-Y.NYB?range=5d&interval=1d` (UA header; same-day ICE close).
   - Confirm/lag: FRED `fredgraph.csv?id=DTWEXBGS` (broad $, T+4 lag; one id
     per call — comma-joined ids return a ZIP).
   - Pairs: Yahoo `EURUSD=X` / `USDJPY=X` (same endpoint) → `fx_context.pairs`.
   - Rate expectations: Yahoo `ZQ=F` fed-funds future, implied = 100 − price
     (CME FedWatch page is bot-blocked, HTTP 000 — do NOT fetch it).
2. Bonds — FRED CSV (no-auth), T+1~2 lag: `DGS10` (10y nominal) · `DFII10`
   (10y real) · `T10Y2Y` (2s10s curve) → `bond_context`. Real-yield fall =
   risk tailwind; curve steepening = context note.
3. Regime — inputs: FRED `VIXCLS` (vol) · `SP500` · `NASDAQ100` (daily, for
   corr20) · OKX `us.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1D&
   limit=60` (public, no-auth; backup CoinGecko market_chart). Compute the
   regime score (formula below) → `market_regime`.
4. Themes — relative strength + newsflow (DEEP scan is every-other-day /
   weekly, NOT daily — see routine): stockanalysis `api/symbol/e/{SMH|SOXX|
   AIQ|XLK}/history?range=3M&period=Daily` (UA header) → RS20 = ETF 20d
   return − XLK 20d return; Google News RSS `news.google.com/rss/search?q=
   semiconductor+AI+chips` for the catalyst headline → `candidates[].theme`.

## Regime score (reproducible; 1-line rationale MANDATORY)
S = s_vix + s_dxy + s_real + s_corr, each ∈ {−1,0,+1}, so S ∈ [−4,+4].
- s_vix: VIX<16 → +1 · 16–24 → 0 · >24 → −1.
- s_dxy: DX-Y.NYB 5-day change down → +1 · up → −1 (close-to-close).
- s_real: DFII10 5-day change down → +1 · up → −1.
- s_corr: corr20 = 20-bar Pearson of BTC & NDX daily log-returns. ≥0.3 & NDX
  5d↑ → +1 · ≥0.3 & NDX 5d↓ → −1 · <0.3 (decoupled) → 0.
- Verdict: S≥+2 → `risk_on` · S≤−2 → `risk_off` · else `neutral`.
- Usage (tailwind gain only, flow_not_block): `risk_on` gives long-direction
  signals a context gain; `risk_off` gives the same gain to short / counter-
  trend signals (direction re-assignment, never a cut); `neutral` = no gain.
- Missing axis = score it 0, so S always computes. `rationale` = 1 line citing
  the numbers, e.g. "VIX 16.6/+0, DXY 5d↓/+1, DFII10 ↑/−1, corr20 0.41 NDX↑/+1 → S=+1 neutral".

## Daily +10 min routine (append; 07:20 Sydney, ~2 min single script)
1. FRED ×7 — DTWEXBGS·DGS10·DFII10·T10Y2Y·VIXCLS·SP500·NASDAQ100, one CSV each.
2. Yahoo chart ×5 — DX-Y.NYB·EURUSD=X·USDJPY=X·^NDX·ZQ=F (implied=100−price).
3. OKX BTC-USDT 1D limit=60 · 4. Google News RSS ×2 (FedWatch · AI-chips) top 5.
5. corr20 + regime score → `market_regime` block; write file + vault 1-liner.
- THEME deep scan (stockanalysis SMH/SOXX/AIQ/XLK 3M → RS20) = every-other-day
  or weekly, NOT part of the daily 2-min core (keeps daily cost flat).
- Failed axis → per-axis backup (FRED lag ↔ Yahoo live, OKX ↔ CoinGecko);
  regime always emits (missing = 0). Fetch-blocked: CME FedWatch · stooq (PoW).
