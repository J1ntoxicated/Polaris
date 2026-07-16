---
type: research
status: recorded
date_created: 2026-06-25
tags: [research, backfilled-frontmatter]
---

# News Sentiment Collector — increment 1 (collect + classify + regime evidence)

2026-06-25 · DEMO/PAPER · SIGNAL/EVIDENCE-only · absorbed into existing alt-data spine (NO new pipeline)

## Scope (this increment)
Collect headlines + async-classify sentiment + emit regime EVIDENCE. **Entry/exit/size WEIGHT
wiring is DEFERRED to /debate** (trading-behaviour change — items ④⑤ of the design). Built byte-
identical to `crypto_fg`/`fred_macro`: collector → cache → fuser scorer → audit snapshot.

## What shipped (file:line)
- NEW `polaris/core/altdata/news_sentiment.py` — `NewsSentimentCollector` (name=`news_sentiment`,
  ttl=900s, asset_classes=crypto/forex/commodity/index/equity). Alpaca News `/v1beta1/news`
  (reuses Alpaca paper creds + ARCHIVE_* fallback; pre-tagged symbols). Per-symbol flat dict
  `{SYM:{sentiment∈[-1,1],relevance,magnitude,n,headline}}`. Keyless/error → `{}`, never raises.
- Async classifier INSIDE `fetch` — gpt-5-mini (P0, `reasoning_effort=minimal`), runtime LLM =
  OpenAI. Deterministic lexicon = keyless graceful fallback (lexicon-alone edge weak ≈ coin-flip,
  literature; GPT is primary). in-loop GPT=0 holds (15min collector cadence, off tick hot path).
- `fuser.py` `_score_news()` + `_SOURCE_WEIGHTS` news entries (crypto/equity 1.1, rest 1.0; clamp
  [0.75,1.25]). Wired in `fuse_evidence` for ALL branches (extracts group's per-symbol row).
  Bull/bear tilt only, scaled by relevance; CONVICTION_FLOOR=1.5 + 2-close gate intact; never
  writes CRISIS; evidence always recorded even below neutral band.
- Registration: `production_paper_loop.py` `_default_altdata_collectors()` + import;
  `cache.py` `_GROUP_SOURCES` 5 prefixes. Audit persist = existing `persist_altdata_snapshot`
  (code change 0 → altdata_snapshot accrues news rows for the dashboard panel).

## Guardrail sweep (all PASS)
- 9-stack: ZERO new sizing multiplier (this increment does not touch sizing).
- flow_not_block: keyless/error → `{}` (price-only regime stands, not a throttle); negative
  sentiment = bear evidence, never block/halt; CRISIS stays price-led.
- in-loop GPT=0: classify on collector cadence; hot path reads cached deterministic numbers.
- runtime-LLM=OpenAI (gpt-5-mini); Anthropic absent from runtime path. Rejection keywords: 0.

## Adversarial review (fresh Claude, builder≠reviewer) → APPROVE-WITH-NITS, 0 blocker
2 MAJOR (both silent-evidence-loss, fail-safe) FIXED before commit:
1. Crypto symbol-key mismatch — Alpaca tags `BTCUSD` but fuser routes `crypto:BTC`. Fix: emit
   raw key + canonical-base alias (`_emit_keys`). E2E verified `crypto:BTC` now hits.
2. GPT string-id echo dropped whole batch — Fix: key by `str(id)` on both classify + aggregate.
NITs fixed: magnitude surfaced to evidence; nan/inf sentinel → neutral no-op (math.isfinite).
NIT noted for /debate: news GPT call ignores `POLARIS_AI_FREE` (defensible — off hot path, but a
background OpenAI spend an AI-free operator may not expect; add a flag if desired).

## Verify
TDD red→green (collector keyless→{}, GPT-error→lexicon fallback, never-raise, fuser branch,
crypto-alias, string-id, registration). 25 new tests pass. mypy --strict + ruff clean. Alt-data
+ production + dashboard regression: 375 pass, 0 regression.

## Deferred (next, /debate-gated)
③ AltDataView.news_sentiment strategy-visible field · ④ exit_tightness fold (tick_exit:262,
tick_mfe:190) · ⑤ T4 size continuous_scalar fold (sizing/engine:383) · ⑥ dashboard news tab.
Edge decides promotion (E2 SHADOW / Behavior-0). FMP free = secondary FX/commodity source TODO.
