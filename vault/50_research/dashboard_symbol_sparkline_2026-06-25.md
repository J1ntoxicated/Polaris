---
type: digest
status: active
date_created: 2026-06-25
tags: [dashboard, display-only, bars, sparkline, demo-paper]
---

# Symbol sparkline — per-row recent-close mini graph — 2026-06-25

Jin: a tiny inline trend graph next to each symbol on the dashboard rows
(positions / recent_trades / ticker_stats). DEMO/PAPER. **Display-only — 0
behavior change**; never feeds sizing/gating/exit/order. flow_not_block intact.

## What

- **Backend** (`polaris/scripts/dashboard/`): new `_spark_series(conn,
  instruments, n=30)` in `snapshot_q_common.py` — ONE bars query
  (`ROW_NUMBER() OVER (PARTITION BY instrument_id, bar_interval)` bounded to the
  newest 60/partition) over the displayed instruments, picks the FRESHEST
  interval per instrument (so a stale-daily + fresh-intraday symbol sparks the
  fresh series, never mixed), excludes FUTURE-dated bars (`ts <= now`, the +10h
  Capital AEST bug), downsamples to N keeping first+last anchors. A `spark:
  list[float]` field is embedded on `PositionRow` / `TickerStat` / `ClosedTrade`
  by `_attach_sparks()` in `collect_snapshot`. Graceful empty `[]` when the bars
  cache (Yahoo-primary) has nothing. No per-row fetch.
- **Frontend** (`tools/visualizer/static/`): `sparkline(arr)` in `board.js`
  builds a ~62×16 inline SVG polyline, green when last≥first else red, `''` for
  <2 points. Injected next to the symbol cell in `board_tabs.js` (positions +
  trades) and `board_tabs_ext.js` (ticker). Minimal-footprint additive cell.

## Verify

- TDD: `tests/test_dashboard_sparkline.py` (8) — presence/order, empty/missing
  graceful, downsample, freshest-interval pick, future-bar exclusion, multi-
  instrument single-query, end-to-end `collect_snapshot` attach + asdict
  serialize. All green; 47 dashboard tests pass.
- `mypy --strict` + `ruff` clean; `node --check` clean on the 3 JS files.
- Live DB (`polaris_live.sqlite`): all 11 ticker + 40 trade rows get a 30-pt
  series; `dataclasses.asdict` carries the `spark` key for `/api/snapshot`.
- Fresh adversarial sub-agent review: APPROVE (one P1 LOC-cap fix applied —
  attach loop extracted to `_attach_sparks`, snapshot.py back to 500 LOC).
- Rejection-keyword sweep: 0 hits.

## Anti-pattern guard

- One query, not per-row (no REST/DB storm; 60s display poll).
- Future-dated + freshest-interval guards mirror `_last_prices` (no stale/mixed
  canvas). `_safe_query` swallows errors → empty spark, never crashes snapshot.
