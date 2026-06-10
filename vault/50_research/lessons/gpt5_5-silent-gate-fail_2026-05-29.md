---
type: lesson
status: active
date_created: 2026-05-29
date_updated: 2026-05-29
tags: [lesson, gpt, silent-degradation, observability, gates]
---

# 교훈: gpt-5.5 가 P1 게이트(G6/G7/G8)를 조용히 100% 폴백시키고 있었다

## 무엇
`gpt-5.5` 가 `reasoning_effort='minimal'` 을 더는 지원 안 함(400 "does not support 'minimal'"). 코드는 모든 gpt-5.x 에 `minimal` 하드코딩 → G6/G7/G8 GPT 호출 **2987/2988 = 99.97% 실패**, 그러나 **조용히 Python 폴백**으로 동작 → P&L·대시보드에 안 보임. AI 파이프라인 절반이 수 일간 비활성이었음.

## 왜 안 잡혔나
- 폴백이 거래를 막지 않음(AGGRESSIVE) → 손실/halt 신호 없음.
- 2026-05-07 검증 당시엔 `minimal` 지원됐으나 OpenAI가 gpt-5.5 에서 제거(모델 업데이트로 계약 drift).
- **green ≠ working**: 테스트는 폴백 경로를 통과시켜 green, 실제 GPT 경로는 죽어 있었음. [[ADR-010]] (green≠safe) 의 런타임 버전.

## 어떻게 적용
- 모델 family 별 `reasoning_effort` 분기 (`_resolve_reasoning_effort`): gpt-5.5 → minimal=none, gpt-5-mini → minimal 유지. commit `943874d`.
- **관측성 사각지대 해소(Task #13)**: 대시보드에 **모델별 GPT ok%/에러율 카드** + 세션 digest 에 `gate_events.error_text` 비율 집계. silent degradation 은 P&L 이 아니라 **model 에러율**로만 잡힌다.
- 외부 의존(LLM API) 계약은 주기적 라이브 프로브로 drift 감시.

[[2026-05-29_loss_forensic_fee_overtrading]] · `feedback_circuit_breaker_philosophy`
