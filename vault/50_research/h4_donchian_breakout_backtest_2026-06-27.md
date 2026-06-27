---
type: research
status: validated
date_created: 2026-06-27
tags: [research, backtest, donchian, 4h, breakout, fee, oos, crypto, okx, multi-tf, confluence]
---

# 4h-donchian 돌파 백테스트 — VERDICT: taker FEE-FATAL · maker MARGINAL (1D-confluence 필수, OOS 박함)

DEMO/PAPER · OKX SPOT long-only · aggressive 보존 · flow_not_block(모든 적격 돌파 진입, 비용 실현이지 게이트 X).
backlink: [[okx_donchian_55_breakout]] (1D survivor +97bps) · [[donchian55_fx_generalization_backtest_2026-06-27]] · [[project_validated_edge_is_slow_trend_not_scalp]].
harness: `data/h4_donchian_bt.py` (yfinance 실 4H 바 730d, 19 OKX 유동 메이저, 1D 리샘플 아님=실 4H fetch).

## 핵심 질문 (미검증 가설)
4H는 1H(fee-fatal, net −44.6bps)와 1D(fee-beating, +97bps) **사이** — 안 해봤음. 일봉보다 자주 발화(추정 6배)하면서
1H churn보다 적어 fee 넘나? + 테크니컬스토어 multi-TF confluence(1D regime up AND 4H 돌파)가 1H 단독돌파와 다른 품질인가?

## 셋업 (실데이터, 검증 archetype 정확 복제)
- 진입: close>Donchian-N(prior N, no look-ahead) AND ROC-20>0. N sweep = 20/40/55.
- 엑싯: −1.0R rail(1.0·ATR20 intrabar, **rail 불변**) + peak-protect(arm +0.30R, giveback 50%) + D-20 prior-low harvest trail + time-stop.
- 비용: **taker 70bps RT**(demo flat 35/leg) vs **maker 16bps RT**(real-fee shadow 8bps/leg). slippage stress 10/15/20bps(진입측).
- OOS = chronological 2nd-half. 1R≈205bps(median ATR20/price 4H 2.05%). net_bps = net_mean(R)×205.

## 결과 — taker 전멸, maker는 confluence 없으면 음수
발화빈도(per-instrument/day): **D-20 ≈0.19/d, D-40 ≈0.13/d, D-55 ≈0.11/d** (confluence 시 절반 ≈0.08-0.11/d).
일봉(~1/day)보다 오히려 **드묾** — 4H 돌파+ROC+confluence 조건이 좁아 가설("6배 자주")은 **틀림**.

| 구성 (OOS, slip15) | net bps | win% | PF | inst net+ | verdict |
|---|---|---|---|---|---|
| **taker** 全 window/conf | **−52 ~ −92** | 19-23% | 0.45-0.63 | 1-4/19 | **FEE-FATAL** (1H와 동일) |
| maker standalone (no conf) | −10 ~ −22 | 33-36% | 0.85-0.90 | 4-7/19 | FEE-FATAL (음수) |
| **maker + 1D-confluence** D-20 | **+11.0** | 40.0% | 1.12 | 9/19 | FEE-BEATING (박함) |
| maker + 1D-conf D-40 / D-55 | +1.1 / +2.2 | 37% | 1.01-1.02 | 9-10/19 | MARGINAL (≈breakeven) |

## 🚨 치명 caveat: OOS decay 심함 (robust 아님)
IS vs OOS (maker+conf, slip15): **D-20 +51.1bps(IS)→+11.0(OOS)** · D-40 +37.2→+1.1 · D-55 +30.3→+2.2.
edge가 OOS에서 **반토막~near-zero**. win-rate(~40%/37%)는 유지되나 per-trade edge 붕괴. slip 20bps에서 D-40/55 conf도 음수(+4.0→−5.4).
1D survivor(+97bps, 두꺼운 마진)와 **질이 다름** — 4H는 fee 문턱 바로 위 박빙. 비대칭 winner 소수 견인(inst 9/19) 패턴도 잔존.

## VERDICT: taker FEE-FATAL / maker MARGINAL (조건부)
- ❌ **taker 4H 돌파 추격 = 절대 금지** — 全 window/slip/confluence 음수(−52~−92bps). 1H와 동급 fee-fatal. 추격진입은 4H에서도 죽음.
- ⚠️ **maker + 1D-confluence D-20 = 유일 양수(+11bps OOS)** 이나 박하고 OOS decay 큼 + slip 민감. shadow-first만, 단독 배포 위험.
- ✅ **확정 교훈**: (1) maker 실행 필수(70bps taker는 모든 추세돌파를 죽임), (2) 1D regime-up confluence가 standalone 음수→양수로 뒤집는 **유일 레버**(테크니컬스토어 multi-TF 가치 입증), (3) 그래도 1D 일봉(+97bps)이 4H(+11bps)보다 압도적 — **느린 추세 > 빠른 추세** 재확인(churn 비례 fee drag).
- 가설 반증: 4H가 1D보다 "자주 발화"=거짓(실측 더 드묾), "1H와 1D 사이 sweet spot"=일부만 참(maker+conf에서만, 그것도 박빙).
다음: 4H 단독 신규배포 No. 기존 1D donchian survivor 유지 + 4H는 maker+1D-conf shadow probe로만(엣지 확인 후 admit). churn 아님 검증 완료.
