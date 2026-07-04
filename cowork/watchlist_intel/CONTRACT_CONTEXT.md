# Output Contract — macro context extension (FX · bonds · regime · themes)

DEMO/PAPER. Additive tailwind context only (flow_not_block) — never a block
filter. Extends the same `alpaca_seed.json` file / drop path with three new
top-level blocks + two candidate add-ons.

## 🚨 Collect ≠ consume — mechanism (read first, fail-safe)
The bot's reader `polaris/core/universe/intel_seed.py` applies a code
**allowlist that is default-deny**: only `candidates[]` rows whose `thesis_tag`
is one of the seven equity tags pass (`ALLOWED_THESIS_TAGS`); the reader never
reads `venue`, and the consumer (`_alpaca.py`) additionally intersects with the
Alpaca universe, so okx/capital symbols never enter. Every other top-level field
is off the allowlist, so it is never read. `fx_context` / `bond_context` / `market_regime` /
`candidates[].theme` / the new `thesis_tag` values are therefore COLLECTED
ONLY — present in the file but unread, harmless exactly like an expired feed.
Whether the bot ever consumes them is decided SEPARATELY, after the seed cohort
earns its keep (Prove-then-Scale). Aggressive stays intact: context can only
add a tailwind gain, it never throttles or blocks.

## New top-level blocks (siblings of `candidates[]` / `macro_events[]`)
```json
"fx_context": {
  "dxy_trend": "down",                 // up|down|flat, DX-Y.NYB 5d close-to-close
  "pairs": {"EURUSD": 1.089, "USDJPY": 151.2},
  "rate_expectations": "cuts_priced"   // free label from ZQ=F implied (100−price)
},
"bond_context": {
  "y10_nominal": 4.48,                 // FRED DGS10
  "y10_real": 2.25,                    // FRED DFII10
  "curve_2s10s": 0.35                  // FRED T10Y2Y
},
"market_regime": {
  "risk_state": "neutral",             // risk_on|neutral|risk_off (score verdict)
  "btc_ndx_corr20": 0.41,              // 20-bar Pearson, BTC vs NDX log-returns
  "dxy_gold_corr20": -0.62,            // 20-bar Pearson, DXY vs Gold (context)
  "vix": 16.6,                         // FRED VIXCLS
  "rationale": "VIX 16.6/+0, DXY 5d↓/+1, DFII10 ↑/−1, corr20 0.41 NDX↑/+1 → S=+1"
}
```
Any block absent / malformed → no-op (unread today). `rationale` is mandatory
per INSTRUCTIONS_CONTEXT so the score is always reproducible from cited numbers.

## New `thesis_tag` values (add to CONTRACT.md's list)
- `fx_tailwind` — candidate whose direction agrees with the dollar/regime
  tailwind (context gain, not a size). venue alpaca (or okx/capital, collected).
- `theme_momentum` — candidate inside a top-RS20 theme (e.g. semis/AI) with a
  live newsflow catalyst. Pairs with the optional `candidates[].theme` field.

## New optional `candidates[].theme` field
`"theme": "semiconductors"` — free lower-case label naming the RS-leading theme
(from stockanalysis RS20 + Google News). OPTIONAL; absent = no theme context.
Collected-only, on no allowlist → unread today (fail-safe), pure future context.
