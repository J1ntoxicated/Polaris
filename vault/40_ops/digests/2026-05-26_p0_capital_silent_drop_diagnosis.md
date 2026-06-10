---
type: digest
status: active
date: 2026-05-26
date_created: 2026-05-26
related: [[2026-05-08_p1_day9_24h_audit_detail]], [[layer-1-canonical-baseline]]
tags: [diagnosis, capital, fills, silent-drop, p0, debunk]
---

# Day 10 P0 "Capital fills 0 silent drop" — Diagnosis (debunked + real bug found)

## TL;DR
- **Audit claim "0 capital fills post ts=1778190207"** = false positive (ts_ms range filter caught the drifted ts, not the absence of rows).
- **Real bug**: `fills.ts_ms` for `venue='capital'` is **-36000s (-10h) drift** vs `positions.opened_ts` (Sydney AEST UTC+10 offset). OKX control: +30s drift (normal).
- **Current code reproduces 0 drift** when invoked fresh — historical data carries the bug. Likely fixed pre-2026-05-26 (codex Day 5 R1 "Capital ts naive→UTC" P1 fix per [[_NOW]]).
- **Action**: verify with next 24h paper run; if new capital fills are 0-drift, mark resolved; otherwise hunt the remaining naive-ts path.

## Evidence
SQLite query against `data/polaris.sqlite` (snapshot 2026-05-20 17:35):

| Metric | Value |
|---|---|
| `fills` table capital total | 166 (80 closes) |
| `fills` table capital post-threshold (`ts_ms > 1778190207000`) | **0** |
| `positions` table capital post `opened_ts > 1778190207` | 85 (79 closed + 6 open) |
| `allocator_reservations` capital confirmed post-threshold | 55 |
| `strategy_fault_events` post-threshold | 0 |
| capital fill `contribution_id` → `position.position_id` JOIN | **165 matched rows** |

**JOIN sample** (latest 5 capital positions):
```
pos.opened=1778225394 fill.ts_s=1778189423 drift=-35971s (-10.0h) is_close=0
pos.opened=1778225394 fill.ts_s=1778190200 drift=-35194s ( -9.8h) is_close=1
pos.opened=1778225341 fill.ts_s=1778189369 drift=-35972s (-10.0h) is_close=0
...
```

**OKX control** (same query, different venue):
```
pos.opened=1778402392 fill.ts_s=1778402427 drift=+35s
pos.opened=1778402355 fill.ts_s=1778402386 drift=+31s
```

## Root cause path (most likely)
`polaris/scripts/_smoke_fills.py:_capital_fill_payload` emits
`"date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())` (UTC).
`polaris/core/data/fill_normalizer.py:_capital_ts_ms` parses with `fromisoformat`
+ `replace(tzinfo=UTC)` + `timestamp()` — **today reproduces 0 drift**
(verified live: -903 ms = clock skew only).

Historical fills predate the codex Day 5 P1 fix and carry the naive-ts artefact.

## What the audit detail digest got wrong
[[2026-05-08_p1_day9_24h_audit_detail]] line 40:
> **fills row NEVER appears for ts>1778190207 venue=capital**

Filter was on `ts_ms > threshold` but `ts_ms` itself was shifted -10h, so the
rows appeared as being from the *past* of the threshold, not after it.
`strategy_fault_events=0` is real (no exceptions thrown — the pipeline path
worked end-to-end). The "Prime suspect" line about `persist_fill` swallowing
or missing `capital:` prefix is **refuted** — `instrument_id='capital:US100'`
is correctly stored (verified).

## Recommended fix (P0)
1. Run next 24h paper loop with current main (post 2026-05-26 commit `c3181fd`).
2. Query the resulting capital fills' `ts_ms` drift vs `positions.opened_ts`:
   - **0 drift** → confirmed fixed in current code; close P0.
   - **non-zero drift** → trace remaining naive-ts path (suspect
     `_smoke_real_roundtrip.py` or another fill emitter). Add explicit
     `assert dt.tzinfo is not None` guard in `_capital_ts_ms`.
3. Backfill correction for historical capital fills (optional, low impact —
     dashboards / forensic queries only).

## Day 10 P1 unchanged (separate items)
- `fx_breakout_basket` all-time 0 signals — strategy logic, not fills.
- `xau_indices_trend` US100 ticker mismatch — universe / symbol resolution.
- G3 KILL ratio 73% — validator gate threshold (B-variant).

These are unaffected by the Capital diagnosis; queue separately.
