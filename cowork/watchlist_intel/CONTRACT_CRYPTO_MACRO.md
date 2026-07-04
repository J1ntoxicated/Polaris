# Output Contract — crypto / macro extension (split from CONTRACT.md)

DEMO/PAPER. Additive rank-uplift + session context only (flow_not_block) —
never a block filter. Extends the same `alpaca_seed.json` file / drop path.

## 🚨 Collect ≠ consume (fail-safe, read first)
Today the bot consumes ONLY `venue: alpaca` candidates. `okx` / `capital`
candidates and the `macro_events[]` array are COLLECTED ONLY — no consumer is
wired. Whether the bot ever ingests them is decided SEPARATELY, after the seed
cohort earns its keep (Prove-then-Scale, see CONTRACT.md cohort section). Until
then the bot simply IGNORES these fields — present in the file but unread, so
they are harmless (fail-safe, exactly like an expired feed). Aggressive stays
intact: extra data can only add context/uplift, it never throttles or blocks.

## `candidates[].venue` allowed values
- `alpaca` — US equity (the only value the bot consumes today).
- `okx` — OKX SPOT crypto (24/7). Symbol notation `BASE-QUOTE`, e.g. `BTC-USDT`.
- `capital` — Capital CFD (macro/commodity/index). Symbol notation bare, e.g. `GOLD`.

## New `thesis_tag` values (add to CONTRACT.md's list)
- `token_unlock` — token unlock within 7d, large %MCap (CryptoRank). venue okx.
- `listing` — new listing / delisting announcement (OKX API). venue okx.
- `etf_flow` — BTC/ETH ETF daily net-flow context (Bitbo). venue okx.
- `crypto_catalyst` — crypto news catalyst headline (Cointelegraph/CoinDesk). venue okx.
- `macro_event_window` — inside a High-impact econ-event window (ForexFactory). venue capital.
- `commodity_event` — OPEC / oil-inventory / commodity / index-rebalance event. venue capital.

## Top-level `macro_events[]` (new, sibling of `candidates[]`)
Session-context array — dated events, NOT tradeable candidates. Same fail-safe
(unread today). Each element:
```json
{
  "date_utc": "2026-07-04T12:30:00Z",   // ISO-8601 UTC (FF ET → UTC converted)
  "event": "US Non-Farm Payrolls",       // human label from the source
  "affected_symbols": ["GOLD", "US100"]  // Capital/OKX symbols this may move; [] ok
}
```
`macro_events[]` absent / empty / expired → no-op. It never gates anything; it
is pure context the bot may (later) attach to a CFD session, never a block.
