# Output Contract — alpaca_seed.json

DEMO/PAPER. Additive rank-uplift only (flow_not_block) — never a block filter.

## Drop path
`data/intel/alpaca_seed.json` (repo-relative). Overwrite each run.

## JSON schema
```json
{
  "generated_at": "<ISO-8601 UTC>",
  "expiry_ts": "<ISO-8601 UTC — after this the bot ignores the whole file>",
  "candidates": [
    {
      "symbol": "AAPL",            // exact Alpaca ticker (verified real)
      "venue": "alpaca",           // constant for this feed
      "thesis_tag": "pead_high",   // from allowed list below
      "score": 0.0,                // [0,1] rank-uplift, per INSTRUCTIONS rubric
      "close": 0.0,                // last close (REQ) — self-report entry geometry
      "high_52wk": 0.0,            // max(high, prior 252 bars) (REQ; or dist_to_trigger_pct)
      "dist_to_trigger_pct": 0.0,  // 100*(close/(0.99*high_52wk)-1); ≥0 = at/through trigger
      "catalyst_ts": "<ISO-8601 UTC or null>", // when the catalyst hit/hits
      "catalyst_age_days": 0,      // days since catalyst_ts (null if no catalyst_ts)
      "drift_consumed": false,     // true = initial pop already faded (window late)
      "evidence": ["https://...", "https://..."] // ≥2 live sources (see rule below)
    }
  ]
}
```
Entry geometry (`equity_52wk_high_breakout`): emit when `close >= 0.99*high_52wk`
AND `close > SMA200`. `close`+`high_52wk` (or `dist_to_trigger_pct`) self-report
distance to trigger — far-below candidates stay in the feed, only their rank
decays (INSTRUCTIONS proximity term = uplift decay, not a block).

## thesis_tag allowed values
- `pead_high` — positive earnings surprise near/at 52-week high (strongest).
- `pead` — positive earnings surprise, not near high.
- `analyst_revision` — upgrade / upward estimate-revision breadth.
- `sector_rs` — breakout inside a top relative-strength sector.
- `volume_breakout` — 52wk-high breakout on abnormal high volume.
- `insider_buy` — non-routine opportunistic / cluster insider purchase.
- `short_squeeze` — high short interest WITH a live catalyst (bonus only).
- Crypto/macro `thesis_tag` + `venue` okx/capital + `macro_events[]` →
  `CONTRACT_CRYPTO_MACRO.md`. 🚨 Collect ≠ consume: bot reads ONLY `venue:alpaca`; okx/capital/`macro_events[]` are collected + IGNORED (fail-safe).

## Evidence rule (mandatory, strengthened)
- ≥2 INDEPENDENT live sources/candidate (not 2 links to the same page). Prefer
  primary: SEC (8-K/filings), BLS/BEA/gov, or a wire (Reuters/AP/PR/BW).
- Every URL resolves NOW — no 404/403/dead. Won't load → drop it; <2 left →
  drop the candidate. Single-source/dead-link rows are malformed → reader skips.

## Expiry semantics (fail-safe)
`expiry_ts` in the past → bot treats the file as absent, seeds nothing (degrades
to the normal universe — never an error). Set = end of target US session.

## Backward-compat (extra fields are safe)
The reader (`intel_seed.py`) validates only `symbol`, `venue`, `thesis_tag`,
`score`, `evidence`, `expiry_ts` — it IGNORES every other key, so ALL the new
self-report fields above add telemetry with ZERO ingest-behavior change
(hardcompat intact). Reader detail → `CONTRACT_INGEST.md`.
