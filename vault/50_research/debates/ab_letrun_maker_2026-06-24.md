---
type: debate
status: design
topic: ab-letrun-maker-entry
date_created: 2026-06-24
participants: [claude-opus-4-8, gpt-5.5, gpt-5.5-redteam]
verdict: DESIGN-CONVERGED (build pending Jin sign-off)
related: [[all_strategy_edge_diagnosis_2026-06-24]], [[harvest_generalization_2026-06-23]]
tags: [debate, exit, let-run, maker, fee, slippage, flow-not-block]
---

# A+B 설계 — let-winners-run + maker/marketable-limit 진입

DEMO/PAPER · aggressive 보존 · flow_not_block(차단/사이즈컷/블록 0). 설계 단계.
Gemini CLI = free-tier 폐지(Antigravity 이관)로 불가 → GPT base + GPT red-team 2-pass 적대검증으로 대체.

## A = let-winners-run (엑싯 비대칭 수정)
**병형**: burst_rider winner peak +7.33R인데 실현 +0.137R — 고정 lock(+0.25R)·60% give-back force-close·HARVEST 1-ATR trail 붕괴가 spike retrace를 flat 청산.
**설계**: peak-fraction floor 신규 — `floor=entry+peak_mfe_r*frac*atr`, 기존 고정 floor와 phase 합성, prev_stop 대비 monotone(차단 아님, stop을 수익쪽으로 ratchet만).
- knob: `exit_engine.py:303-317`(floor), `:285-290`(HARVEST trail 붕괴), `exit_params.py:43-48,108-123`, tick `_production_tick_mfe.py:90-141`, bar `_production_recalc_exit.py:252-291`.

**디베이트 수렴+적대수정 (load-bearing)**:
1. arm=+2.5R는 common case 굶김 — 측정상 6.5%만 +1.0R 도달, 29%가 +0.30R. 대부분 winner가 arm 못해 3.5-ATR trail에서 naked round-trip(원버그 재현). → burst arm **+1.0R**(0.8-1.2), session_breakout **+0.5R**(0.45-0.6). 기존 저-rung(0.35/0.50/0.25)은 sub-arm floor로 **유지**.
2. raw `max(fixed,peak)`는 crossover 아래서 choke 재유입 — frac=0.50이면 peak +0.50R까지 고정 +0.25R 지배. → **phase 합성**: ~0.6R 미만 BEP/소-lock, 이상 peak-fraction. 고정 floor가 0.6R 위 지배 금지.
3. 60% hard give-back 전면 OFF 위험 — 1R=2ATR, 3.5-ATR trail은 ATR안정 시 1.75R 양보(+7R→+5.25R OK)지만 ATR 4x 팽창 시 7R 양보(→0R). peak-floor(45%→+3.15R)가 구제하나 stop 실행 신뢰 전제. → 전면 OFF 말고 **0.75 backstop 유지**(native stop 검증 전).
- frac: GPT base 0.45-0.50 수렴 → **0.50**(simplicity, 범위내). trail: burst **3.5**, bar **3.0**.

## B = maker/marketable-limit 진입 (fee/slippage 수정)
**병형**: 가격-양인데 fee가 먹음 — session_breakout gross +1.88 vs fee 165, burst fee 133, equity_gap_go 73bps 슬리피지.
**설계**: momentum/breakout → **marketable-limit**(cross-spread cap +N bps, IOC), reversion → **pure post-only**. 모든 경로 **market fallback** 유지(no-fill/reject/timeout) → flow_not_block.
- knob: OKX `_okx_limit_open.py:326-398`(real_okx_open_fill, post_only path 재사용), 라우팅 `_production_tick_engine.py:137,279`(`_MAKER_FIRST_SIGNALS`), bar `_production_pipeline.py:380`, Capital `capital/adapter.py:297`(place_working_order). 어댑터 `ord_type='limit'+last_price_hint` 이미 지원(okx/adapter.py:461-531).

**디베이트 수렴+적대수정**:
- B1: pure post-only는 momentum서 오답(가격 도망→non-fill) — 양 pass 합의. marketable-limit이 절충(taker처럼 체결, adverse fill cap).
- B2 cap_bps: GPT base BTC/ETH **2**, top alt 5, mid 10, thin 20. red-team 반박: 2bps는 breakout서 IOC no-fill→market fallback이 move 후 더 나쁨. → BTC/ETH **5**(normal), burst/OFI extreme 시 8-10, spread+depth가 cap-miss 시사 시 IOC 건너뛰고 직접 market. 비용 ≤edge 25%.
- B3 Capital: working-order 체결률 **미검증** → market default + measurement shadow, 검증 전 maker default 금지(양 pass 합의).

## 체결률 tradeoff (FLAG)
(1) momentum pure post-only = 高 non-fill → marketable-limit으로 회피, burst는 pure-post-only set 제외. (2) cap 너무 tight → no-fill→market fallback(fee 절감 forfeit + move 후 worse) → family/유동성별 cap 폭 필요. (3) Capital demo LIMIT 미검증 → shadow 우선. **모든 경로 market fallback = miss는 never skipped entry, 드물게 worse taker만.**

## flow_not_block 증명
A = stop을 수익쪽 ratchet만(느슨 엑싯, 차단·사이즈컷 아님). B = 진입가 개선(market fallback 상시, 블록 아님). 9-stack 불변(신규 ≤1 mult 0). in-loop GPT=0(전부 deterministic Python). 거부키워드 sweep=0.

## Jin 결정 필요
(A) arm/frac/trail + hard_frac 0.60→0.75 수락(win-rate↓ ↔ right-tail↑, aggressive-correct). (B) 체결률 tradeoff + Capital shadow 윈도우 수락 + cap_bps 시작값(BTC/ETH 5 / top 5-6 / mid 10 / thin 20). 빌드는 확정 후.
