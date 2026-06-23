---
type: research
status: active
date_created: 2026-06-22
tags: [debate, execution, liquidity, sizing, volume-burst, p3]
---

# Debate — P3 실행/유니버스 계층 (−56R 출처) (2026-06-22)

GPT-5.2 + Gemini-2.5-pro 적대검증. 진단: 실제 ~−266R, volume_burst 80%(스파이크 매수+OKX 알트 스톱 미체결), 슬리피지. edge=상류, 출혈=하류. 원칙: aggressive·flow_not_block(막지 말고 재배분).

## D1 순서 → **수렴: 실행층 먼저 · volume_burst 중간 · 자본재집중 마지막**
- GPT: (3)ATR스톱→(4)트랜치→(1)유동성스칼라→(2)burst→(5)재집중.
- Gemini: (1)유동성등급(센서)→(3)→(4)→(2)→(5).
- 합의: **(5) 마지막**(출혈 외면, 실행 안 고치면 원인 가림) · **(2) 실행 안정화 후**(alpha 변경이라 측정 분리) · 실행(1/3/4) 먼저. 이견은 (1)vs(3) 선후뿐.

## D2 aggressive 보존 → **'축소' 아니라 '정밀 공격'**
- T4 단일 스칼라 `clamp(0.75,1.5, 1.0 + k·(liq_score − median_liq))` — 유니버스 중앙값 상대라 1.0 자동 중심(방어 편향 X). 핵심=**1.5**(깊은 유동성에 더 크게). `E[t4]<1` 장기 쏠림=flow_not_block 위반 경보.
- ATR 스톱: `size=risk$/(ATR×mult)` → R 일정. mult 전략별(burst 타이트 1.5~2.5, micro_reversion 와이드 3~4).
- KPI 3개: `fill_rate · slippage_bps · stop_trigger_to_fill_ms` (재배분 성공 증명).

## D3 volume_burst → **수렴: fade(극성 뒤집기), 격리 아님**
- 둘 다: −45R 신호는 '무의미'가 아니라 '매우 의미'(방향만 틀림). flow_not_block은 전략레벨에도. `IF spike AND failed_follow_through(N) AND structural_resistance THEN SELL`. 단 실행층(3)(4)와 **묶어서만** 배포(fade는 더 어려운 실행).

## D4 빠진 것 → **수렴: 사전 유동성 게이팅 + 실행품질 피드백 + ⚠clamp 버그**
- ⚠ **GPT: `pnl_r −10R clamp`는 손실방어가 아니라 측정/회계 버그** — MAE −34~−100R를 −10R로 캡 → 최적화 전체 왜곡. realized fill 기반 R 재정의 필요. **(P0 측정 항목 추가)**
- 사전 유니버스 게이팅(min vol $5M·depth $20k@2%, **유연**=유동성 회복시 재편입, 영구차단 아님).
- 실행품질 피드백 루프(체결마다 slippage+스톱미체결 → 심볼 유동성등급에 반영 → '함정 유동성' 자동 식별). drift를 실시간 양지화.
- OKX 심볼별 허용 주문타입 매핑(stop-market 가능 여부).

## 확정 P3 플랜 (디베이트 반영)
**A 실행 안정화**: ATR-R 스톱+시장성 스톱(미체결 해소) · 트랜치/TWAP 엑싯+동적 슬리피지 · **pnl_r −10R clamp 제거(P0)** · OKX 주문타입 매핑.
**B 유동성 라우팅**: T4 유동성 스칼라(중앙값 상대) · 사전 게이팅(유연) · 실행품질 피드백 루프.
**C 전략**: volume_burst fade(필터+A 패키지).
**D 마지막**: 자본 재집중(KPI 개선 확인 후).
선행: **P0(측정)·P1(사이징 가시성) 후 P3 빌드.** 관련: [[structural_roadmap_2026-06-22]] · [[system-architecture-map]]
