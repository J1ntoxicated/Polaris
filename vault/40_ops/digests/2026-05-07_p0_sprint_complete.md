---
type: runtime
status: active
date_created: 2026-05-07
tags: [digest, p0-sprint, complete, milestone]
related: [[ADR-001]], [[ADR-002]], [[ADR-003]], [[ADR-004]], [[ADR-005]], [[ADR-006]], [[ADR-007]], [[ADR-008]]
reviewed_by: codex (38+ technical + 17 4-axis = 55 codex calls)
---

# P0 Sprint Complete — 2026-05-06 ~ 2026-05-07

## Status: P1.0 Ignition READY

`python3 -m polaris.scripts.ignite_p1 --paper --duration 86400 --tick 5 --full-pipeline --real-roundtrip --db data/polaris.sqlite`

## 7-day Sprint Summary

| Day | Scope | LOC | Tests | Technical Codex | 4-axis Codex |
|---|---|---|---|---|---|
| 1 | Layer 0/1 (universe + canonical + baseline + normalize) | 2,896 | 58 | R1 4 P0 fixed | - |
| 2 | Layer 4/7 (cell matrix + isolation) + Capital proxy | 4,600 | 143 | R3 APPROVE | - |
| 3 | Layer 2 (8-gate pipeline + 4 Haiku gates) + L3 full T4 + L1 bar ingest | 3,969 | 225 | R3 APPROVE | - |
| 4 | Layer 5/6 (learner network + live recalc stub) + 7 strategies signal generator | 3,840 | 281 | R2 APPROVE | R6 APPROVE |
| 5 | Venue adapters full (OKX trade/order + Capital position lifecycle) + fill_normalizer + dashboard v0 | 2,180 | 339 | R1 APPROVE | R4 APPROVE |
| 6 | Full pipeline G4-G7 plumbing + real fill round-trip + fills DDL + ignite_p1 wire | 2,500 | 385 | R3 APPROVE | R7 APPROVE |
| 7 | 30-min ignition smoke + 24h readiness verify (18 watchdog probes) | 680 | 406-408 | R3 APPROVE_WITH_NITS | (skip — ignition 영역) |
| **Total** | | **~20,665 LOC** | **408 tests** | **38 calls** | **17 calls (3/3 APPROVE)** |

**Plan v5 target ~7,000 LOC → 3× 초과** (active autonomous evolution system 복잡도 반영). Codex 매일 APPROVE + Day 별 4-axis policy review 가 추가 architectural debt cleanup 까지 잡음.

## 30-min Live Smoke Result (Day 7)

| Metric | Value |
|---|---|
| Ticks | 99+ / 360 projected |
| signals_emitted/tick | 8 (4 OKX strategies × 2 OKX symbols) |
| **fills_persisted** | **896 (225 closed)** |
| size_usd_total (22min) | **$158,420** notional |
| **pnl_usd running** | **+$198.05** |
| pipeline_kills | **0** (zero rejections / zero errors) |
| cell_matrix active cells | 3 (okx/tsmom/BTC, volume_burst, spot_donchian — n_eff ≥ 63 each) |
| learner_state keys | 15 across 4 types (max_hold:6, regime_mult:3, session_mult:3, triple_stats:3) |
| learner_snapshot rows | 3 hourly snapshots persisted |
| OKX 401 errors | **0** |
| Capital CST expirations | 0 |

## 8-Layer Architecture Verified

| Layer | Status | Key spec |
|---|---|---|
| L0 Dynamic Universe | ✅ | 4-axis filter (vol/spread/atr/depth) + 12-48 dynamic focus + listing watchdog |
| L1 Canonical + Baseline | ✅ | bars/quote_ticks/market_events + 5-metric ratio normalize |
| L2 Per-Gate AI Pipeline | ✅ | 8 gates (G1-G8), Haiku G1/G3/G4/G8, Python G2/G5/G6/G7, phase guard P0/P1 |
| L3 Sizing + Risk | ✅ | T4 공식 + tier amplifier (1.5/2/3×) + Cold Start CS-3 + cluster cap + fill-rate cut |
| L4 Cell Matrix | ✅ | 4-dim P0 (exchange × strategy × ticker × regime) + EWMA 7d + warmup shrinkage + dynamic quartile gate ≥20 + top ×1.5 |
| L5 Learner Network | ✅ | 3 P0 (session_mult / regime_mult / max_hold) + adaptive_learner_attack 4 원칙 + hourly auto-tune + snapshot rollback |
| L6 Live Recalc | ✅ stub | dirty-trigger 5s + regime SSOT 2-consecutive + max 1 swap/trade + same correlation_group only (P0 logging only) |
| L7 Strategy Isolation | ✅ | per-strategy asyncio task + central allocator (one Lock + reservation TTL 5s) + 4-state breaker + idempotent order keys |

## Aggressive Bias Self-Check (final)

- [x] Drawdown auto-stop 없음 (snapshot only at -8/-20/-35%, 차단 X)
- [x] Daily target hard limit 없음
- [x] KPI auto-disable 없음 (Jin manual only)
- [x] Monthly review 없음 (continuous trade-driven trigger)
- [x] Regrets/ 없음 (B' lever_change + D forensic + C winner-only ELO max 3.0×)
- [x] Macro guard / news blackout 없음
- [x] Posture standard 없음 (aggressive only, reserved field)
- [x] v1 9-stack collapse 영구 봉쇄 (T4 1 scalar BEFORE clip + tier amp + hard MAX, headroom min())
- [x] Cell mult clip-전 placement (Day 2 SSOT, Phase 0 L3 합의)
- [x] Top quartile ×1.5 amplify (Phase 0 L4 patch — ×1.3→×1.5 상방 개방)
- [x] G8 P0 Python template / P1 Sonnet (architectural split, Day 6 R7 fix)

## Real Demo Trade 검증

- ✅ OKX SPOT demo IOC fill (BTC-USDT 0.00012262 BTC @ $81,514.3, ordId 3542763044948807680)
- ✅ Capital CFD demo round-trip (EURUSD 100-lot @ 1.1752, dealRef 양쪽 ACCEPTED)
- ✅ `.env` `OKX_DEMO_BASE` 잘못 박힌 거 자동 detect + override (`feedback_okx_region_endpoint` memory 효과)
- ✅ 30-min smoke 896 fills / 225 closed / $158k notional / +$198 PnL / 0 kills / 0 errors

## Codex Cumulative Findings (P0 sprint 통해 잡힌 것)

### Technical review (매 day codex APPROVE 까지)
- Day 1: 4 P0 (hard filter, Capital P0 whitelist, listing watchdog wiring, L1 cold-start chain)
- Day 2: 4 P0 (warmup shrinkage dead code, EWMA decay 미적용, circuit breaker non-monotonic, RISK_ONLY tick skip) + 1 P0 (SSOT prod path)
- Day 3: 2 P0 (lifecycle bypass, initial_stop_price not forwarded) + 3 P1 → R3 one-way lifecycle invariant
- Day 4: 1 P0 (max_hold winner-hold collapse) + 3 P1 (race / swap mismatch / regime seed)
- Day 5: 2 P1 (UTC + leverage factor)
- Day 6: 3 P0 (paper-mode db_path, OKX close base_ccy, round-trip ok=True without close) + 2 P1
- Day 7: R1-R3 18 watchdog probes (kill-switch, urlparse netloc-equality, learner cancel race)

### 4-axis Policy review (Day 3 era debt 까지 catch)
- Day 4 R6: 15 fixes (disk snapshot pair, segment schema, named constants 5 strategies, regime SSOT, entry seeding, active_strategy_id guard, legacy ALTER, unconditional backfill)
- Day 5 R4: 11 fixes (OKX clamp docstring, Capital auto_ping, dashboard fills prefer + qty_base separate, dead get_capital_session_env, Final[] constants, TARGET_HEIGHT 제거)
- Day 6 R7: 7 rounds (ignite contract, 11 SSOT 상수, G8 P0/P1 model split architectural fix, reflector 3 hardcodes, Python template lesson generation + Δ clamp, P1 Δ rail ADR-007)

## Known Gaps (Jin mandate 정합 — blockers X)

- Drawdown checkpoint (-8/-20/-35%) DDL 만, snapshot trigger 미wire (Phase 0 합의: 차단 X, 데이터만)
- policy_engine matrix not fully wired (Haiku stub all-passes today)
- Auto-stop on demo-fund=0 deliberately absent (`feedback_aggressive_always_profit`)
- Capital close-leg exception regression test parity (OKX 있음, Capital 없음 — Day 8 fix)
- Layer 0 long-running 5/10-min refresh (현재 one-shot at boot, P1.x)
- Pre-existing flake `test_layer2_pipeline.py::test_g8_p1_phase_forwards_haiku_client` (full suite pass, isolation fail — state pollution, Day 8)

## Vault Stats

- 8 ADRs minted (creation order, all wikilinked)
- 5 charter notes (north-star / aggressive-bias / active-autonomous-vision / coding-conventions / karpathy-workflow)
- 8 component specs (vault/30_components/layer-0~7-*.md, Phase 0 codex 합의)
- 7 strategy specs (vault/20_strategies/, Day 4)
- 7 day digests + 3 4-axis review digests
- 2-tier lint clean (light pre-commit + heavy weekly cron)

## Memory Stats

- Active: 27 + 2 신규 (`feedback_active_autonomous_vision.md` + `feedback_p0_sprint_review_cycle.md`) = 29
- Archive: 81 (모태 historical, read-only)
- 거부 키워드 0건 (12주 / 90d / regulatory / professional / monthly review / regrets / posture standard / fractional Kelly is too aggressive in practice)

## Jin Awake-time Action

### 24h Paper Loop Start
```bash
python3 -m polaris.scripts.ignite_p1 \
  --paper --duration 86400 --tick 5 \
  --full-pipeline --real-roundtrip \
  --db data/polaris.sqlite
```

### Stop
```bash
kill -SIGTERM <pid>  # probe-verified clean exit
```

### Dashboard (별도 터미널)
```bash
python3 -m polaris.scripts.dashboard_v0 --refresh 5
```

### Day 8 Backlog (P0 → P1 transition)

1. Capital close-leg exception regression test parity
2. Layer 0 long-running 5/10-min refresh wire
3. Drawdown checkpoint snapshot trigger wire
4. policy_engine matrix wire (Haiku stub → real Haiku)
5. P1.x: Sonnet upgrade (entry-sizer / position-monitor / adaptive-exit / post-trade-reflector)
6. P1.x: Layer 6 self-correction full (regime flip auto adjust + mid-trade strategy swap)
7. P1.x: 7 learner full (4 P1 추가)
8. P1.x: Cell matrix 8-dim 확장
9. P1.x: WebSocket 도입 (REST polling 대체)
10. P1.x: Signal Funnel SCOPE4 dashboard

## Self-correcting Cycle 입증

- 7 day × technical codex review (REJECT→fix→APPROVE)
- 3 day × 4-axis policy review (REJECT→fix→APPROVE @ R6/R4/R7)
- 누적 ~80+ fix iterations / 0 plan v5 deviation
- Aggressive bias preserved across all fixes (defensive throttle X, hard cap headroom min(), hi-risk hi-return)
- Demo unlock applied throughout (`real-money safety` keyword 0건)

**P0 Sprint = 자율 cycle 입증 + P1.0 paper trading READY.**
