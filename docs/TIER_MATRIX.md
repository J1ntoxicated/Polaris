# Tier Matrix — Plan v2.2 Pillar 3 Write Authority

3-Tier process separation preparation. MVP: module boundary + write linter
only. Actual process split (start.sh supervisor + 3 processes) is deferred.

## Tiers

### Tier 1 — `invasion-ingest`
- Modules: `invasion.ingest`, `invasion.exchange`, `invasion.data.collectors`
- Write tables:
  - `market_ticks`
  - `market_candles_1m`
  - `market_candles_1h`
  - `provider_raw`
  - `feature_cache`
  - `ticker_baseline`

### Tier 2 — `invasion-trade`
- Modules: `invasion.trade`, `invasion.signals`, `invasion.strategy`
- Write tables:
  - `trades`
  - `signals`
  - `trade_events`
  - `signal_queue`
  - `signal_blocks`
  - `position_health`
  - `loss_attribution`

### Tier 3 — `invasion-learn`
- Modules: `invasion.ticks`, `invasion.evolution`, `invasion.ops`
- Write tables:
  - `cell_matrix`
  - `strategy_cell_matrix`
  - `ai_event_audits`
  - `lag_kpi_hourly`

## Linter

`invasion/ingest/_write_lint.py` scans INSERT/UPDATE/DELETE statements
and reports modules writing outside their tier's allowed set.

```bash
python3 -m invasion.ingest._write_lint invasion
```

Tests: `tests/ingest/test_tier_write_lint.py` (smoke only, report not strict).

## Roadmap

- MVP (D17): module boundary + linter report.
- Next: expand matrix per audit, enforce strict 0 violations for Tier 1.
- Future: start.sh supervisor + 3-process split with IPC bus.

## Entry point

`python3 -m invasion.ingest --standalone` — placeholder, currently
in-process.
