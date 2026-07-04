# Output Contract — alpaca_seed.json

DEMO/PAPER. Additive rank-uplift only (flow_not_block) — never a block filter.

## Drop path
`data/intel/alpaca_seed.json` (repo-relative; new `data/intel/` namespace,
mirrors the learner-snapshot Path-constant convention). Overwrite each run.

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
      "catalyst_ts": "<ISO-8601 UTC or null>", // when the catalyst hit/hits
      "evidence": ["https://..."]  // ≥1 source URL, required
    }
  ]
}
```

## thesis_tag allowed values
- `pead_high` — positive earnings surprise near/at 52-week high (strongest).
- `pead` — positive earnings surprise, not near high.
- `analyst_revision` — upgrade / upward estimate-revision breadth.
- `sector_rs` — breakout inside a top relative-strength sector.
- `volume_breakout` — 52wk-high breakout on abnormal high volume.
- `insider_buy` — non-routine opportunistic / cluster insider purchase.
- `short_squeeze` — high short interest WITH a live catalyst (bonus only).
- Crypto/macro `thesis_tag` + `venue` okx/capital + top-level `macro_events[]` →
  `CONTRACT_CRYPTO_MACRO.md`. 🚨 Collect ≠ consume: bot consumes ONLY `venue:
  alpaca` today; okx/capital/`macro_events[]` are collected-only + IGNORED (fail-safe).

## Expiry semantics (fail-safe)
`expiry_ts` in the past → the bot treats the file as absent and seeds nothing.
A stale or missing feed therefore degrades to the bot's normal universe — never
an error, never a stuck seed. Set `expiry_ts` = end of the target US session.

## Ingest status (READ, wired 2026-07-04)
`polaris/core/universe/intel_seed.py` reads this file (fail-safe: schema-
validated, `_sample:true`/expired/absent/malformed all no-op). The Alpaca
universe is fed by `(most-actives ∩ universe) ∪ (curated LIQUID_SEED_SYMBOLS
∩ universe) ∪ (this feed ∩ universe)` — additive union, `_alpaca.py`. A seeded
signal carries the tag in a SEPARATE `seed_tag` field (`RawSignal.seed_tag`,
G3 payload); `equity_52wk_high_breakout` still owns/overwrites `thesis_tag` —
untouched. `positions.seed_tag` (DDL + legacy ALTER) is stamped at open.

## Cohort measurement (Prove-then-Scale) — wired, no consumer yet
`polaris/core/classes/score_f.py::score_f_by_seed_tag` rolls up score_F per
`seed_tag` (join `score_f_events` → `positions.seed_tag`), read-only —
verify: `python3 -c "from polaris.storage.schema import init_db; from
polaris.core.classes.score_f import score_f_by_seed_tag as f; print(f(init_db(
'data/polaris_live.sqlite')))"`. Principle: if a tag's cohort does NOT earn —
its score_F is weak — that feed should be demoted (a future consumer's job).
Aggressive stays intact: the feed just loses influence, the bot never throttles.
