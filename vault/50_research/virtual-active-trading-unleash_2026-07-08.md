---
type: research
status: recorded
date_created: 2026-07-08
tags: [research, backfilled-frontmatter]
---

# Virtual Active-Trading Unleash — per-strategy loosen spec (2026-07-08)

DEMO/PAPER. Goal: VIRTUAL 활발거래 대폭↑ (`POLARIS_VIRTUAL_ACCOUNT=1`), REAL byte-identical.
Pattern: `virtual_loosen(virtual, real)` — real 인자 = **현재값 그대로**. flow_not_block /
aggressive_always_profit / no_defensive_param_dampen (완화지 dampen 아님). 근거: virtual =
무자금·무수수료 → churn 억제 이유 없음. 9-stack·-1.0R rail·사이징·캡 **무접촉**.

## A. 손대면 안 되는 것 (재확인)
사이징(T4 continuous scalar 0.75-1.5 / tier amp 1.5·2·3 / cell mult) · -1.0R rail ·
hard caps(per-symbol/strategy/track/daily) · cluster caps · Cold-Start CS-3 · `profit_target_r`
· maker `post_only`/`maker_no_fill_cancel`. 진입빈도만 완화.

## B. 고빈도 7 전략 — virtual 활성

`dispatch_eligible=False`(KILLed but registered) 4개는 **threshold 완화만으론 무발화** —
dispatch flag 가 master gate. `dispatch_eligible=virtual_loosen(True, False)` 필수(+threshold 심화).

| # | strategy | file | 변경 | 비퇴화 floor |
|---|---|---|---|---|
| 1 | rsi_bb_pullback | rsi_bb_pullback.py | `dispatch_eligible=virtual_loosen(True, False)` (meta L62) + `RSI_THRESHOLD=virtual_loosen(50.0, 39.0)` (L28) | RSI virtual≤50 (midline; BB_lower touch+MA200 상행이 실셋업 유지) |
| 2 | connors_rsi2 | connors_rsi2.py | `dispatch_eligible=virtual_loosen(True, False)` (L114) + `RSI_ENTRY=virtual_loosen(20.0, 13.0)` (L38) | RSI(2) virtual≤20 (SMA200 상행 필터 유지) |
| 3 | supertrend | supertrend.py | `dispatch_eligible=virtual_loosen(True, False)` (L171) + `ATR_FLOOR_PCT=virtual_loosen(0.0002, 0.0005)` (L56) | atr% floor≥0.02% (micro-cap 노이즈 배제; flip 트리거 자체는 불변) |
| 4 | cci_reversion | cci_reversion.py | `dispatch_eligible=virtual_loosen(True, False)` (L85) + `CCI_OVERSOLD=virtual_loosen(-40.0, -70.0)` (L36) | CCI virtual≥-40 (얕은 oversold cross-up; 여전히 <0 편차 반등) |
| 5 | tsmom_12_1_multiasset | tsmom_12_1_multiasset.py | 월 리밸런스 cadence 가 지배(구조적 fee-immunity, real 불변). virtual만 `MOM_LOOKBACK=virtual_loosen(63, 252)`(L70) + `MOM_SKIP=virtual_loosen(5, 21)`(L71) → 짧은 모멘텀·짧은 skip 로 sign>0 심볼 breadth↑. `_is_rebalance_bar` 월경계 게이트는 **불변**(주1회로 못 올림 — cadence=edge) | lookback≥63(분기 모멘텀, 노이즈 아님)·skip≥5 |
| 6 | fx_breakout_basket | fx_breakout_basket.py | dispatch 이미 True. `ADX_THRESHOLD=virtual_loosen(5.0, 10.5)`(L35) + `WINDOW=virtual_loosen(20, 40)`(L28) → 약트렌드 donchian break 발화 | ADX≥5(추세존재 최소)·window≥20(진짜 20-bar break) |
| 7 | weekend_funding_capitulation_maker | weekend_funding_capitulation_maker.py | 주말+shadow 이중 억제. `_WEEKEND_UTC_WEEKDAYS=virtual_loosen(frozenset({0,1,2,3,4,5,6}), frozenset({5,6}))`(L68) + `shadow_first=virtual_loosen(False, True)`(meta L102) → virtual 전일 라이브 maker | funding≤own p10 **AND** funding<0 조건 불변(진짜 crowded-short만) |

## C. 채택 14개 — 완화폭 심화 (real 인자 불변, virtual 인자만 하향)

| strategy | 상수(file) | 현재 virtual_loosen(v, real) → 심화 | floor |
|---|---|---|---|
| okx_donchian_55_breakout | DONCHIAN_WINDOW | (20,55)→(10,55) | window≥10 |
| bar_breakout_run | DONCHIAN_WINDOW / ROC_LOOKBACK | (20,40)→(10,40) / (5,10) 유지 | win≥10·roc≥5 |
| ema_crossover | EMA_REGIME | (50,200)→(20,200) | regime EMA≥20 |
| macd_ema_trend_pullback | EMA_FILTER | (50,200)→(20,200); virtual MACD≤0 shallow-pullback 게이트 드롭 유지 | EMA≥20 |
| gold_breakout_1h | DONCHIAN_WINDOW | (20,55)→(10,55) | ≥10 |
| silver_breakout_1h | DONCHIAN_WINDOW | (20,55)→(10,55) | ≥10 |
| uk100_breakout_1h | DONCHIAN_WINDOW | (20,55)→(10,55) | ≥10 |
| us100_breakout_1h | DONCHIAN_WINDOW | (20,55)→(10,55) | ≥10 |
| gold_trend_chandelier_1d | DONCHIAN_WINDOW | (30,55)→(15,55) | ≥15 (1D) |
| gold_riskoff_trend_amplify | DONCHIAN_WINDOW | (30,55)→(15,55) | ≥15 |
| xau_indices_trend | DONCHIAN_WINDOW / MOMENTUM_LOOKBACK | (15,30)→(10,30) / (10,20)→(5,20) | win≥10·mom≥5 |
| index_52w_high_momentum | HIGH_LOOKBACK / PROXIMITY | (120,252)→(60,252) / (0.97,0.98)→(0.95,0.98) | lookback≥60·prox≥0.95(고점 5% 이내=진짜 near-high) |
| index_dual_momentum_rotation | TOP_N / ROC_LOOKBACK | TOP_N (4,2) 유지; ROC_LOOKBACK 신규 `virtual_loosen(60, 120)`(현재 120 고정) → 짧은 모멘텀 로테이션 | ROC≥60·TOP_N≤universe |
| weekend_thin_book_flush_maker | RSI_FLUSH_THRESHOLD / weekend gate | (35,25)→(45,25); 주말게이트 `virtual_loosen(all-days, weekend)` 추가(#7과 동형) | RSI≤50(진짜 flush 유지) |

## D. (2차·optional) un-registered 재등록 — virtual 전용

session_breakout·donchian_turtle_breakout·spot_donchian·volume_burst 는 fee-bleed KILL 로
`STRATEGY_REGISTRY` 에서 완전 제거됨 → `dispatch_eligible` 로 못 살림(레지스트리 부재).
virtual = fee 0 → fee-bleed KILL 근거 void. `__init__.py` 에 virtual-only 재등록 블록:

```python
from polaris.strategies._virtual_loosen import virtual_mode_enabled
if virtual_mode_enabled():  # REAL registry(default) byte-identical; virtual 만 churner 추가
    for _cls in (SessionBreakoutStrategy, DonchianTurtleBreakoutStrategy,
                 SpotDonchianStrategy, VolumeBurstStrategy):
        STRATEGY_REGISTRY[_cls.metadata.strategy_id] = _cls
```

빌더 확인사항: 각 모듈/클래스명 존재(read-only 보존됨) + import 경로 + Alpaca-inert equity
2종(equity_52wk_high_breakout·equity_vol_expansion_pocket_pivot)은 SIP#42 미라우팅이라 제외.
이들 진입상수(ATR_MULT=1.05·ADX 등)는 이미 완화상태 — 추가 loosen 불요, 재등록만으로 virtual 발화.

## E. 비퇴화 원칙 요약
window lookback ≥5(breakout 계열 ≥10) · RSI 오실레이터 virtual ≤50(midline, 편차조건 유지) ·
proximity ≥0.95 · ADX ≥5 · CCI virtual ≥-40. threshold 극단·window<5 금지 — 신호=실셋업, 노이즈 아님.
複合조건(MA200 상행·BB touch·funding<0)은 전부 불변 → 완화해도 setup 정체성 보존.
