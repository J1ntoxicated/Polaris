---
type: plan
status: superseded
superseded_by: [[system_design_audit_2026-06-22]], loop_state.md
date_created: 2026-06-22
date_updated: 2026-06-22
tags: [plan, structural, roadmap, measurement, sizing, execution]
---

> ⚠️ **SUPERSEDED (2026-06-22)** by [[system_design_audit_2026-06-22]] + `.claude/loop_state.md`.
> **헤드라인 −266R/−209.7R은 대부분 측정 아티팩트**(cross-venue R 합산 무의미 + reconciled-mae를 realized로 오기록 −211R) — 감사 자기정정 참조. '거래손실'이 아니라 '추적실패' 카운터. 정직 $ ledger: capital +$431 / okx −$646 / alpaca −$1881.
> 실행 프로그램은 이 문서가 아니라 **loop_state.md의 M→S→D→R**(측정 재설계→안정화→/debate→리셋)로 대체됨.

# Structural Roadmap — what to look at / fix (2026-06-22) [SUPERSEDED]

근거: 세션 종합(diagnosis · 운영봇 리서치 · correlation · okx-else 스윕 · LIVE-1 진단 · drift 백필). **한 줄: 상류(regime/AI/evidence/유니버스 발굴)는 정교하고 앞섬. 약점은 하류 3층 — ①P&L 못 봄 ②실제 크기 모름 ③실행에서 샘.** 새 기능 전에 하류를 단단히.

**~~충격 수치(2026-06-22)~~ [REVISED]**: ~~drift 백필로 숨은 손실 −209.7R / 130건~~ → 감사 정정: **−209.7R 중 −211R분은 측정 아티팩트**(reconciled mae를 realized로 합산한 오기록). drift는 '손실'이 아니라 **별도 추적실패 카운터**. 측정 정직화가 왜 최우선인지의 증거(방향은 유효, 수치는 재정의됨).

분류: **[BUILD]**=버그/텔레메트리/정리(TDD+적대검증 builder≠reviewer) · **[DEBATE]**=sizing/strategy 트레이딩파라미터(/debate GPT+Gemini 먼저→결정→BUILD).

## P0 — 측정 정직화 (최하부; 거짓이면 위 판단 다 거짓)
- 0.1 [BUILD·~~DONE~~ **REVERTED/SUPERSEDED**] ~~reconcile drift→pnl_r stamp + 백필 130건(−209.7R)~~. **감사가 이 백필을 아티팩트로 판정** — reconciled mae를 realized R로 합산해 R을 −211R 부풀림. M 재정의로 **realized R ledger에서 제외**(reconciled = 손실 아닌 **별도 drift 카운터**). 자기정정: builder≠reviewer가 잡아냄.
- 0.2 [BUILD] PF/WR/confidence/digest가 `fills.pnl_usd`만 봄 → reconciled drift를 **별도 카운터로 surface**(realized P&L에 합산 X — M 정의 준수). 봇이 제 손익 + drift를 각각 보게.
- 0.3 [BUILD] per-ticker/strategy/regime/session 귀속 대시보드 노출(BLEED 완료, ticker-level 추가).

## P1 — 사이징→노출 가시성 (LIVE-1 판단 전제)
- 1.1 [BUILD] 사이징→노출 경로 계측: `compute_size` notional vs 실제 venue 표현 notional(lots×price) 로깅·대조. '의도 vs 표현' 갭 가시화.
- 1.2 [DEBATE] LIVE-1 tick-engine leverage SSOT — 1.1 가시성 위에서 올바른 sizing 의미 결정 ($41k/자본52% 우려, venue-translation leverage-inert 엉킴).
- 1.3 [DEBATE] two-producer(bar/tick) 사이징 정합.

## P2 — 멀티스트림 SSOT 강제 (2→3 leaf)
- 2.1 [BUILD] `_production_bars.py:412` baseline asset_class를 focus 튜플에서(venue 재유도 제거). 정규화, sizing 무관.
- 2.2 [BUILD] `_production_layers.py:395` `or "crypto"` → group_id prefix 폴백.
- 2.3 [BUILD/DEBATE] `cluster_cap.py` equity 클러스터 정의(StreamConfig 선언했으나 누락).
- 2.4 [DEBATE] Capital equity-CFD whitelist 67종 포함 여부(현재 영구 미거래).
- 2.5 [BUILD] CI 테스트: StreamConfig 선언키(cluster_id/asset_class) ↔ 소비테이블 키 정합.
- 2.6 [BUILD] asset_class Literal/enum 폐쇄(신규 클래스 시 모든 분기 mypy 강제 갱신).

## P3 — 실행/유니버스 품질 (−56R 출처)
- 3.1 [DEBATE] 유동성 등급 Layer-0 사이징(차단 아닌 변조).
- 3.2 [DEBATE] volume_burst 극성 뒤집기(exhaustion-aware) — 손실 80%.
- 3.3 [DEBATE] ATR 정규화 스톱(저유동 알트에서 체결되게).
- 3.4 [DEBATE/BUILD] 분할 엑싯(TWAP/트랜치) + 동적 슬리피지.
- 3.5 [DEBATE] 검증된 흑자(Capital 지수/원유·micro_reversion) 자본 재집중.

## P4 — 테스트/프로덕션 신뢰
- 4.1 [BUILD] pre-existing `tick_engine` burst→order 테스트 실패 조사+수리(틱엔진 프로덕션 작동 확인).
- 4.2 [BUILD] smoke-vs-production 위장 정리(SSOT 통일 또는 죽은 smoke 삭제).

## P5 — 측정 토대 (edge 검증)
- 5.1 [BUILD] replay/backtest 하네스 가동(Jin: 참고용 — 가격 스켈레톤 edge 검증, replay_runs 0→).
- 5.2 [BUILD/DEBATE] `signals.correlation_group` populate + regime cross-asset evidence(flow_not_block 신호/사이즈 가중).

## P6 — 운영 위생
- 6.1 [DECISION] `/Users` vs `/Volumes`(마운트됨) canonical 확정 + stale `__pycache__` 정리.
- 6.2 [BUILD] stale docstring(2-venue·smoke_paper_loop 참조) 정리.

## 실행 원칙·순서
순서 = **P0→P1(가시성)→P2→P4→P5→P3→P6**. **1·2가 맑아지기 전 P3 sizing 변경 금지**(거짓/불투명 위 fix = '고친 줄 알지만 모름'). 각 BUILD=TDD+적대검증, 각 DEBATE=/debate 먼저. 관련: [[system-architecture-map]] · [[research_agent_mesh_2026-06-22]]
