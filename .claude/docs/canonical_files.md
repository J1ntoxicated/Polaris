# Canonical File Map

> **Audit (2026-04-26 INSIGHT-003)**: 38 path fix 후 6 잔여 = 의도된 placeholder/deprecated.
> - `_pipeline_scan_ai/_gate/_preprocess.py` (line 28): 3-file shorthand notation
> - `canonical_cell_matrix.md` (line 33): same-dir relative
> - `invasion/dashboard/sections/alert_panel.py` (line 35): deprecated 2026-04-25
> - `invasion/signals/_signal_blocks.py` (line 44): planned T13 D14
> - `invasion/_metric_contract.py` (line 46): planned T13 D16a
> All marked with explicit status — vault canonical 정합 ✓.


verify with grep — paths may change. **Cell Matrix SSOT**: `strategy_cell_matrix` 가 모든 trade decision 결정 → [canonical_cell_matrix.md](canonical_cell_matrix.md) (Phase 2~5 schema 확장 + plan link).

| Feature | Canonical |
|---------|-----------|
| OKX Public API | `invasion/exchange/okx/public.py` |
| Events/Logging | `invasion/utils/events.py` |
| Market Hours | `invasion/utils/market_hours.py` |
| Technicals | `invasion/utils/technicals.py` |
| Calendar | `invasion/utils/calendar_blackout.py` |
| Collectors | `invasion/data/collectors/*.py` (cnn_feargreed, coinglass, fred_macro, yfinance_macro, etc.) |
| Asset Groups | `invasion/utils/groups.py` |
| Alpaca Adapter | `invasion/exchange/alpaca/client.py` |
| Gate Matrix | `invasion/trade/gate_matrix.py` |
| Asset Themes | `invasion/config/themes.py` |
| Computed Params | `invasion/config/computed.py` |
| Canonical Names | `invasion/config/canonical_names.py` |
| Exchange Errors | `invasion/exchange/errors.py` |
| Emergency Flatten | `invasion/ops/emergency.py` |
| Candle Cache | `invasion/data/candle_cache.py` |
| Strategy Evolution | `invasion/strategy/evolver.py` |
| Dashboard Ops | `invasion/dashboard/operations.py` |
| Dashboard Intel | `invasion/dashboard/intel.py` |
| Flow Providers | `invasion/signals/providers_extended.py` (OrderFlowImbalance, VWAPMeanReversion) |
| On-chain Providers | `invasion/signals/providers_onchain.py` (OnChainValuation, BasisSpread, LiqCascade, GoogleTrends, LLMSentiment) |
| Google Trends | `invasion/data/collectors/google_trends.py` |
| Trade Pipeline (scan) | `invasion/trade/_pipeline_scan.py` (+ `_pipeline_scan_ai/_gate/_preprocess.py`) |
| Trade Pipeline (sizing) | `invasion/trade/_pipeline_sizing.py` |
| Trade Pipeline (regime) | `invasion/trade/_pipeline_regime.py` |
| Close Handler | `invasion/trade/close_handler.py` |
| Exit FSM | `invasion/trade/exit_fsm.py` (+ `exit_cycle.py`) |
| Strategy Cell Matrix (Decision SSOT, 8-dim) | `invasion/strategy/cell_matrix.py` — see `canonical_cell_matrix.md` |
| Ticker Learner | `invasion/ops/ticker_learner.py` |
| Alert Monitor | `invasion/ops/harness_alerter.py` (emit) + `invasion/dashboard/sections/alert_panel.py` (deprecated 2026-04-25) |
| Live Config | `data/live_config.json` (ops-executor SSOT) |
| External Providers | `invasion/signals/providers_external.py` (16 classes registered via wiring_signals) |
| Position Utils | `invasion/trade/position.py` (결함 8 duplicate open rule, `*:337` alpaca fallback) |
| Hourly Stats (learner) | `invasion/ticks/hourly_stats.py` (E14 profit_target unit bug site `*:655`) |
| Param Validator | `invasion/ticks/param_validator.py` |
| ParamRegistry SSOT | `invasion/config/param_registry.py` (+ `invasion/config/_registry_core.py` / `invasion/config/_registry_api.py`) |
| Preg Files (domain) | `invasion/config/_params_{signal,sizing,exit,defense,gates,orphans,strategy_ai}.py` |
| Signal Composer | `invasion/signals/composer.py` (결함 5 drop 5-category → quarantine 전환 대상) |
| Signal Blocks Table | `invasion/signals/_signal_blocks.py` [T13 D14 planned, 현재 없음] |
| Metric Taxonomy SSOT | `docs/metric_taxonomy.yaml` [T13 D0 완료 `ecbadba1`] |
| Metric Contract Validator | `invasion/_metric_contract.py` [T13 D16a 동반 생성 예정, 현재 없음] |
| Position Health Score | `invasion/phs.py` [T13 D19 planned, 현재 없음] |
| Trade Events (DB) | `trade_events` table [T13 D8 완료 `c0d29970`, signal_id FK 추가] |
| Kill Switch | `invasion/ops/kill_switch.py` [T13 D6 완료 `321aea19`, file + DD 통합] |
| Backup Snapshot | `invasion/ops/backup_snapshot.py` + `scripts/restore_rehearsal.sh` [T13 D7+D7.5 완료 `7eac1ca1`] |
| Signal Blocks (jsonl) | `data/signal_blocks.jsonl` [T13 D-B 완료 `70254876`, DB 이관은 D14] |
| Signal Write Helpers | `invasion/data/_repo_signals.py` mark_signal_acted / mark_signal_rejected [T13 D8 완료 `c0d29970`] |
| Paper→Live Gate Preg | `invasion/config/_params_strategy_ai.py` paper_to_live_min_{trades,wr,sharpe} [T13 원안 #3 `7edfa07b`] |
| Duplicate Open Guard | `invasion/trade/_pipeline_scan.py` + preg `duplicate_open_window_sec` [T13 D9 완료 `ebede747`] |
| DB Schema Unified | `invasion/data/unified_schema.py` [T13 D10 완료 `2fe92e29`, 11 신규 테이블] |
| Forensic D11.5~9 | `tasks/forensic_t13_d115_to_9.md` [T13 `e128f96b`] |
| Harness Alert Router | `.claude/docs/alert_routing.md` + `invasion/ops/harness_alerter.py` emit |
| Alert Triage Detail | `.claude/docs/alert_triage_detail.md` [P2-23 split, alert-triage.md 보조] |
| Dashboard Redesign Appendix | `.claude/docs/dashboard_redesign_appendix.md` [P2-23 split, dashboard_redesign_mockup.md 보조] |
| Vault Knowledge Base | `vault/` (Claude second brain, **`.gitignore vault/`**, hourly db_views sync) [Jin 2026-04-26 `dc048ffd`] |
| Vault Sync Orchestrator | `tools/vault_sync_full.py` (db_views_export + crosslink, ~0.86s) |
| DB Views Export | `tools/db_views_export.py` (sqlite → vault/02-70_/50_cells/, hourly cron) |
| Preg Export (on-demand) | `tools/preg_export.py` (657 keys, manual run only) |
| Code AST Export (on-demand) | `tools/code_ast_export.py` (369 modules, manual run only) |
| Vault Crosslink | `tools/vault_crosslink.py` (전수조사 backlinks, called by vault_sync_full) |
| Vault Lint | `tools/vault_lint.py` (5 checks: symlinks/portability/timezone/frontmatter/cookbook) |
| Vault Symlinks | `tools/vault_symlinks.sh` (161 source → vault link) |
| Vault Live Diagnostic | `vault/_NOW.md` (Tier 0 entry, manual edit) |
| Vault Master Index | `vault/INDEX.md` (master nav) |
| Vault Architecture Map | `vault/05_process/meta/architecture_map.md` |
| Vault Workflows | `vault/05_process/meta/workflows.md` (6 standard workflows) |
| Vault Session Start | `vault/05_process/meta/SESSION_START.md` (다음 세션 entry checklist) |
| sqlite MCP Cookbook | `vault/05_process/meta/sqlite_mcp_query_cookbook.md` (10 SQL queries, validated) |
| Data Dictionary | `docs/governance/data_dictionary.json` (DB schema SSOT) |
