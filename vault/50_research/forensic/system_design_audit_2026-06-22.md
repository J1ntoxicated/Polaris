---
type: forensic
status: active
date_created: 2026-06-22
tags: [forensic, audit, metrics, measurement, regime, exit, data-quality, root-cause]
---

# 시스템 설계 타당성 감사 — 종합 (2026-06-22)

8-서브시스템 멀티에이전트 감사(wnmnilw3g, 9 agents) + 회의적 종합. Jin 의문 "메트릭스 이상·설계 맞는지" 근본검증.

## 결론
**아키텍처(8-Layer·8-gate·StreamConfig·AI-free·T4 9-stack 봉쇄)는 sound, 설계대로 wired.** 깨진 건 골격이 아니라 **입력 데이터·측정·청산보호·튜닝**. 증거: **Capital FX(실 바·체결가능)는 같은 프레임워크로 avg −0.05R**(거의 정상) → 결함=data+venue-fill+measurement, NOT 아키텍처.

## "메트릭스 이상"의 정체
- **~70% 측정/단위 결함**: ① 같은 거래 R 3개(dashboard $10 / confidence $50 / positions ATR 분모) ② **R이 venue 공통 단위 아님** — entry ATR timeframe venue별(alpaca 1D 0.18 / okx 0.016 / capital 0.0007 = ~250x), cross-venue R 합산(−266R)=분봉R+일봉R 더한 무의미 ③ **reconciled(추적실패) mae를 realized로 합산**(−281R 중 −211R) = '거래손실' 아닌 '추적실패' 오기록 ④ ±10 클램프(unrealized 결정경로)가 −34~−100R 손실 은폐.
- **~30% 정직한 손실**: fills.$ ledger(capital +$431 / okx −$646 / alpaca −$1881) 일관·정직. PF 0.39 실제로 나쁨.
- ⚠ 두 장부 다른 bleeder(R뷰=BNT/ADA/FLOKI vs $뷰=SPCE/SBEV/INJ).

## 손실 진짜 원인 (정직 $ 기준)
1. **입력 데이터** — OKX 1m 73% 합성(flat/zero-vol) 위 지표 + volume_burst 스파이크-탑 매수. Alpaca 피드 **사망**(WS 0틱, 4-10일 stale).
2. **청산 보호 부재** — venue-resting 스톱 없어 OKX 알트 5s 틈 관통→미체결→orphan(−1R→−34~−100R).
3. **튜닝 무력화** — session='asia' 하드코드, regime **24h 1233 flip-flop**(틱 케이던스가 히스테리시스 무력화).

## ⚠ 자기 정정
이전 P0.4(±100 클램프)+fix#1(reconciled mae를 pnl_r로 합산)이 −211R 아티팩트를 **키웠음**(추적실패를 손실로 기록). 감사가 잡아냄 — builder≠reviewer 가치 입증. → R 재정의 시 reconciled mae 제외.

## Jin 결정 (2026-06-22 락인)
①Ledger=fills.$ 진실 + positions.pnl_r 재정의(스트림 공통 R, reconciled 제외→drift 카운터) ②Alpaca=equity 신규중단+복구 ③거동변경=안정화 바로/트레이딩 param /debate.

## 실행 프로그램 → [[structural_roadmap_2026-06-22]] 대체, loop_state.md 참조
M 측정 재설계 → S 안정화(스톱/regime bar-close/Alpaca guard/OKX 합성바 필터) → D /debate(crisis·session·틱엔진·burst fade) → R 리셋(고친 메트릭) → 깨끗한 측정.

관련 [[_NOW]] · [[ADR-003-8-layer-architecture]] · [[feedback_root_cause_evidence_based]].
