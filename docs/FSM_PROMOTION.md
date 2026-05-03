# FSM Promotion Checklist (Forward-looking staged rollout)

Forward-looking Plan a9f2a162 PR-C. Staged rollout gate for `exit_fsm_enabled*` flags.
Consumes live empirical health (Fwd PR-B) + per-slice flag (Fwd PR-A / MSG-FSM-STAGED).

## Pre-deploy
- [ ] T2-0 Replay harness 6 assertion (I1-I6) status
- [ ] FSM seed grid search best params (MSG-FSM-SEED-TUNE 참조)
- [ ] Current `exit_fsm_enabled` = 0 확증 (`pget('exit_fsm_enabled')`)

## Phase 1 — OKX crypto pilot (1h)
- [ ] `pset('exit_fsm_enabled_okx_crypto', 1)`
- [ ] 15min interval 3회 empirical:
  - `asym >= max(baseline_24h, 0.9)`
  - `pnl_mean > 0`
  - `wr >= 0.40`
- [ ] Live gate SQL: `DataStore.get_live_empirical_health(exchange='okx', asset_group='crypto', window_sec=3600)`
- [ ] 3 consecutive PASS → Phase 2
- [ ] 1 FAIL → `pset('exit_fsm_enabled_okx_crypto', 0)` + 14일 cooldown

## Phase 2 — All-paper extension (3h)
- [ ] Phase 1 통과 후 24h 경과
- [ ] `pset('exit_fsm_enabled', 1)` (global)
- [ ] 모든 slice 관찰 (okx_forex, alpaca_stock, cap_*)
- [ ] Per-slice asym/wr 측정
- [ ] 3h asym >= 0.9 → Phase 3

## Phase 3 — Permanent commit
- [ ] Phase 2 통과 후 48h 경과
- [ ] T2-1 PR5 legacy branch 제거 PR
- [ ] `exit_fsm_enabled` bounds (1, 1) lock
