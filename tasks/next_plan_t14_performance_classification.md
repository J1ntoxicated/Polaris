# T14+ Plan — Exchange / Cell Performance Classification + 설계 반영

> **수립**: 2026-04-22 T12 (구조 변경 T13 이후 T14 실행 대상).
> **원칙 준수**: `feedback_no_single_review_verdict`, `feedback_no_quick_patch_ever`, `feedback_flow_not_block`, `feedback_no_hardcode_in_plans`.
> **본 문서 상태**: 관찰 + 가설 + 검증 방법 리스트업. **단정/결정 X**. T14 리뷰 세션에서 전체 카드 펼친 후 결정.

## 배경

T12 에서 OKX 가 "cash cow 후보" 신호 관찰됨 (14h 내 -$615 → +$601). 단, 8개 미확인 조건 존재. **OKX 한정 아니라 전 exchange 에 대해 data 기반 성과 분류 절차** 필요. T13 구조 변경 완료 후 가능한 정확한 측정 위해 T14 에 배치.

## 목적

1. (exchange × group × cell) 별 **객관적 성과 분류** (cash cow / star / 질문표 / dog 등)
2. 분류 기반 **자본 allocation / risk budget / strategy pool / signal weight** 자동 조정
3. Paper → Live 전환 기준 확립
4. **북극성 flow 유지** — 분류로 차단/삭제 아닌 amplify/dampen 만

## Part N1 — 검증 조건 (T12 관찰된 8 + 추가 후보)

### N1.1 T12 관찰된 8 미확인 조건 (재확인)
1. Paper vs Live 격차 (fee/slippage/체결지연)
2. 샘플 크기 — 관측 window 길이 부족
3. 변동폭 지속성 (mean-reversion 여부)
4. 구조 결함 fix 후 재측정 필요
5. Signal drop 89% 적절성 (winner signal 함께 버리는지)
6. KPI asymm 실측 크기
7. Regime 다양성 (bear/sideways/bull 전부)
8. 병렬 운영 시 상관 (attention/capital 분산)

### N1.2 추가 검증 조건 (T12 후반 발견)
9. 시간대 / 세션 별 성과 차 (Asia vs EU vs US)
10. Group 별 성과 차 (같은 exchange 내)
11. Strategy 별 기여도
12. Entry signal 품질 vs 실제 pnl 상관
13. Ticker 개체별 kurtosis (outlier ticker 가 전체 왜곡?)
14. Drawdown 회복 속도 (per exchange × group)
15. Correlation 간 자본 이중 노출
16. Broker health / uptime 영향

## Part N2 — 측정 방법 (전부 preg, data-driven)

### N2.1 Window 다층화
- 1h / 24h / 7d / 30d / since-reset 5개 window
- 각 window 별 동일 metric 재계산 → trend 판정
- 모든 window 길이 preg (time-based 아닌 sample-based fallback)

### N2.2 Metric set (모든 metric cell_resolve)
- Raw: PnL, WR, count, fee_paid, slippage_bps
- Derived: avg_winner_pct, avg_loser_pct, profit_factor, sharpe_proxy, max_dd, recovery_time
- Normalized: per_unit_risk / per_unit_time / per_unit_capital
- Quality: signal→entry lag, peak_capture_ratio, missed_opportunity_ratio
- Robustness: regime×성과 variance, session×성과 variance

### N2.3 Fee / Slippage 반영
- Paper 계정도 realistic fee 적용 (taxonomy 에 per_exchange fee contract)
- `realized_slippage_bps` 재사용 (trades 테이블에 이미 있음)
- Paper pnl = simulated pnl - simulated_fee - simulated_slippage

## Part N3 — 분류 프레임워크 (후보, 단정 X)

### 후보 A: BCG 매트릭스 유사
- Cash Cow: high PnL + low growth (유지, 축소 아닌 amplify 유지)
- Star: high PnL + high growth (추가 amplify + 자본 확대)
- Question Mark: low PnL + high growth (관찰 강화, exploration 살림)
- Dog: low PnL + low growth (축소 X, 흐름은 유지 but 자본 배분 작게)

### 후보 B: 연속 score
- `performance_score = f(risk_adj_return, consistency, regime_robust, sample_size)` — cell_learned weights
- Score 기반 continuous allocation, 분류 box 없음 (flow 원칙 강화)

### 후보 C: Jin 판정 (hybrid)
- Data 제시 → Jin 최종 판정 + 이유 cell 학습
- 인간 감각 반영 + 자동화 병행

**T14 리뷰 시 결정**: 후보 A/B/C 중 선택 또는 조합. 현재는 가능성 나열만.

## Part N4 — 설계 반영 포인트 (T14 결정 후)

### N4.1 자본 Allocation
- (exchange × group × cell) 별 capital_weight cell_resolve
- 성과 높은 cell → weight 확대 (flow amplify)
- 성과 낮은 cell → weight 작게 but > 0 (학습 유지)
- Weight 재조정 주기 preg (hourly / daily / on-regime-flip)

### N4.2 Risk Budget
- Daily risk cap (자본 × preg) 을 cell 별 분배
- 성과 variance 큰 cell 은 risk budget 작게
- Safeguards: (exchange × group) 하루 max loss preg (amplify 원칙 유지하되 파산 방지)

### N4.3 Strategy Pool
- 성과 낮은 strategy → Elo 토너먼트에서 mutation 우선 대상
- 성과 높은 strategy → amplify + 유전자 보존
- 완전 retire 기준 preg (장기 dormant + upstream 우수 대체 있을 때만)

### N4.4 Signal Weight (Part M 연결)
- 성과 좋은 cell 의 active provider set 이 비중 높음
- 낮은 cell 의 set 은 관찰만 (학습 기록)
- Dynamic signal evolution 의 seed population 선택 입력

### N4.5 Paper → Live 전환 기준
- N 거래 이상 + N2.2 metric 전부 preg 임계 초과 → Live 전환 후보
- 후보 → shadow live (작은 size) → 검증 → full live
- 모든 단계 전환 임계 cell_resolve

## Part N5 — T14 실행 순서 (제안, 단정 X)

| 순서 | 작업 | 예상 | 선행 |
|---|---|---|---|
| N-0 | T13 구조 완료 확인 (Taxonomy / Cell / Tier / PHS) | 대기 | T13 |
| N-1 | N1 검증 조건 전수 SQL 작성 (관측 전용) | 2h | T13 |
| N-2 | N2 metric 계산 파이프라인 (cell_resolve 경유) | 3h | T13 Cell API |
| N-3 | 전 exchange × 전 group × 주요 cell 측정 실행 | 1h | N-2 |
| N-4 | 후보 A/B/C 비교 시뮬 (기존 데이터로 backtest) | 4h | N-3 |
| N-5 | Codex peer review + debate (단일 리뷰 아님, 다각도) | 2h | N-4 |
| N-6 | Jin 리뷰 + 분류 방식 결정 | 대기 | N-5 |
| N-7 | 설계 반영 (자본 / risk / strategy / signal / Paper→Live) | 6-10h | N-6 |
| N-8 | 24-48h 관찰 + KPI 비교 | 대기 | N-7 |

## Part N6 — 북극성 정합 (사전 체크)

- **Flow**: 낮은 성과 cell 도 weight > 0 유지 (완전 차단 X)
- **Amplify-only**: 분류 기반 weight 변경은 상대적 확대/유지만 (절대 축소 X)
- **Data-driven**: 모든 임계 / weight cell_learned
- **No hardcode**: 분류 boundary 도 preg / 알고리즘 기반
- **Asymmetry**: winner cell 에 자본 집중, loser cell 은 exit 구조로 자연 정리
- **Self-improving**: 분류 기준 자체가 성과 피드백으로 진화 (meta-learner)

## Part N7 — 잠재적 위험 / 반례 (미리 리스트업)

- **Overfitting to past**: 과거 성과로 분류 → 미래 regime 에서 실패 가능
- **Self-fulfilling**: 자본 집중이 성능 추가 강화 → 편향 누적
- **Exploration 희석**: cash cow 위주 → 신규 cell 기회 축소 (북극성 exploration 균형 주의)
- **Paper → Live 격차**: 분류 기준이 paper 데이터면 live 에서 달라질 가능성
- **Regime 편향**: 특정 regime 에서만 유효한 분류
- **Broker 장애 시 집중 위험**: cash cow exchange 의존 높을수록 장애 시 전체 타격

## 참조

- `tasks/prep_t13_hardcode_audit_and_integration.md` — T13 구조 변경 plan
- `tasks/observation_log_t12.md` — T12 hourly 관찰
- `tasks/anomaly_snapshot_t12.md` — T12 결함 증거
- Memory: `feedback_no_single_review_verdict`, `feedback_flow_not_block`, `feedback_adaptive_learner_attack`
- T12 commits: ~25건 (handoff_unified_2026_04_22_T12_session_end.md 참조)

---

**재확인**: 본 문서는 **리스트업만**. T14 세션에서 전체 카드 펼친 후 결정.
