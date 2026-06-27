---
type: research
status: validated
date_created: 2026-06-27
tags: [research, backtest, weekend, funding, maker, okx-spot, capitulation, flow-not-block]
---

# weekend_funding_capitulation — 주말 펀딩-카피출레이션 maker (OKX SPOT long) — VERDICT: 1 candidate (조건부)

DEMO/PAPER · aggressive 보존 · flow_not_block · -1.0R rail · OKX SPOT long-only · funding=SIGNAL only.
backlink: [[weekend_liquidity_range_maker_family_2026-06-27]] · [[weekend_dip_maker_revert_backtest_2026-06-27]] (둘 다 REJECT) · weekend_thin_book_flush_maker (#77 deployed, 보완).

## 핵심 발견 — 주말 고유 펀딩 레짐은 real (cohort separation 결정적)
실데이터 OKX public (funding-rate-history + history-candles 1H), 15 심볼, 2026-03-23~06-27 (95d, ~13.5 주말).
스크립트: `/tmp/weekend_funding_regime_probe.py` · `/tmp/weekend_funding_robust.py` · 기존 `/tmp/funding_skew_backtest.py`/`funding_capitulation_backtest.py`/`decile_probe.py`.

**Q2 — extreme-neg funding(per-symbol ≤p10) → forward spot drift, weekend vs weekday (frictionless):**
| horizon | weekend mean | %pos | weekday mean | %pos |
|---|---|---|---|---|
| +8h | +21.9bps | 51% | −2.3bps | 50% |
| +24h | +57.2bps | 55% | −13.7bps | 54% |
| +48h | **+82.9bps** | 60% | **−46.2bps** | 51% |

깨끗한 cohort 분리: 주말 음(-)펀딩=강한 양(+) 드리프트, 평일 동일신호=음수. **진짜 주말 고유 레짐.**
- Q1 메커니즘: 주말 funding 음수비율 44.6% vs 평일 36.2% — 주말이 crowded-short로 skew (thin book + 8h reset 극단화).
- 문헌 정합: extreme-neg funding이 relief rally 선행, thin 주말 유동성이 perp-spot 스프레드 확대 (단 "보편 임계 없음" + 청산 캐스케이드 continuation 위험도 동반).

## 강건성 (overfit 배제) — 통과
- **R1 IS/OOS 시간반분할(주말, +48h)**: IS +20.6bps(58%) · OOS +144.2bps(62%). **양쪽 다 양수, OOS 더 강함.** (cf. funding_skew let-run-2R 변종은 OOS −53bps로 sign-flip = 그건 overfit. 차이 = weekend-restriction + bounded-exit.)
- **R2 per-symbol breadth**: **12/15 심볼 주말 +48h 양수** (LINK +315 / ETH +206 / BTC +112 / XRP +99; 음수 = LTC/DOT/BCH 3개). 메이저+알트 광범위 = artifact 아님.
- **R3 tradeable conservative maker** (주말, post_only bid, +1R target / -1.0R rail, real fee): fill_rate **99.3%**(thin book 체결 thesis 확인) · net_R **median +0.83R** · win 57% · OOS mean **+0.042**(IS −0.061, ALL −0.009). exit_mix stop61/target79.
  - mean≈flat << median +0.83 = winner를 +1R로 cap해서 우측꼬리(+83~144bps=>+1R) 버림. asymmetry mandate → let-run trail이 upside lever (tunable). bounded +1R가 **tested-positive base**.

## 기존 REJECT들과 왜 다른가
dip_revert(−0.18R)/fade(−0.49R)/skew OOS(−0.53R) 전부 패배 = adverse selection이 엣지 tail 역선택 + 신호진폭<cost. 여기선 주말 +48h 드리프트가 **+83~144bps로 16bps maker floor를 압도** → adverse-selected fill(61 stops)도 median +0.83R로 흑자. fee가 binding이 아니라 **신호가 충분히 큼**이 차이.

## weekend_thin_book_flush(#77 배포)와 중복 아님 = 보완
- flush = 가격축(RSI<25 + 하단BB wick), 빠른 +0.30R revert. funding-cap = **포지셔닝축**(perp 펀딩, okx_funding 콜렉터는 있으나 strategy trigger로 미배선), +24-48h squeeze-unwind drift.
- 직교: slow-grind short-crowding(가격 wick 없음)=flush 못봄, funding이 잡음. spot-led wick(펀딩 중립)=funding 못봄, flush가 잡음. 동시발화=confluence 보너스(셀/AI amplify). **커버리지 확장.**

## 정직한 한계 (배포 전 의무)
- **95d 단일 기간** (OKX history-candles ~30page cap). OOS 절반=~45d. suggestive, not multi-year. 주말기존리서치는 104주(yfinance) — 더 길다. **라이브 maker-fill-shadow로 real 누적 검증 필요.**
- skew-backtest tension: 동일 underlying 신호의 let-run-2R·非주말 변종은 OOS 음수. 양수의 유일 차이 = weekend-restriction + bounded(+1R) exit. → let-run은 upside-tunable, **default는 bounded+1R**(tested).
- /debate 트리거: maker bid 깊이(이격) + bounded vs let-run exit + funding 임계(per-ticker p10 vs abs) = 전략-거동.

## 결론
주말 펀딩-카피출레이션은 dip_revert/fade/skew가 넘지 못한 바를 넘음 (cohort-separated · IS/OOS 양수 · 12/15 broad · median +0.83R net real-fee). **candidate 1개 제안** (reset 변종 Q3는 weak: 주말 +8h median −1.1bps → 폐기, family=1). 조건: 95d 단일기간 한계 명시 + bounded base + let-run tunable + 라이브 shadow 누적검증.
