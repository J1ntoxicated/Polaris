---
type: debate
status: resolved
date_created: 2026-05-30
tags: [debate, architecture, ai-conductor, cost]
related: [[ai_conductor_architecture_2026-05-30]], [[north-star]], [[2026-05-30_ai_validity_audit]]
---

# /debate — AI 아키텍처 "technical-decides + AI-conducts" 전환

**합주(5 관점)**: Claude 4-lens 적대검증 Workflow(surgical-precision·evidence-based·aggressive-mandate·conductor-sufficiency) + codex external review. **결론 = PROCEED_WITH_CHANGES.** 전 gate fully technical-replaceable, reject 0, blocking 3개 양측 완전 수렴.

## 5항목 판정 (Claude ∩ codex)
| # | 항목 | 판정 | 핵심 |
|---|------|------|------|
| 1 | net_edge_r KILL/MODIFY gating 금지 | **확정 금지** | `net_edge.py:22` 자기부정("placeholder, NOT alpha, Do not trade off"). 대체 KILL은 **warm cell에서만** narrow. |
| 2 | G3 fail-closed→fail-open | **AGREE** | G3만 바꾸면 KILL이 G4(`pre_entry_watcher.py:196`)로 이동, 순효과 0 → **P4(G4) → P5(G3) hard prerequisite**. |
| 3 | deterministic 임계 캘리브 | **수정** | cell 7/8 cold(n_eff<5) → **cold cell = KILL 금지(pass-through)**, warm만 KILL. cell-독립 booster 필수(optional 아님). |
| 4 | conductor 주기 + regime classifier | **AGREE** | `regime_flip.py:42` classify_regime = P0 stub(caller 그대로). 실 classifier(4h EMA20/50+24h ATR+5m-1h eff)가 **P0 안**에 선행. |
| 5 | aggressive/9-stack 정합 | **수정** | G3 MODIFY scalar[0.5,1.5]→continuous_scalar 단일 mult = 9-stack OK(`engine.py:124,241`). |

## 🔴 BLOCKING 3 (Claude=codex 완전 수렴)
1. **regime classifier 미구현** → conductor는 실구현 전까지 shadow-only. 실 classifier가 **P0**(P1+ deferred 금지).
2. **G4 no-client fail-closed** → G3만 fail-open 무의미. **P4(G4 deterministic) → P5(G3 fail-open) hard gate.**
3. **live cell matrix 거의 전부 cold**(7/8 or 6/6 n_eff<5) → cell-only KILL 발동 불가. cell-독립 booster + cold=pass-through 필수.

## codex가 강화한 교정 3 (Claude 설계 수정 필요)
- **A. cold cell = KILL 절대 금지(pass-through)** — aggressive mandate. "모호하면 통과", warm cell에서만 narrow KILL.
- **B. G4 realized-vol KILL 추가 금지** — "expanding vol=기회" thesis 충돌 + 기존 vol-aware sizing(`vol_target.py:57`) 중복. realized vol은 continuous scalar/conductor 톨러런스만 조정, blanket block 절대 X. (Claude aggressive-lens도 우려 → codex 확정)
- **C. G4 spread도 KILL 아니라 MODIFY/flag default** — 현재도 fast-path eligibility일 뿐 KILL 아님(`pre_entry_watcher.py:111`). shadow 캘리브 후에만 KILL 승격.

## phased 확정 (AGREE-WITH-CHANGES)
P0 shadow(+**실 regime classifier 구현**, 복수 레짐 윈도우 acceptance gate) → P1 G1 ranker → P2 G8 → P3 G6 → **P4 G4(→P5 hard gate)** → P5 G3(최엄격, cold=pass-through lock) → P6 conductor.
hard 제약: ① regime classifier P0 내 구현 ② P4→P5 enforced ③ cold-start KILL=pass-through를 P5 live 전 lock(미준수 시 silent aggressive 위반).

## Jin 결정 위임 항목 (없음 — 5 관점 수렴, PROCEED_WITH_CHANGES)
codex agent=aade86e7f91ea717e (재호출 가능). 다음 = plan 교정 A/B/C 반영 → P0 빌드(shadow + regime classifier).
