---
entity_type: insight
entity_id: INSIGHT-034
auto: false
last_modified: 2026-05-04
expires: never
editable: true
back_links: ["[[HYPO-AI-001]]", "[[ADR-012]]"]
mode: dev
reviewed_by: codex
tags: [type/insight, scope/architecture, polaris, phase3, ai-advisor]
---

# INSIGHT-034 — AI Advisor 부활 (모태 핵심 가치 인수)

## Context

Polaris 14 rounds 동안 hardcoded threshold strategy만 운영. AI 개입 0%.
Jin mandate (2026-05-04): '원래 의도는 AI 개입 실시간 분석으로 거래'.
모태 (auto_invasion_mk1) advisor 패턴 → Polaris SPOT 전용 재설계.

## Insight

**모태 핵심 가치**: AI advisor는 단순 signal filter가 아닌 multi-source aggregator.
  - tick + book + flow + sentiment + macro → 단일 confidence score
  - Per-candle Claude/GPT analysis → action decision
  - Adaptive: 시장 구조 변화에 자연스럽게 반응 (hardcoded threshold 대비)

**Polaris 재설계 원칙**:
  1. Pure core + Shell I/O 분리 (P6 준수)
  2. Rate limit (60s/ticker) + Cache (5min) → API cost $0.11/h 안전
  3. Anthropic Haiku 4.5 (최저비용 Claude) — quality vs cost 최적
  4. 모태의 복잡한 orchestrator 제거 → 단순한 per-strategy evaluate_ai()
  5. 기존 realtime_runner 인프라 재사용 (WS + candle cache + regime)

## Pattern Extracted from Legacy

모태 wiring_ai.py 분석:
  - 6 live stages: signal_augmenter / entry_judge / exit_adviser / strategy_evolution / portfolio_intel / proactive_exit
  - AIOrchestrator singleton (budget sharing)
  - PromptEvolver (Thompson sampling variant selection)

Polaris Phase 3 scope: entry_judge만 (최소 viable — 결과 측정 후 확장).
향후 확장 후보: exit_adviser (exit 시점 AI 판단), signal_augmenter (기존 strategy score 보정).

## Cost Guard

```
180 calls/h × $0.0006 = $0.108/h
일일 max: $2.59 (24h continuous)
Hourly report: [AI-COST] log (1h window)
Hard gate: ANTHROPIC_API_KEY 없으면 RuntimeError → 즉시 HOLD fallback
```

## Key Decision

exit_profile = "liquidation" (TP 1.5%, SL 0.7%, max 30min) 선택 이유:
  - AI confidence > 0.65 = 강한 setup → tight exit으로 빠른 profit lock
  - 모태 실측: 긴 hold가 오히려 AI edge 감소 (market regime shift)
  - 0.14% fee round-trip vs 1.5% TP = 10.7x reward/risk ratio (fee 대비)

## Files Changed

| File | Change |
|------|--------|
| `src/strategies/ai_advisor.py` | NEW — AIAdvisor strategy |
| `tests/strategies/test_ai_advisor.py` | NEW — 27 tests (all pass) |
| `src/paper/realtime_runner.py` | HYPO-AI-001 + primary_tf="ai" branch |
| `requirements.txt` | anthropic>=0.97.0 추가 |
| `vault/40_components/ai_advisor.md` | NEW — component spec |
| `vault/60_alpha/active/HYPOTHESIS-AI-001-*.md` | NEW — hypothesis tracking |
