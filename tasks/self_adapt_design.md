# Self-Adapt Upgrade — 설계 (12:00 AEST 결정용)

## 현재 상태 진단 (실측 데이터)

### Cell Matrix score 분포 (468 cells)
| Bucket | Range | Cells | % |
|---|---|---|---|
| SKIP | score < -0.5 | 36 | 7.7% |
| NEUTRAL (1.0) | -0.5 ~ 0.2 | **378** | **80.8%** |
| MILD amp (1.2) | 0.2 ~ 0.6 | 38 | 8.1% |
| STRONG amp (1.5-2.0) | > 0.6 | 16 | 3.4% |

**판단**: Neutral bucket **80%** → threshold loose, amplify/skip 적용 비율 낮음. Quantile 기반 전환 필요.

### Regime 실측 (7d)
| Regime | Trades | WR | Avg pnl | Default mult | 적합성 |
|---|---|---|---|---|---|
| **transition** | 2,195 | 40.6% | **-$2.32** | ❌ **preg 없음** (fallback 1.0) | ⚠️ gap |
| neutral | 7,823 | 43.3% | -$2.26 | 1.0 | ✅ 유지 |
| risk_off | 706 | **65.2%** | -$1.07 | 1.2 | ⚠️ WR 높은데 avg 음수 — asymmetric loss |
| risk_on | 1,105 | 38.3% | $0 | 1.0 | ✅ baseline |
| crisis | 0 | — | — | 1.5 (추측) | ⏳ 샘플 없음 |

**판단**: 
- `transition` regime 대량인데 mult 정의 없음 → 추가 필요
- `risk_off` WR 65%/avg -$1 = 큰 loser 섞임. mult=1.2 amplify 는 loser amplify 위험

### Conviction 분포
- 현재 0건 (Phase A 배포 직후, Asia 초반)
- 데이터 충분해질 때까지 learner 는 대기

---

## 설계 — 3단계 Upgrade

### P0. Regime learner + transition 추가 (~50줄, 즉시 가능)

**문제**: 
1. `transition` regime 의 mult preg 없음
2. Regime mult 가 static default, 실적 반영 X

**제안**:
1. `regime_size_mult_transition` preg 추가 (default 1.0, bounds 1.0-1.3)
2. `invasion/ticks/hourly_stats.py` 의 `_learn_session_mult` 패턴 따라 `_learn_regime_mult` 신설:
   - 매 hourly tick 에서 regime 별 WR/avg_pnl 집계
   - WR≥55% AND avg_pnl>0 → mult +0.1 (up to preg bound)
   - WR<40% AND avg_pnl<-$5 → mult -0.1 (floor 1.0, dampen 금지)
   - Smooth ±0.1/cycle
3. 학습된 값을 live_config.json 에 persist

**파일 변경**:
- `invasion/config/_params_sizing.py` +1 preg
- `invasion/ticks/hourly_stats.py` +40줄 (새 learner 함수)
- `invasion/ticks/` 의 hourly_tick loop 에 연결

**북극성 정합**: ✅ Amplify-only (mult floor 1.0)

### P1. Quantile-based cell threshold (~40줄)

**문제**: 현재 bucket 경계 (-0.5/0.2/0.6) 고정 → 80% cell 이 neutral 로 빠짐

**제안**:
1. `cell_matrix.py` 의 `_score_to_mult` 를 quantile 기반 전환:
   - Computed 매 1h (CellMatrixReviewer) 에서 `p10, p25, p75, p90` 계산
   - 결과 저장 (in-mem + DB)
   - `lookup_cell_score` 호출 시 현재 score 가 어느 quantile 인지 판단:
     - < p10 → SKIP
     - p10 ~ p25 → 1.0 (보수)
     - p25 ~ p75 → 1.0 (neutral)
     - p75 ~ p90 → 1.2 (mild)
     - > p90 → 1.5-2.0 (strong)

**파일 변경**:
- `invasion/strategy/cell_matrix.py` +40줄 (quantile calc + lookup 변경)

**북극성 정합**: ✅ data-driven (분포 변하면 자동 재조정)

### P2. Conviction step learner (~30줄, **데이터 대기**)

**문제**: conviction_step=0.3, conviction_max=2.0 hardcoded. agreement=N 별 WR 실적 학습 안 됨.

**제안**: (데이터 충분해진 후)
1. 거래가 수행되며 `conviction_count` field 기록 (이미 cand 에 주입 중)
2. `strategy_conviction_stats` table: agreement N × WR × avg_pnl
3. `_learn_conviction_step` 주기 학습
4. 조건: conviction_count 를 trades 테이블에 저장하는 경로 필요 (현재 entry cand 에만 있음)

**규모**: +30줄 + DB schema 확장 + entry persistence

**북극성 정합**: ✅ data-driven + amplify-only

---

## 구현 순서 권고

| 단계 | 규모 | 의존성 | Asia 세션 내 가능? |
|---|---|---|---|
| **P0 Regime learner** | 50줄 | 독립 | ✅ 즉시 |
| **P1 Quantile cell** | 40줄 | Phase A (완료) | ✅ 즉시 |
| **P2 Conviction learner** | 30줄 + schema | conviction_count persist 필요 | ⏳ 데이터 충분 후 (1-2일) |

P0 + P1 = **90줄 단일 commit**. 2-3h 작업.

---

## C. Top-K Selection 병행 가능성

C 는 cell matrix + signal score 결합 rank. P1 (quantile cell) 과 자연스러움:
- P1 이후 cell score 가 normalized → signal × cell_score rank 직관적
- **P1 → C 순서** 로 하면 통합 clean

---

## 우선순위 최종 제안 (Jin 12:00 결정용)

**Option X**: P0 + P1 (self-adapt 기반) → 관측 1-2h → P2 + C (learner + top-K)
**Option Y**: P0 만 → 관측 → P1/C 병합 고려
**Option Z**: 관측 더 (현재 conviction 0 건, Phase B 검증 불충분) → 14:00 재평가

권고: **Option X** — self-adapt 기반이 북극성 정합 핵심 (hardcoded 벗어나 data-driven). 규모 90줄 관리 가능.

---

## 실행 명령 (Jin 결정 시)

```bash
# P0 + P1 구현 (dev-coder)
# → 1 commit (~90줄)
# → restart
# → 1h 관측 후 P2/C 진행
```

## Rollback 경로

각 preg 별 kill-switch 또는 learner `enabled` flag. 예: `regime_learner_enabled=1` → 0 시 바이패스.

## 북극성 정합 최종 확인

| 원칙 | P0 | P1 | P2 |
|---|---|---|---|
| Aggressive always profit | ✅ | ✅ | ✅ |
| Amplify-only (≥1.0) | ✅ preg floor 1.0 | ✅ mult 1.0~2.0 | ✅ |
| No defensive dampen | ✅ | ✅ | ✅ |
| Data-driven evolution | ✅ learner | ✅ quantile | ✅ conviction WR |
| No block filter | ✅ | ⚠️ quantile p10 SKIP 은 `_PERMANENT_` 동일 정당화 | ✅ |
