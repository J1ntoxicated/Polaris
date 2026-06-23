---
type: research
status: designed-pending-jin
date_created: 2026-06-24
tags: [debate, flow_pressure, crypto, profit-tuning, entry-precision]
---

# Debate — flow_pressure 크립토 수익 튜닝 (entry+profit) 2026-06-24

연: [[flow_pressure_calibration_ai_2026-06-23]]

## 안건 (Jin)
flow_pressure 크립토(OKX SPOT) 맞춤 튜닝해 수익나게. profit-taking/let-winners-run
활성 — 로스방어만 하지 말 것. **설계까지만, 거동변경이라 Jin 승인 후 빌드.**

## 진단 (실측, 코드확인)
flow_pressure(OFI momentum, LONG-only SPOT)는 **소진된 스파이크 꼭대기/플래토를 매수**.
`_flow_followthrough` (features.py:143-210)의 4-체크는 "스파이크가 아직 안 죽음"만 증명
— microprice가 tail-range 중점 위 + OFI가 0.6·θ 위면, 상승모멘텀이 죽은 평평한 플래토도
4체크 통과 → 꼭대기 매수. 결과: **61% MFE~0**(진입이 수익으로 안 감), unknown-regime
버킷은 −0.837R로 맹목 발사. 정정: flow_pressure→Bucket.TREND (exit_engine.py:401-413),
LET_RUN(666) **이미 도달가능** — let-run 막는 건 버킷오분류 아니라 (a)딸 MFE 없음 (b)25s
grace 안에서 즉사. **profit 활성은 entry fix의 다운스트림.**

## 디베이트 수렴 (DEMO·aggressive 컨텍스트 명시)
GPT(gpt-5-mini; gpt-5.5/gpt-5 둘 다 TimeoutError 폴백) + Gemini(gemini-2.5-pro) 수렴:
(a) 실패 = long-only SPOT의 "exhaustion top-buy", (b) fix = "스파이크 살아있음" 대신
**양(+)의 continuation 증거**(dOFI/accel 지속·microprice higher-high·aggressor 테이프),
(c) profit = 낮춘 rung + partial bank + TP 도달 후에만 켜지는 WIDEN.
**미해결**: 라우터가 Gemini 본문을 [:200]로 잘라 Gemini 수치/fade대안 의견 미포착 —
수렴은 진단+방향만, 숫자 아님. no-single-review-verdict → fade-on-retrace는 열린 A/B.

## 튜닝 설계
ENTRY (surgical primary):
- E1 `_flow_followthrough` 롱 분기에 5번째 continuation 요건: accel>0(가속 지속)·
  mids[-1]≥max(mids[:-1])(신고가=연장)·최근2스텝 동부호 velocity. "아직 안죽음"→"여전히 상승".
- E2 ewma_fast_sec 1.0→~2.5-3.0 (POLARIS_TICK_EWMA_FAST_SEC) — 순간 peak에서 벗어나 지속압력.
  theta_ofi=0.40 불변(fee-net 상수).
- E3 unknown 셋에 flow_pressure 유지(제거X), E1 continuation을 unknown에도 적용 — 차단 아닌 corroboration.

PROFIT (surgical primary, entry fix 후):
- P1 rung을 실측 0.326R winner mass에 맞춤: mfe_bep_r 0.35→0.20, mfe_protect_r 0.50→0.30,
  lock 0.25→0.15 (config.py:255-257). 현재 전 rung이 MFE mass 위라 안 켜짐→winner 반납.
- P2 `_flow_decay_exit` mfe_gate 0.50→0.30 (_production_tick_engine.py:264-287). grace 25s 불변.

실험 (Jin-결정, primary 미포함): ENTRY-3 long-only fade-on-retrace 신규 진입 분기,
P3 scale-out partial bank(exit FSM partial-fill 변경). 둘 다 별도 빌드.

## flow_not_block 확인
모든 변경 = 진입타이밍 지연(재-arm next cadence) / post-green 수익확보. veto·size-cut·
9-stack·in-loop GPT 도입 0. 거래수 ↓(정밀)는 의도된 부수효과, 디펜시브 throttle 아님.

## Jin 결정사항
1. ENTRY: surgical continuation-confirm(E1+E2+E3) primary 승인? + fade-on-retrace A/B 빌드?
2. PROFIT: rung 하향(P1+P2) 승인? + scale-out partial(P3) 빌드?
3. knob 타겟 확정. 4. (옵션) [:200] 잘림 없이 라우터 재실행해 Gemini 수치 포착?

기대효과: MFE>0 39%→~55-70%, unknown −0.837R 버킷 정리, 0.326R winner 뱅킹 → R breakeven→양(+).
