---
type: research
status: recorded
date_created: 2026-07-09
tags: [research, backfilled-frontmatter]
---

# Dashboard Full Audit — 2026-07-09

DEMO/PAPER dashboard (:8770). Read-only synthesis of 6 audit groups (g1 activity/gates/logic · g2 performance/legacy/context · g3 ai/learned/path/build · g4 chart/weekend · g5 mobile · globe). Evidence-based, no code/DB/server touched.

## Verdict table — 12 desktop tabs + mobile + globe

| Surface | Verdict | Evidence (1-line) |
|---|---|---|
| activity | WORKS | grid render, rows 110, "·8" hdr = api open_positions_n 8, real positions, 0 console err |
| gates | WORKS | G1-G8 funnel counts monotone (200 focus…6 sized), 0 err |
| logic | WORKS | textLen 65308, per-strategy UPNL/state real, 0 err |
| performance | WORKS | badges 22/14/8 = strategy/ticker/edge api len, fee-net −$2,374 ≈ api, 0 err |
| legacy | WORKS | isolated mount per 07-07 restructure; minor "SEE BELOW" wording nit |
| context | WORKS | 6 sources real values+freshness, api count 6, 0 err |
| ai | WORKS | in_loop=0 honest, debates 8, probe 40 = /api/ai_activity; label "AI" cosmetic |
| learned | WORKS | 83 lessons verbatim vault, 0 err |
| path | WORKS | 11 phases P0-P6 DONE/BUILD tags, 0 err |
| build | WORKS | tests 2794 green, 60 commits typed chips, 0 err |
| **chart** | **BROKEN** | tab body collapses to 32px; canvases+data exist but invisible; +7-8s backend |
| **weekend** | **BROKEN** | same self-inject layout collapse as chart |
| mobile /m (5 tabs) | WORKS | MAIN/LOGIC/BUILD/ROADMAP/AI all real, equity/PF match api, Desktop▸ link, globe canvas, 0 err |
| globe: 3 galaxies | WORKS | theme RGB exact match okx/capital/alpaca, node counts DB-verified |
| globe: particle flow | WORKS | chainStreams from trade_chains, pnl-colored, frame-hash animates |
| globe: conductor | DEGRADED | drawConductor commented out (intentional) but legend+header stale |
| globe: ring roles | DEGRADED | inner/outer match; mid hosts 8 families not sole "strategy" |
| globe: satellite rings | DEGRADED | nodes orbit live; ring guide lines drawSatRings never called (intentional) |

## Root causes — BROKEN/DEGRADED

- **chart + weekend (BROKEN, shared root):** `chart.js`/`weekend.js` self-inject their pane via `board.appendChild(wrap)` **after** `#board`'s footer is already in DOM. `#board` CSS grid has 6 explicit tracks (`grid-template-rows: auto auto auto auto auto minmax(0,1fr)`); footer claims the 6th `1fr` row, so the injected pane lands in an implicit `auto` row. Combined with `.tab-pane.active{min-height:0;overflow-y:auto}` → body collapses to 32px at any viewport height (verified 1000/3000px unchanged). Silent (0 console err).
- **chart backend (secondary):** `/api/ticker_chart?list=symbols` = 7-8s (3 curl runs) → dropdowns empty ~7s post-switch, no loading indicator. DB `bars` freshness fine (unrelated).
- **globe conductor (DEGRADED):** `globe-core.js:626` `drawConductor(now)` commented out ("Jin: 컨덕터 제거"). Intentional, but `index.html:412` legend "● CONDUCTOR" swatch + `index.html:484` header comment "central AI conductor" are stale → doc/UI mismatch.
- **globe ring roles / satellite rings (DEGRADED = spec-vs-impl drift, not defect):** mid ring hosts 8 families; `drawSatRings` (globe-satellites.js:169) exported but zero call sites (`globe-core.js:613` intentional "위성 궤도선 제거"). Nodes still orbit live. Functionally fine; only literal spec mapping fails.

## Fix priority (Jin impact order)

1. **chart + weekend layout collapse** — one shared fix (self-injected pane must land in the 1fr grow row, e.g. insert before footer or give pane explicit grid placement). Whole tab unusable → highest.
2. **chart /api/ticker_chart?list=symbols 7-8s** — add loading indicator and/or cache symbol list; medium (only bites after #1).
3. **globe conductor legend/header stale** — remove "● CONDUCTOR" swatch (index.html:412) + fix header comment (index.html:484); cosmetic low.
4. **legacy "SEE VIRTUAL $100K X 3 BELOW" wording** — strip points below but header is above; cosmetic low.
5. **ai tab "AI" label / globe spec-doc drift** — cosmetic, optional.

## In-progress vs new (dedup)

**Already in-progress (NOT re-flagged, observed but excluded):** NET ledger mixing (`starting_capital` 230,181.75 carries legacy vs pure 3×$100k), upnl mark label, daily_trades UTC-vs-local count skew (fills 69 vs api 85). All three surfaced across g1/g2/g5 and map to the known NET-ledger/mark-label fix.

**New findings (this audit):**
- N1 (major) chart tab layout collapse to 32px — self-inject-after-footer grid bug.
- N2 (major) weekend tab — same root cause.
- N3 (med) /api/ticker_chart symbol-list 7-8s latency, no loading state.
- N4 (low) globe conductor render disabled but legend+header comment stale.
- N5 (low) legacy tab directional wording nit.
