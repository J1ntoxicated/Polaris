---
type: debate
status: active
topic: p0-day1-layer-0-1-review
date_created: 2026-05-06
participants: [claude-opus-4-7, codex-gpt-5-4]
rounds: 1
verdict_initial: REJECT
verdict_after_fix: PENDING
related: [[layer-0-universe-discovery]], [[layer-1-canonical-baseline]], [[ADR-003]]
tags: [debate, codex, p0, layer-0, layer-1, code-review]
---

# P0 Day 1 — External Codex Review

External reviewer: Codex (gpt-5.4). Implementer: Claude Opus 4.7. Spec source: `vault/30_components/layer-0-..1-*.md`.

## Round 1 — Codex verdict: REJECT

Source: `/tmp/polaris_p0_day1_review_response.md` (8186 bytes, gpt-5.4).

### P0 Blockers (4)

1. **4-axis filter is not hard** — `apply_active_filters` skipped vol/depth gates when venue published 0; Capital CFD passed on spread+ATR alone. Tests codified the relaxation. This is the spec's "hard MAX only" contract violated and a defensive softener disguised as venue accommodation.
2. **Capital scoping wrong** — `fetch_capital_instruments` took the first 4 nav nodes by *position*, not by name; child/subchild walked without category whitelist. `capital:AMD` (shares = P2) leaked into the P0 universe.
3. **Listing watchdog mostly not wired** — fetchers always set `listing_ts=None`, `detect_listing_changes` returned diff but never stamped timestamps into state, `FocusSelection` had no field to carry size_mult/max_concurrent.
4. **Layer 1 fallback chain incomplete** — `get_baseline` did `instrument → group → None` only; spec requires `instrument → group → asset_class → 1.0`. Schema also lacked `asset_class` column.

### P1 Suggestions

- Capital epic dedup before filter / focus (multi-node duplication).
- `ratio_to_baseline` accepted negative raw_value (domain `[0, +inf)` violated).
- Hot-path indexes on `universe(is_active, venue)` + `universe(underlying_group_id)` missing.
- SQLite `busy_timeout` not set.

### Spec Drift

- API names: `fetch_*` vs spec `refresh_*`; `score_focus_candidates` vs `score_focus_candidate`; `compute_dynamic_focus` vs `select_focus_watchlist`. `normalize` / `get_baseline` missing `asof_ts`.
- `core` bucket assigned by rank only, not "top quartile cell + active signal" per L0 Q3.
- `Signal` dataclass missing from `data/schema.py`.

## Round 1 — Claude response: ACCEPTED, all P0 blockers fixed

### Fixes applied (commit-ready)

| issue | fix | file |
|---|---|---|
| 4-axis hard | Removed zero-publish relaxation; all 4 axes mandatory | `polaris/core/universe/discovery.py:apply_active_filters` |
| Capital scoping | `CAPITAL_P0_CATEGORY_TOKENS` whitelist by name (forex/currenc/indic/commod/metal/energ/crypto). Shares/ETFs auto-rejected. + epic dedup. | `polaris/core/universe/discovery.py:fetch_capital_instruments` |
| Listing watchdog | `detect_listing_changes(now_ts=)` stamps `listing_ts`; new `merge_listing_timestamps()` carries prev → curr; smoke + tests cover wiring | `polaris/core/universe/discovery.py` |
| Cold-start chain | Added `asset_class` column to `ticker_baseline_state` + index; `get_baseline` 3-step chain (instrument → group → asset_class → None); `normalize/get_baseline` accept `asof_ts` | `polaris/storage/schema.py`, `polaris/core/data/{baseline,normalize}.py` |
| `ratio_to_baseline` negative | Negative raw → `COLD_START_NEUTRAL` (preserve `[0, +inf)` domain) | `polaris/core/data/normalize.py` |
| Hot indexes | `idx_universe_active_venue`, `idx_universe_group`, `idx_baseline_group_metric`, `idx_baseline_class_metric` | `polaris/storage/schema.py` |
| SQLite busy | `PRAGMA busy_timeout=5000` | `polaris/storage/schema.py` |
| Spec API names | Added aliases: `refresh_okx_universe`, `refresh_capital_universe`, `score_focus_candidate`, `select_focus_watchlist`. Existing names kept for ergonomics. | `polaris/core/universe/{discovery,watchlist}.py` |
| `core` bucket semantic | New `_bucket_for` uses cell_q75 AND sig_q75 thresholds across the active universe; rank fallback only on full cold start | `polaris/core/universe/watchlist.py` |
| `Signal` dataclass | Added with `signals` table DDL | `polaris/core/data/schema.py`, `polaris/storage/schema.py` |
| `active_reason` | `persist_universe` writes first-failing axis name on rejected rows | `polaris/core/universe/discovery.py:persist_universe` |
| `CAPITAL_MARKETS_PATH` dead | Removed; replaced with `CAPITAL_P0_CATEGORY_TOKENS` constant | `polaris/core/universe/discovery.py` |

### Tests added (9 new)

- `test_4_axis_filter_is_hard_no_zero_relaxation` (was: relaxation expected)
- `test_merge_listing_timestamps_preserves_prev_and_stamps_new`
- `test_compute_dynamic_focus_listing_watch_via_merged_ts`
- `test_persist_universe_writes_active_reason`
- `test_filter_failure_reason_first_axis`
- `test_capital_name_matches_p0_categories` (Shares/ETFs assert NOT match)
- `test_ratio_to_baseline_negative_raw_neutral`
- `test_normalize_falls_back_to_asset_class`
- `test_get_baseline_asof_ts_filters`
- `test_signal_dataclass_smoke`

### Smoke result (post-fix)

```
OKX     instruments fetched : 182
Capital instruments fetched : 387        ← was 1933 (whitelist applied)
4-axis filter survivors      : 24         ← was 112 (Capital correctly rejected for missing vol/depth)
dynamic focus                : 24 (all listing_watch — first cycle)
top 5: BTC-USDT, TON-USDT, NEAR-USDT, FET-USDT, DOT-USDT (all OKX)
```

Capital being rejected is **correct under spec** at P0; Day 2 must add a real CFD vol/depth proxy (chart endpoint or L2 stream) before CFD re-enters the active universe.

### Test gate

```
pytest: 58 passed
ruff:   clean
mypy --strict: clean
```

## Open Questions / Deferred

- Codex P1 suggestion to enrich Capital with chart-endpoint vol proxy → Day 2 task.
- `asof_ts` is plumbed through `get_baseline` / `normalize`; not yet exercised by L2 gates (Day 2+).
- `select_focus_watchlist` alias takes explicit `target_size`; the dynamic compute path still goes through `compute_dynamic_focus`. Both supported.

## Conclusion

All 4 P0 blockers fixed. P1 suggestions absorbed. Spec drift reconciled. Awaiting Round 2 codex sign-off (recommended next session) or Jin sign-off.
