---
type: research
status: built-reviewed
date_created: 2026-06-25
tags: [multi-horizon, entry-routing, swing, okx-spot, carve-out, flow-not-block]
---

# 멀티-호라이즌 STEP1 — OKX SPOT 바-경로 carve-out (entry 라우팅)

## 문제 (라이브 실측 polaris_live.sqlite)
51 closed 전부 <60분(max 1690s=28분), swing/position **0건**. OKX 1H 스윙전략
(tsmom/supertrend/spot_donchian/ema_crossover, 전부 `venue=okx asset_class=crypto
timeframe=1H`, 심볼 화이트리스트 없음=전체 universe) 진입·emit 0건. 유일 바-발화 =
session_breakout(Capital US100, carve-out 심볼). 장벽: 틱엔진이 OKX/Capital 소유
(`TICK_ENGINE_OWNED_VENUES`), `keep_on_bar_path` carve-out가 forex+Capital
index/commodity만 커버 → OKX SPOT 전체가 틱엔진에 양도되어 `generate_raw_signal`
미도달(`_production_tick.py:478` 게이트 `not keep_on_bar_path` → continue).

## 변경 (load-bearing 1줄 widen)
`keep_on_bar_path`(`_production_tick.py:118`): `asset_class ∈ {forex}` →
`{forex, crypto, spot}`도 바 경로 유지. OKX SPOT = 정확히 `crypto`(라이브 universe
219행 전부 crypto; Capital엔 crypto/spot 0건이라 누수 없음). 이제 OKX 1H 스윙이
바 파이프라인에서 발화, 틱엔진은 같은 심볼 스캘프 계속 = 상보 edge 공존. exit/sizing
무접촉.

## 디베이트 5 안전장치 검증 (전부 **기존 인프라가 이미 충족**)
1. **no blind netting** — `concurrent_same_side_open`(reentry.py:154) strategy-scoped
   (`WHERE strategy_id=?`) + order_key가 strategy 포함 → 틱-스캘프·바-스윙 독립 논리
   포지션·독립 PnL/exit. + OKX SPOT **long-only**(streams/config)라 강제반전 물리적 불가.
2. **호라이즌 risk 예산** — `per_symbol_remaining_pct`(engine.py:306) 두 producer
   합산(strategy-agnostic). 명시 60/40 split은 별도 increment(자원잠식 튜닝, 디베이트값
   필요). per-symbol cap 합산만으로 STEP1 안전.
3. necessary-not-sufficient — exit_engine.py:391 thesis_cut top-of-ladder + G6 -1.0R
   rail 불변(범위 밖, spot-check OK).
4. trail_mult 동적 — exit 쪽, 이번 범위 밖(미접촉).
5. regime 히스테리시스 — `detect_regime_flip` 2-close confirm + bar-close dedup
   (regime_flip.py:32) **이미 존재·테스트됨** → ⓪ 빌드 불요.

## 북극성 정합
flow_not_block(skip 좁힘=도달성↑, block/size-cut 0) · 9-stack(sizing 무접촉) ·
비대칭(exit 무접촉, 손절 rail 불변) · aggressive(스윙 위너 흐르게). 거부키워드 0.

## TDD + 리뷰
3 테스트(carveout OKX crypto/spot True·미지원 Capital False / per-symbol cross-producer
OKX 합산 cap-0.06 / reentry 독립포지션). 38 pass, full suite 2687 pass. mypy --strict·
ruff clean. fresh Claude 적대리뷰 = **PASS (BLOCKER 0)**, long-only로 netting 위험 무효 확증.

## 후속 (별도 increment)
- 호라이즌 risk budget split(60/40 reservation, 디베이트값) — 자원잠식 관측되면.
- OKX 1D 바 수집(현 0행) + Capital/equity 1D → position 호라이즌.
- venue reconcile: 같은 심볼 long+long 2논리포지션 vs 단일 wallet 잔고 drift 오탐 관측.

## 관련
[[multi_horizon_activation_2026-06-25]] · [[north-star]] · [[feedback_flow_not_block]]
