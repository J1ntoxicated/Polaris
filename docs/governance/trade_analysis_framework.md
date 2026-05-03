# Trade Analysis Framework — Post-Trade Ecosystem Verification

## Trigger
- 50거래 이상 축적 시 자동 실행
- 100거래마다 재실행
- Regime 변경 시 재실행

## Analysis Layers

### Layer 1: Trade Outcome Analysis (기본)
- Win/Loss 패턴: 왜 이겼나, 왜 졌나
- Exit type 분포: TIME_STALE 비율 개선됐나?
- Hold time 분석: 2-5분 수익 구간 여전히 유효?
- Size vs outcome: 큰 포지션이 더 나은가/나쁜가?

### Layer 2: Signal Bias Detection
- 시그널 방향 편향: short 과다? long 부족?
- Provider 편향: 특정 provider가 항상 같은 방향?
- Entry strength vs outcome: r 개선됐나? (이전 0.024)
- Score 분포: sweet spot(25-45) 여전히 유효?
- FundingSignal 방향 수정 후 효과 확인

### Layer 3: Strategy Appropriateness
- 전략별 WR/PnL: 어떤 전략이 실제 수익?
- 전략-레짐 매칭: preferred_regimes 설정 효과?
- 전략 선택 빈도: exploration_bonus 40 적절?
- 좀비 전략 재출현 여부

### Layer 4: Exit Mechanics Validation
- STOP overshoot: -0.8% limit → 실제 몇 %?
- BEP activate 0.3% 효과: 더 많은 거래 보호?
- Trail activate 0.6% 효과: TRAIL exit 증가?
- flat_kill 2700s: TIME_STALE 감소?
- max_hold 1800s: 30분+ 손실 제거?
- ProfitTaker: 여전히 WR 100%?

### Layer 5: Regime Appropriateness
- 현재 레짐 vs 시장 실제 상태 일치?
- 레짐별 거래 성과: risk_off에서 정말 수익?
- Choppy gate 작동 확인: choppy 거래 0건?
- F&G=11 (Extreme Fear) → CRISIS 발동?
- 3-layer for_ticker: ticker/group/global 실제 blend 확인

### Layer 6: Post-Trade Events (생태계)
- **Evolution**: 거래 후 evolver 트리거됐나? 어떤 mutation?
- **Tournament**: bracket 구성 정확? Elo 업데이트?
- **Feedback loops**: 
  - signal quality tracker: 패턴 기록 + WR 업데이트?
  - adaptive tuner: 파라미터 조정?
  - ticker learner: 블랙리스트/화이트리스트 업데이트?
  - sizing feedback: tier/regime mult 조정?
  - session learning: 시간대별 size mult?
- **Defense**: consecutive loss 추적? WR degrade 작동?
- **AI Controller**: 포지션 리뷰 정상? conf ≠ 5.0?

### Layer 7: Architecture Document Alignment
- 아키텍처에 기술된 데이터 플로우대로 실제 작동?
- EventBus 이벤트 발행/구독 확인 (로그에서)
- Scheduler job 실행 확인 (각 tick 로그)
- Dictionary의 pseudo-code vs 실제 실행 경로

## Expert Assignment
| Layer | Expert Skill | Agent |
|-------|-------------|-------|
| 1-2 | /alpha-strategist | 퀀트 트레이더 |
| 3 | /strategy-review | 전략 전문가 |
| 4 | /trade-analysis | 트레이드 분석가 |
| 5 | /strategy-review | 레짐 전문가 |
| 6 | /health-check | 시스템 엔지니어 |
| 7 | /code-review | 아키텍트 |

## Output
- 각 Layer 분석 결과 → dictionary Layer 2 (ASSESSMENT) 업데이트
- 개선 필요 항목 → todo.md 등록
- 디베이트 필요 항목 → /debate 태그
- 모든 결과 → verification_log.md에 기록
