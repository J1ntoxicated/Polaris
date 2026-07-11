---
type: research
status: active
date_created: 2026-07-11
tags: [backgate, brain, g8, learners, ai-judge, gpt, tokens]
---

# Design — Brain·AI 유닛 소화: G8 · 러너 · Judge (설계 전용, 코드 0)

DEMO/PAPER 가상계정 · 봇 내부 LLM = OpenAI GPT만 (gpt-5-mini P0 / gpt-5.5 P1).
**GPT 콜 증가 0 주장은 W1–W4 한정** [R1-B3] — W5 에스컬레이션만 예외 (아래).

## 컨텍스트 주입 (W2, behavior 0)
- `_frontgate_line()` — judge payload에 척후병 요약 1줄 (부재 = "n/a" 패턴, 절대 차단 X).
- #3 랭크 컴포짓 → judge payload 컨텍스트, **G1 한정** — 사이저 무접촉.
- regime v2 → AI 컨텍스트 = flip 사다리 **①단계** (최선행 소비자).

## 토큰 예산 [R1-B3, W4 flip 체크리스트 편입]
- 신규 콜 0이어도 기존 콜 비용 증가 경로 실재: judge payload 팽창(rank + regime_v2 +
  _frontgate_line) + G8 레슨 barrier 성장. **필드별 token cap + payload diff 예산** 명문화.
- 입력 팽창분 **파싱 절단율 섀도우 카운트 선행** (실측 10.7% 악화 방지) — 리스크 톱3-③.

## mini→5.5 2단 에스컬레이션 (W5, 조건부)
- W2: mini 판정 섀도우 카운트만 → C-predicate 정의. W5: 섀도우 불일치율 실측 후 실배선.
- 실제 콜 증가 경로 — **발동율 상한 + 비용 캡 게이트** 전제로만 착수 [R1-B3].
  (5.5 = 기존 승인 P1 티어 — 원칙 위반 아님, 주장 범위 오류만 정정.)

## ai_feedback 러너 + G8 레슨 barrier 업그레이드 (W5)
- 전제 = **#4·#10 동시 승격 후** — ai_lessons 615행 write-only 전례(paid no-op)의
  소비자-선행 조건. 읽는 주체 없는 컬럼/로그/프롬프트 라인 증식 금지.
- 러너 5축 편입·히스테리시스·churn = W4 flip R2 /debate 의제 (canon).

## regime v2 러너 키 (flip 사다리 ②)
- `regime_mult_v2` — ① 활성 이후 독립 순차. 승격 채점 = flip 이전 축적분(frozen
  baseline)만 사용 + post-flip 행 ladder_stage 스탬프 [R1-B4] (모집단 오염 차단).

## 검증
- W2: 프롬프트 diff 스냅샷 + 라이브 판정 분포 무변화 확인 (컨텍스트 추가 전후).
- W4: 토큰 예산 체크 + flip 전후 judge verdict 분포 A/B.
- 뉴스 conviction의 judge 경로(SIZE_UP→judge_conviction)는 사이저 이중 가산 가드와
  연동 — news_scalar↔judge_conviction 상관 상시 감시 [R1-B2]. → [[design-sizer]]

실코드 근거: `polaris/core/pipeline/agents/{ai_judge,post_trade_reflector}.py` ·
`polaris/core/learners/`.
관련: [[master-sequence]] · [[design-regime-v2-rollout]] · [[design-monitoring]]
