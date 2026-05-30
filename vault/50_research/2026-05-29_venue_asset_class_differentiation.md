---
type: research
status: active
date_created: 2026-05-29
date_updated: 2026-05-29
tags: [research, venue, asset-class, differentiation, architecture]
---

# 익스체인지/자산군별 차별화 조사 (Jin 우려: "다 묶어서 거래")

## 목표 구성 (Jin 명확화) — 4 상품 클래스
- **OKX 스팟** (크립토, 롱only) — 코드 있음
- **OKX CFD/perp-swap** (크립토, 롱숏, 레버리지) — **코드 없음** (OKX 데모 SWAP 지원, 크립토 숏 가능케)
- **Capital CFD** (FX/지수/commodity, 롱숏) — 코드 있음, 크립토와 성격 다름
- **Alpaca 주식** (US equities, 세션/PDT) — **코드 없음**(env 보관키만). Binance(perp)도 키만.

## 결론
Venue **격리/라우팅은 양호**(전략 venue 배정 안 섞임, universe z-score venue별 분리, learner/cell `exchange` 키 격리, cross-contam 없음). 그러나 **자산군 특성 차별화는 거의 없음**. 핵심 누락 3:
1. **전 7전략 long-only** (`side="long"` 하드코딩) — Capital CFD 숏 어댑터 있으나 **미사용**. 최대 미활용 capability.
2. **수수료/레버리지 사이징 미반영** — 레버리지 production 하드코딩 uniform 30.0(translator는 per-market 읽지만 사이징에 전달 안 됨, disconnect); fee 는 T4 공식에 0, 사후 posterior 귀속에만 venue별.
3. **세션·게이트·universe 랭킹 가중치 자산군 무관 uniform** (OKX 크립토 24/7에도 asia/eu/us 세션; 게이트 8개 asset_class 분기 0건; Capital 내부 FX+XAU 한 z-score 풀 → FX 구조적 불리).

## 차별화 로드맵 (impact 순, file:line in agent report)
1. **[高] 롱숏 활성화** — Capital/tsmom 양방향 숏 신호. 어댑터·side 타입 이미 지원 → 전략 로직만. 中.
2. **[高] per-market 레버리지 사이징 전달** — translator per-market leverage → build_sizer_payload, dead `venue_constraints.leverage_max` 활용. 低.
3. **[中] 수수료를 진입 expectancy 에 반영** (net edge; 9-stack 금지 — base 조정형).
4. **[中] universe 랭킹 자산군별 공식/sub-pool**.
5. **[中] 세션 클럭 venue 인지** (OKX 크립토 세션 비활성).
6. **[低] 게이트 임계/프롬프트 자산군 톤** · **[低] per-symbol/cluster cap 값 차별화**.

## 신규 venue 연동 (어댑터 패턴 `venues/{okx,capital}/`)
- **OKX perp/SWAP**: instId BTC-USDT-SWAP, 롱숏 포지션모드, 레버리지/마진, funding cost, 청산. spot 경로와 공존.
- **Alpaca 주식**: 세션/애프터/PDT, 공매도 borrow, US 장시간 밴드, track 신규.
- **Binance perp**: 숏·funding·per-symbol 레버리지·24/7.
- learner `exchange` 키 이미 있어 신규 venue 자동 격리. **선결: 롱숏(#1)+per-market 레버리지(#2)** 먼저.

Refs: [[2026-05-29_profit_structure_backlog]] · agent report 2026-05-29.
