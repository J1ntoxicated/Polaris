# 컴포넌트 설계 정합 감사 카탈로그

설계 의도 vs 실제 동작 정기 검증. 통합 Harness 세션이 agent dispatch 로 실행.

## 📈 Living Catalog
동적으로 성장. 패턴/기능 이상 발견 시 Harness 가 항목 추가 (tasks/audit_log.md 에 근거 기록).

## 컴포넌트 감사 (9건)

| # | 컴포넌트 | 트리거 | 검증 | 실행 agent | 이상 조치 |
|---|---------|--------|------|------------|----------|
| 1 | Regime | 매 전환 or weekly | VIX/DXY/HY 값, 가중평균, Crisis, 5-group 독립성 | ops-regime-watcher | 공식 → dev-coder / 임계 → ops-executor |
| 2 | Signal providers | 매 200 signals | fire 분포, dead provider, damp 0.85x, weight=0 | ops-log-advisor + dev-wire-guardian | dead → dev-coder / damp → dev-coder |
| 3 | Strategy selection | 매 100 trades | fitness+regime, size_mult, donchian 72%, idle | ops-trade-forensic | 편중 → /debate + Evolver |
| 4 | AI 판단 경로 | daily | augmenter, judge, controller, orchestrator 예산 | ops-log-advisor | 우회 → dev-coder / 예산 → ops-executor |
| 5 | Exit 거리 | 매 50 exits | stop/target=ATR×mult, trail, hard_stop, exit_type | ops-trade-forensic | misalign → ops-executor / 버그 → dev-coder |
| 6 | Evolver | weekly | mutation, fitness, 신규 전략 승격, tier1_replay | ops-trade-forensic | 미작동 → dev-coder |
| 7 | Tournament (Elo) | weekly | `strategies` 테이블 컬럼 존재, 업데이트 로그, 주기 | dev-audit-advisor | 불일치 → Jin 에스컬 |
| 8 | Gate 실차단 | weekly | 8 live gate 발동률 + 사유 분포 | dev-entry-gate-specialist | 과잉 → ops-executor / 0 → dev-coder |
| 9 | Param governance | daily | Governor review/h, Thompson, revert, hot-reload | ops-param-tuner | 과소 → 재검토 / 지연 → dev-coder |

## 북극성 감사 (6건 — Jin 철학 직결)

| # | 감사 | 트리거 | 검증 | 이상 신호 |
|---|------|--------|------|----------|
| 10 | 전천후 수익 | 매 100 trades OR weekly | regime × asset_group 매트릭스 PnL 양수 | 조합 지속 음수 → /research + Evolver seed |
| 11 | 공격성 정량화 | daily | max_positions 소진률, signal→entry 퍼널, regime별 진입률 | 저조 → gate 과잉 or signal 품질 |
| 12 | 비대칭 추세 | 매 50 trades | avg_win/avg_loss 시계열 (이상 >1.5, 대칭=위험) | 1.0 수렴 → 전략·exit 재검토 |
| 13 | Kelly edge | daily | 현재값, 추세, 심볼·전략 분해 | 음수 장기화 → 축소 + blacklist |
| 14 | Data freshness | Liveness Phase 1 후 자동 | tick frequency 분포 | stale 증가 → Liveness 조정 |
| 15 | Auto-evolve 속도 | weekly | generation, 신규 전략 승격, mutation | 정체 → Evolver 재가동 or seed |

## 신규 감사 (04-16 이후)

| # | 감사 | 트리거 | 검증 | 이상 신호 |
|---|------|--------|------|----------|
| 16 | Bot longevity | 매 12h + uptime ≥ 24h | silent death 빈도, RSS, FD count | 30h+ silent dead → MSG-SILENT-DEATH-54 fix 검증 실패 |
| 17 | STOP slippage | 매 50 STOP trades | `avg_pnl_pct × exit_type='STOP'` (단위 = 실제 %, × 100 금지) | avg < -2% → exit.py STOP branch audit |
| 18 | TIME symmetry | 매 24h | TIME × (pnl<0) vs TIME × (pnl>0) 비율 + avg | loss > 3× profit → early_flat + time_exit 조정 |
| 19 | Family cap reject | MSG-185 후 매 24h | `family_cap_abs` reject 건수, family 분포 | 0 reject or 특정 family 지배 → cap 재조정 |
| 20 | Strategy triple-block | Component C 후 매 24h | block triple reject + loss 회피 | 우회 경로 → filter wire 재검토 |
| 21 | Backfill safety | backfill 직후 + 매 24h | UNKNOWN_BACKFILL → tier1_replay / evolver / AI NULL 유입 | downstream crash → backfill pnl_pct=0 default + exit_type filter 확증 |

## 수행 방법

1. **수집**: `sqlite3 data/invasion.sqlite`, `grep data/invasion.log`, `data/param_history.jsonl`
2. **기준**: `CLAUDE.md`, `docs/ARCHITECTURE.md`, `feedback_aggressive_always_profit`
3. **비교**: 수치 차이 > 허용 오차 시 이상. root-cause 까지 추적
4. **라우팅**:
   - 파라미터 → ops-executor inline dispatch (pr.set / live_config.json)
   - 코드 로직 → dev-coder inline dispatch + git commit
   - 아키텍처 결함 → Harness 판단 (Jin 승인 필요 시 보고)
   - 설계-코드 불일치 → Jin 에스컬
5. **기록**: `tasks/audit_log.md` findings append

## 참조

- 부팅 절차: [../commands/harness-mode.md](../commands/harness-mode.md)
- 감사 증거 기반: [../../memory/feedback_root_cause_evidence_based.md](../../memory/feedback_root_cause_evidence_based.md)
- 감사 카탈로그: [audit_framework.md](audit_framework.md)
