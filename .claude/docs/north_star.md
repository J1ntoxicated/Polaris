# 🌟 Jin의 북극성 (Architectural Invariant)

**"어느 시장 상황에서도 수익이 있다"** — 시스템 존재 이유.

## 의미

- 단순 "손실 최소화" 아님. **모든 regime에서 positive P&L**이 목표
- crisis에서 contrarian으로 이기고, risk_on에서 trend follow로, neutral에서 mean-reversion으로 — **regime별 edge** 보유
- 한 가지 전략의 "평균 승률"이 아니라, 각 regime마다 **승리 전략 존재**하는 구조

## 아키텍처 지원 요소 (기존)

- Dynamic per-group regimes (asset group별 독립)
- 모든 regime이 ATTACK variant (방어 모드 없음)
- Strategy auto-evolution (Elo tournament + genetic mutations)
- Crisis Escalation

## 검증 지표 — "전천후 수익 검증"

- **트리거**: 100 trades 누적 OR 주 1회 fallback
- **체크**: 각 regime × asset_group 조합 PnL 분포
  - 특정 regime 지속 음수 → 그 regime 대응 전략 부재 = **architectural gap**
  - 개선: 해당 regime 전략 연구 (`/research`)
- **주체**: 🟩 Harness 통합 (ops-log-advisor + ops-trade-forensic dispatch → architectural synthesis)
- **라우팅**: AUDIT 판단 → dev-coder inline dispatch (구조 변경 시)
- **메모리**: `feedback_aggressive_always_profit` (공격성 상시 수익), `feedback_no_defensive_param_dampen` (dampen 금지)

## 거래 분석 방향 (ops-trade-forensic 렌즈)

단순 "이 파라미터 어떻게?" 아니라:
1. **regime별 coverage** — 오늘 어느 regime 거쳤나? 각각 수익 냈나?
2. **blind spot 탐지** — 특정 regime에서 늘 져왔다면 = 전략 부재
3. **asset × regime 매트릭스** — 크립토-crisis OK이지만 neutral에서 약하면 수정 대상
4. **진화 가이드** — 약점 regime 발견 시 `/research` → Evolver seed

## Jin의 방향성 (contrarian crisis-max)

CLAUDE.md: **Aggressive Contrarian — crisis = opportunity, max bet on fear**.

거래 분석 렌즈:
- 공포 극단(F&G<20) 수익률 높은 전략 → **증폭**
- 평온한 시장 수익은 **의심** (편차 작은 상황에서 운?)
- risk_off regime 이긴 트레이드 = **golden data**

## 100% 메트릭스화 (2026-04-24 Pivot)

북극성 달성 메커니즘 = **`strategy_cell_matrix` 8-dim SSOT**. 모든 trade decision 이 cell lookup:
- **Sizing**: cell_score_mult (Phase 1 — 11 multiplier 통합)
- **Exit threshold**: cell.optimal_trail/bep/max_hold (Phase 2)
- **Direction**: cell_score_long vs cell_score_short 비교 (Phase 3)
- **Provider weight**: cell × provider matrix (Phase 4)
- **Strategy 선택**: cell × strategy Elo (Phase 5)

global preg = fallback only. Hardcode = FROZEN 영역만 (clean_data_epoch / kill_switch / safety).

cell 가 axis 별 학습 → 북극성 "어느 시장 상황에서도 수익" = "어느 cell 에서도 cell_score > 0" 으로 측정 가능.

Plan: [`.claude/plans/cell-matrix-100pct-pivot.md`](../../plans/cell-matrix-100pct-pivot.md)

## 관련 메모리
- [feedback_aggressive_always_profit](../../memory/feedback_aggressive_always_profit.md) — 공격적 상시 수익 철학
- [feedback_loss_profit_asymmetry](../../memory/feedback_loss_profit_asymmetry.md) — 비대칭 유리

## 참조
- [loop.md](../loop.md), [harness-mode.md](../commands/harness-mode.md), CLAUDE.md
- [`cell-matrix-100pct-pivot.md`](../../plans/cell-matrix-100pct-pivot.md) — 100% 메트릭스화 5 phase
