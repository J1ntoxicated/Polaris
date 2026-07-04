# Ingest side — reader reference (split from CONTRACT.md)

DEMO/PAPER. How the bot consumes `alpaca_seed.json`. Reference only — the
authoring contract is `CONTRACT.md`. Additive rank-uplift, never a block.

## Ingest status (READ, wired 2026-07-04)
`polaris/core/universe/intel_seed.py` reads the feed (fail-safe: schema-
validated, `_sample:true`/expired/absent/malformed all no-op). The Alpaca
universe is fed by `(most-actives ∩ universe) ∪ (curated LIQUID_SEED_SYMBOLS
∩ universe) ∪ (this feed ∩ universe)` — additive union, `_alpaca.py`. A seeded
signal carries the tag in a SEPARATE `seed_tag` field (`RawSignal.seed_tag`,
G3 payload); `equity_52wk_high_breakout` still owns/overwrites `thesis_tag` —
untouched. `positions.seed_tag` (DDL + legacy ALTER) is stamped at open.

## Extra fields are ignored (backward-compat)
The reader validates only `symbol`, `venue`, `thesis_tag`, `score`, `evidence`,
and top-level `expiry_ts`. Every other key — `close`, `high_52wk`,
`dist_to_trigger_pct`, `catalyst_age_days`, `drift_consumed`, and the crypto
self-tag fields — is UNREAD. New self-reporting fields therefore cannot change
ingest behavior (hardcompat). Proximity ranking is an AUTHORING concern
(INSTRUCTIONS rubric), applied before the feed is written — not in the reader.

## Cohort measurement (Prove-then-Scale) — wired, no consumer yet
`polaris/core/classes/score_f.py::score_f_by_seed_tag` rolls up score_F per
`seed_tag` (join `score_f_events` → `positions.seed_tag`), read-only —
verify: `python3 -c "from polaris.storage.schema import init_db; from
polaris.core.classes.score_f import score_f_by_seed_tag as f; print(f(init_db(
'data/polaris_live.sqlite')))"`. Principle: if a tag's cohort does NOT earn —
its score_F is weak — that feed should be demoted (a future consumer's job).
Aggressive stays intact: the feed just loses influence, the bot never throttles.
