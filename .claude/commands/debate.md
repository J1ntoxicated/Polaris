# Debate — Trading 3-AI Debate + Auto-Apply

Trading-specific 3-AI 토론 + 자동 합의 적용. Absorbs: `debate-apply`.
Auto-Apply 상세: [debate_apply.md](debate_apply.md).

## Usage
```
/debate "hard_stop -2.0% → -2.5%?"
/debate "crypto long-only vs allow shorts"
/debate "observe time 20s → 30s effect"
```

## Workflow

### 1. Auto Context Collection
토론 전 자동 수집:
- 현재 config 파라미터 (`live_config.json`)
- 최근 성과 (`trade_stats` + `okx_paper` summary)
- `stats_summary.json`
- 최근 파라미터 변경 이력 (`git log`)

### 2. Expert Prompts
각 AI에게 트레이딩 전문가 프롬프트 + 컨텍스트 자동 주입:
```
You are a quantitative trading systems expert.
[AUTO-INJECTED] Current config: {...}, Recent performance: {WR, RR, Net, streak, exit dist}
[QUESTION] {user question}
Answer: 1) Position 2) Specific values 3) Expected WR/RR/DD impact 4) Worst-case risk
```

### 3. Consensus
- **3/3 합의** → high confidence, 즉시 적용 제안
- **2/3 합의** → majority + dissent 표시
- **전체 상이** → Jin 판단

### 4. Result Storage + Auto-Apply
상세: [debate_apply.md](debate_apply.md)

## Trigger
- Jin 전략 질문
- `/strategy-review`가 중요 파라미터 변경 제안
- 변경 전 교차 검증 필요

---

> 🔴 **Vault mandatory** — SSOT: [[vault_mandatory_protocol]]. 진입 read `_NOW.md` + entity, 종료 write 1+ (INSIGHT/ADR/digest/audit). Entity 링크 의무.
