---
type: debate
status: resolved
date_created: 2026-06-01
date_updated: 2026-06-01
tags: [debate, p3, self-evolve, reframe]
---

# Debate — P3 Self-Evolve 설계 (2026-06-01)

**Topic**: P3 self-evolve 설계 spec (`.claude/plans/p3_self_evolve_2026-06-01.md`) 충분한가? / 누락·과적합·과설계·mandate 위반? / 확정 or 보강?
**Sources** (Jin이 이 1건에 한해 `feedback_no_dev_gpt` 명시적 해제): Claude 6-lens 적대 디베이트 + codex 외부 리뷰.
**Verdict**: Claude = **RECONSIDER** (렌즈 verdict weak/broken/weak/adequate/weak/weak) · codex = **CONFIRM_WITH_CHANGES** → 수렴: **"장치 전에 edge 먼저"**. Jin 수용 → spec REFRAME.

## 수렴된 핵심 (코드 검증)
- **alt-data가 전략에 0 도달** — funding/OI/COT는 게이트만 소비, MarketView=고정 TA ~15개. config 변이는 이미 FAIL 난 피처 공간 재탐색 = "약한 전략 재발견". (없는 edge는 생성기도 못 만듦.)
- **"전략=config dict"는 얇은 seam 아님** — config 구동 전략 런타임 부재 = greenfield 서브시스템.
- **검증 스택 greenfield + 굶음** — honest-N 레지스트리/CPCV/corr-dedup 미구현, 단일후보 시 DSR=PSR 자가비활성, walk-forward OOS 미게이팅. 전역 FAIL 데이터서 per-cell DSR≥0.95 거의 불통(게이트가 0 승격).
- **과설계** — RAG 의미검색·키퍼 T1/T2·3축 형식화·밴딧·C1 라이브 = edge 증명 전 defer (Jin "상품 아님" steer 일치).
- **mandate 모순** — amplify-only floor 1.0 ↔ 기존 0.5/0.8 dampen / C1 residual이 score-input인지 새 mult인지(9-stack) / sizing_hint 변이 / veto 어조 → 수정.
- **exit 진화 누락** = surgical-strike thesis 자체인데 live exit recompute가 dead stub.

## 데이터 caveat (검증)
profit-skeptic "net -$4,382 / fees 3.2x / 19min churn"은 **stale `data/polaris.sqlite`(5/6~10, simulate-only 가능)**서 나옴. 라이브 `data/polaris_live.sqlite`=973 fills(too small). **수치 미채택** — fee/churn 분석 부재·구조적 우려만 유효. 부모 "+1.18R n=87"은 _NOW 정직값 "+0.07R, p_pos<0.5"와 모순(정당화는 정직값에).

## 결정 (REFRAME, Jin 2026-06-01)
**증명 먼저**: P0a KILL-스파이크(기존 config 변종→기존 게이트 오프라인 통과율, 🚦~0이면 피처가 병목) · P0b fee/churn 켜기 + exit recompute 배선 + 키퍼 T0 · P1 alt-data→MarketView 피처 · P2 생성기(증명 시) · DEFER(RAG·키퍼T1T2·3축·밴딧·C1 라이브).
SSOT = `.claude/plans/p3_self_evolve_2026-06-01.md`. 관련 `project_self_evolving_vision` · `project_ai_conductor_direction` (memory) · [[ai_conductor_transition_2026-05-30]] · [[MOC-A1-design-dev]].
