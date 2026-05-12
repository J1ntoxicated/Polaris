---
type: ADR
adr_id: ADR-002
status: active
date_created: 2026-05-06
tags: [adr, vision, vision-targets]
related: [[north-star]], [[active-autonomous-vision]], [[ADR-003]]
reviewed_by: codex+jin (round 2 T1 + round 3 D3 + Jin sign-off)
---

# ADR-002 — Vision: Active Autonomous Demo Aggressive

## Decision

### Targets (DEMO ONLY)
- **Primary**: 일 평균 +0.75% (compounding 252 → +560%/yr; $130k → $865k 가상)
- **Stretch**: 일 평균 +1.25% (~5,400%/yr 이론치)
- **Daily intraday band**: soft ±5% / stretch ±8% / ±8%+ tag-only (실행 차단 X)

### DEMO unlock (절대 컨텍스트)
- OKX SPOT demo (`us.okx.com` + `x-simulated-trading: 1`) — 가상 USDT $79k
- Capital CFD demo (`demo-api-capital.backend-capital.com`) — 가상 AUD $78k
- 합산 ≈ USD $130k. **실제 자금 손실 = 0**
- real-money 보수 논거 무효 (regulatory cap / ASIC retail / professional risk / fund mandate / capital protection)

### Vision: Active Autonomous Evolution System
- Dynamic ticker universe (하드코드 X) — 거래 가능한 모든 ticker 동적 모니터
- Per-gate AI agent supervisory — 각 단계 AI 결정
- 자가 진화 + 자가 correcting — learner network + cell matrix + live recalc
- Mid-trade strategy swap + Adaptive exit — AI 가 매 단계 "이번엔 다르게"

See [[active-autonomous-vision]] for 8 핵심 컨셉

### Miss escalation policy (continuous trade-driven, 시간 X)

**Trigger evaluation** = 매 closed trade 직후
**Lever 변경 발동 조건** = 4개 동시 충족:
1. rolling 100-trade portfolio expectancy < 0
2. rolling 40-trade expectancy < rolling 100-trade expectancy (열화 가속)
3. 직전 lever 변경 후 ≥ 20 closed trades 경과 (cooldown, flip-flop 차단)
4. 손상 지표 ≥ 2개 동시 훼손 (win-rate / avg R / profit factor / MFE-MAE ratio)

### 4 lever 순서 + skip 정책
**순서**: reallocate → strategy_add → sizing(survivors only) → leverage(gated)
**Skip**:
- localized miss: reallocate → sizing → strategy_add
- broad miss: reallocate 생략 → strategy_add 직행
- 2회 연속 lever 변경 후에도 rolling 40-trade 개선 X → strategy_add 점프
- leverage gate: rolling 100-trade expectancy 양수 복귀 전 unlock 금지
- 신규 추가 sleeve = 기존 상위 2 sleeve 와 상관 낮은 쪽 우선

### Drawdown checkpoint (실행 차단 X, 데이터 가치만)
- intraday -8%: snapshot + 원인 태깅
- rolling 5d -20%: feature dump + freeze-copy
- venue equity -35%: full position state freeze

### Auto-stop = 없음
Jin manual only. Demo 자금 0 → DB reset → restart (학습 데이터 archive)

### regrets/ 폐기 → B' + D + C 대체

#### B' — Continuous lever_change log
- vault path: `40_ops/lever_changes/<event_id>_<date>.md`
- 트리거: §1 4-조건 충족
- 내용: rolling expectancy 통계 + 발동 lever + skip 결정 사유
- 허용: reallocate / strategy_add / sizing(survivors only) / leverage(gated)
- 금지: failing sleeve upward sizing, leverage gate skip

#### D — Forensic on checkpoint trigger
- 발동 조건: drawdown checkpoint OR 동일 strategy + correlation_group 7d 내 ≥3 stop-loss OR strategy circuit breaker HALT
- vault path: `50_research/forensic/<event_id>_<date>.md`
- 항상 활성 X

#### C — Winner-only ELO (cap-bound)
- 매 trade 종료 시 strategy_evolution agent ELO update
- Loser 자동 감점 X
- Winner 증액 재원 = 유휴 현금만, 총 cap 초과 금지
- 증액 룰: winner sizing scalar +0.05 / 100 trades (max 3.0×, T4 tier amplifier 와 통일)

## Aggressive bias self-check
- 거부 키워드 sweep: `12주`, `90d`, `60d`, `regulatory cap`, `professional risk`, `monthly review`, `30일 lock-in`, `표본 부족 risk`, `real-money safety` → 0건

## Sources
- Round 2 T1 vision (codex 0.5%→0.75%/1.25% 권고)
- Round 3 D3 continuous trade-driven (`/tmp/polaris_debate_round3/d3_consensus.md`)
- Jin clarification 21:30 (active autonomous vision)
- Memory: `feedback_active_autonomous_vision.md`
