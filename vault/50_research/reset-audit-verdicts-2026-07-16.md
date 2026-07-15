---
type: audit
status: verdicts-recorded
date_created: 2026-07-16
tags: [reset, audit-verdicts, built-not-wired, harness, dead-code, knowledge]
---

# 리셋 감사 판정 요약 (wf_c7762fe8, 4축 — 전문은 워크플로 저널)

## built-not-wired (핵심)
- 🚨 **P0a evolve 승격게이트 자체 미가동** (`polaris/core/evolve/` 1604 LOC, p0a_registry 부재)
  → 전략 2종(cfd_fx_range_fade_short·okx_funding_carry_persist) 영구 INERT. **병목=게이트 미실행.**
- tick엔진(P5, 1500+ LOC) `TICK_ENGINE_ENABLED=0` 무발화 — 섀도우조차 없음. 재평가(P3).
- conviction_pyramid: shadow write 자체가 미기록(0행) — 소비자 배선 재확인(P3).
- maker_fill_shadow: virtual=ON인 한 설계상 0행(구조 락) — real-wire 전 불가침.
- sector_rank_shadow 0행 = 월경계 미도달(버그 아님, 8월 재확인).
- r_budget(이미 self-flip 승격됨)·ai_judge #32(.env로 이미 active) = 오탐 방지 기록.

## harness (랜딩 완료 2026-07-16)
- 게이트-에이전트 8종 vestigial CONFIRMED → `.claude/archive/agents-pergate-2026-07-16/`.
- auto-invasion projects 고아사본 → archive. superseded plans 6 → archive. 1회성 plans 15 = 보류.
- skills 8: 존재 확인, 실사용은 세션로그 필요(미확증). `.agents/skills` 미러 divergent — 단일화 결정 대기.
- ADR-014 graph = vaporware 확정 → status: designed-not-implemented 정정.

## dead-code (놀랍도록 깨끗)
- 삭제후보 = ansi_palette.py 고아 헬퍼 8함수 + _4h_backtest.run_variant 1건뿐 (P0 머지 후 처리).
- "unreachable" 13모듈 전부 의도적 standalone/문서화된 KILL — keep.
- dashboard_v2.py는 살아있음(server.py:60 import) — 오판 방지 기록.

## knowledge (랜딩: lessons 73 삭제, 12 keep)
- vault_lint: error 22(50_research frontmatter 누락 — P6 quick-fix), wikilink 깨짐 114.
- digests 06-22~30 10개 → 주간 롤업 압축 후보(P6). weekend 문서 11 = 전부 keep(음성결과 가치).
- _NOW.md 07-08~15 공백 → 2026-07-16 현행화 완료.
