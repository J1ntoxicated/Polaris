---
type: research
status: built-reviewed
date_created: 2026-06-25
date_updated: 2026-06-25
tags: [research, g2, strategy-assignment, regime, ticker-tailored, flow-not-block, layer-2]
---

# G2 strategy assignment → ticker-tailored (regime-first hybrid)

DEMO/PAPER · AGGRESSIVE · flow_not_block · 9-stack 봉쇄 · in-loop GPT=0. builder≠reviewer.
backlink: [[strategy_vs_execution_partA_2026-06-24]] · [[ADR-008-7-strategies-signal-generator-role]] · [[ADR-006-cell-matrix]] · ADR-003 (Layer 2).

## 갭 (감사 CORRECT지만 Jin 원칙 갭)
G2 emit 루프(`_production_tick.py`)는 전략↔티커를 **venue/asset_class로만** 매칭(static) — 티커의 live regime이 emit 우선순위에 미반영. momentum 전략이 chop 티커에 full conviction emit. per-ticker-tailored 갭.

## /debate 합의 (GPT a68dfca2 + Gemini a55e65bd 수렴)
**(c) regime-first hybrid**: base set → 전략별 regime fitness 사전매핑 → 티커 live regime → 적합 POOL 동적할당 → 풀내 perf-rank/learned 보조 미세조정. 함정: perf-rank 단독(과적합/whipsaw), hard-exclude(flow_not_block 위배), size-mult 우회누적(9-stack), black-box, cold-start 소외.

## 설계 결정 (load-bearing — assignment ≠ sizing input)
- 기존 SSOT 재사용: `cell_matrix/score.py::regime_alignment_mult(strategy, regime)` (amplify 1.25 / neutral 1.0 / dampen 0.8, never zero) = 이미 존재하는 "전략별 강점 regime 사전매핑". 새 fitness 테이블 X (black-box 회피).
- **채널 = rank_penalty (PRIORITY only), NOT strength/size.** strength→T4 scalar이고 regime_fit이 이미 그 scalar를 shrink하므로, regime을 strength에 넣으면 **이중계산=숨은 9-stack ≤1 stack**. 그래서 sizing 경로 절대 미접촉. (Jin 원칙 "focus/assignment는 사이징 입력 아님" 직접 충족.)
- 신규 `regime_rank_penalty(strategy, regime)` = mult의 **non-negative 역인코딩**: amplify→0.0, neutral→0.10, dampen→0.20. PDT step(1.0) 미만 → PDT 무결성 우선, regime은 2차 정렬키. cold/crisis/unknown→NEUTRAL(0.10, 최악 tier 아님 — cold-start 소외 회피).

## 효과 (정직 — overstate 금지)
lower penalty → `order_specs_by_rank` 더 앞 → supervised TaskGroup에서 코루틴 **먼저 생성** = concurrent gate fan-out 진입 **head-start**. **보장된 risk-budget 예약순서 아님**(게이트 await interleave → 예약은 gate-completion 순). best-effort 우선순위 nudge. 1차 적대리뷰가 "first claim on budget" 과장을 HIGH로 적발 → honest rewording(1a)으로 수정(예약경로 동기화=1b는 budget/cap 아키텍처 변경, 별도 /debate). 차단/사이즈컷/드롭 0 (flow_not_block).

## 구현 (file:line)
- `polaris/core/cell_matrix/score.py` — `regime_rank_penalty` + `REGIME_RANK_PENALTY_NEUTRAL/DAMPEN` (+__all__).
- `polaris/scripts/_production_tick.py` — import + emit루프: `regime_penalty = regime_rank_penalty(strategy_id, regime)`; `rank_penalty = pdt_penalty + regime_penalty` → `PipelineTaskSpec`.
- 테스트: `tests/test_cell_regime_alignment.py`(헬퍼 6+property), `tests/test_g2_ticker_regime_rank.py`(POOL 정렬/per-ticker tailored/PDT 우선/teeth via 실 supervise_pipeline_tasks/source lint).

## 검증 / 리뷰
71 tests pass · mypy --strict clean(touched source) · ruff clean. fresh-Claude 적대리뷰 → 최종 verdict 반영. 불변: 9-stack(sizing 미접촉, notional rank-불변) · flow_not_block(드롭/사이즈컷 0) · aggressive(better-fit 먼저, mis-fit 여전히 flow) · 거부키워드 0 · per-ticker(동일 전략·다른 티커 regime → 다른 rank).

## 후속 (open, 미적용)
- regime fitness 학습화(현 deterministic mult → learned per-ticker×strategy): cell routing/posterior가 이미 learned 미세조정 담당 → 중복 회피, 별도 /debate.
- 1b(rank순 동기 budget 예약 = 진짜 teeth): budget/cap 경로 변경 → /debate flag.
- cold/insufficient-confirm vs confirmed chop 구분(현 "chop" default conflation, 리뷰 LOW=acceptable).
