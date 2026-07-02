---
type: research
status: active
date_created: 2026-07-02
tags: [audit, ai-judge, probe, gpt]
related: ["[[ai-hooks-audit-verdict]]", "[[ADR-004-per-gate-ai-pipeline|ADR-004]]", "[[layer-6-live-recalc]]"]
---

# AI judge 프로브(#32) 실상

> gpt-5-mini, mode=active, 3일 3,139콜. 결정적 폴백 보장. [[ADR-012-probe-engine-tuning-log|ADR-012]] 계열.

## 살아있는 것 (엔트리)
- SIZE_UP 166건 전량 G5 도달, 115건 continuous_scalar 0.75→0.8625(×1.15 지문) — **실사이징 변경 확인** ([[layer-3-sizing-risk]])
- Anthropic 런타임 라우팅 0 확정 — 유일 LLM = `_gpt_client.py` httpx→api.openai.com (mandate 준수)
- P1=gpt-5-mini는 의도적·문서화된 결정(Jin 2026-05-31, `_gpt_client.py:55-61`) — CLAUDE.md 'gpt-5.5 P1' 문구가 구버전

## 죽어있는 것
- **G7 EXTEND/TIGHTEN 0콜 ever**: recalc payload cell_routing 부재(warmth=0, -0.30) + ground label 희소(3698행 중 7행, fuser CONVICTION_FLOOR 미달 시 미기록 → consistency=0, -0.15) → robustness 최대 **0.41 < robust_min 0.50** (`adaptive_exit.py:382-385`). rail 배선(:409-492)은 정상 구현이나 미실행
- **REFINE_TIMING 1,487건(판정의 16%) 완전 INERT**: `ai_judge.py:563` 스탬프뿐, repo 전체 소비자 0
- **gpt_parse_fallback 10.6%** (984/9,327): MAX_TOKENS=180 truncate → 비용만 지불·정보 0
- **token 미persist 완전 소실**: GateResult 재조립(`ai_judge.py:571-581`)에서 대체, 스키마에 열 없음. latency는 rotate 로그로 복구 가능(07-01 307건, 2129~5106ms)
- **timeout 비상한**: httpx per-op 8s ≠ wall-clock → 성공콜 4.7%가 8s 초과, max **495s** 게이트 점유

## H1 판정 = 부분 (엔트리 반증 / 엑싯 확정 / REFINE INERT)

관련: [[ai-hooks-audit-verdict]] [[gate-pipeline-value-audit]]
