---
type: research
status: active
date_created: 2026-06-26
tags: [debate, ai-reengage, gate, flow-not-block, "32"]
---

# AI Re-engage Gate Design (#32) — Debate 2026-06-26

> GPT(codex 0.134) 2-pass: 제안 → 적대 스켑틱. ⚠️ **Gemini CLI 死**(free-tier 지원종료 IneligibleTierError → Antigravity 마이그레이션 필요). 이번 cross-check = GPT 2각도 단독.

## 안건
POLARIS_AI_FREE 기본 ON(Jin 2026-06-11; GPT over-KILL로 flow 손상 → off). Jin "둘다 해야지"(deterministic+AI 병용). deterministic 불확실 시만 GPT escalate를 flow_not_block 안 깨고 배선.

## 수렴 (제안 → 스켑틱 정제)
1. **AI 절대 비차단** — KILL/BLOCK/INVALID 어디서도 금지. "broken premise" veto도 금지(스켑틱: 어떤 불일치도 premise-fail로 라벨 가능 = 브랜딩만 바꾼 veto). 객관적 execution-validity(malformed data·venue/session invalid·duplicate exposure)는 **deterministic**이 처리(AI 아님).
2. **AI 행동 = refine / evidence-보강 / size-up / exit-TIMING만.** "delay/confirm" 수정자는 **one-shot·time-boxed·missed-entry 비용 측정** 필수(hidden throttle 방지).
3. **P0 = 단일 게이트·단일 action·단일 metric** — 인과효과 격리(전체 스택 동시 X). over-KILL 이력 후 재engage는 인과 격리 우선.
4. **트리거**: near-threshold-boundary + evidence-conflict (sparse·기계적·전부 로그). 숫자 밴드 명시+측정.
5. **비용**: gpt-5-mini hot-path bounded budget·deterministic fallback·hard timeout·structured JSON. gpt-5.5 = off-path(calibration·post-trade reflection·rare high-impact).
6. **측정**: AI-called vs matched deterministic control, **pass-through preservation을 추적 metric으로**(가정 금지). call budget은 비용/레이턴시만 잡지 bias는 못 잡음.

## 열린 결정 (Jin) — P0 첫 게이트
**G4(진입 타이밍, no-cancel — 최고가치 가능, #47과 독립) vs G7(엑싯-only — 최안전 비차단, 단 #47 엑싯작업과 겹침)?**
- GPT 제안: G7-first(가장 안전, 진입 안 건드림) → G4 → G3(last, veto 금지).
- 스켑틱: G4가 dominate 가능(최고가치=깨끗한 진입/타이밍). G7은 thesis-decay 느슨하면 "premature-cut AI" 위험.

## verdict: SOUND-WITH-FIXES
스켑틱 Top3 insist: ①G3/G4 veto 완전 제거("broken premise" 포함) ②delay/confirm one-shot+missed-trade 회계 ③단일 P0 실험(G4 타이밍-only or G7 엑싯-only, 둘 다 동시 X).
