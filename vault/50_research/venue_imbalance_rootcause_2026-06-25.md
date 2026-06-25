# Venue 거래 불균형 — 근본원인 진단 (2026-06-25)

DEMO/PAPER. read-only forensic. 봇 무중단(메인 worktree 가동중, 코드 미변경).
flow_not_block / aggressive: 결론은 OKX·Capital flow를 **늘리고** Alpaca losing-churn을 **품질개선**(차단 아님).

## Before — 라이브 실측 (data/polaris_live.sqlite)

| venue | fills(2h) | closes | wins | win% | PnL($) | universe active | focus(latest) | trade_eligible | tick/5m |
|-------|-----------|--------|------|------|--------|-----------------|---------------|----------------|---------|
| alpaca | 346 | 159 | 12 | **7.5%** | **-1162.21** | 0 / 12852 | **0** | — | 0 |
| capital | 55 | 133 | 54 | **40.6%** | -71.88 | 167 / 3287 | 96 | 60 | 116 |
| okx | 5 | 39 | 4 | 10% | -65.22 | 219 / 219 | 103 | **9** | 93 |

- Alpaca 평균 보유 **11.9분** (전략 timeframe=1D). 심볼당 7-10 fills churn (MU 10f/3close/-$76).
- Capital 숨은 승자: session_breakout 9/15(60%)+$5.69, micro_reversion 46/119(39%). under-traded.
- OKX 전 전략 저조: tsmom 0/7 -$44, micro_reversion 2/17, flow_pressure 1/12.

## 근본원인 #1 — Alpaca over-trade: held-churn이 1D thesis를 분 단위로 갈아넣음

**모순 해소**: "0 active인데 346 fills" = active 플래그 무시 버그 아님. 시계열로 풀림:
- watchlist_focus 히스토리: alpaca 12:00-14:00 cycle당 **1500 rows** → 14:58:33 이후 **0**. fills도 13:00(17)→14:00(290)→15:00(77)로 focus 풍부할 때 폭증.
- focus producer는 `read_active_universe`(is_active=1)만 sweep — alpaca가 한때 active=1 → sweep 대량 진입.

**진짜 엔진 = 2단 결함**:
1. **Exit horizon 미스매치 (명확한 timeframe-class 버그)**: equity_tsmom/gap_go/rsi_bb 전부 `timeframe="1D"` (1일 모멘텀 thesis)인데 `_production_recalc_exit.py`의 loser-timeout이 1D thesis를 **1시간**에 강제 절단:
   - `_loser_timeout_for_strategy` (`:114-129`): 1D → `tf_floor = 2×bar_seconds("1D") = 172800s`(2일). 그러나 `min(max(900,172800), LOSER_TIMEOUT_CAP_SEC=3600)` = **3600s(1h)**. CAP(3600s, 본래 1H/scalp 기준)이 1D floor를 1시간으로 깎음 — 1D equity track에 무차별 적용된 **명확한 버그**(주석 의도는 "tsmom 1H 7200→3600 cap"인데 1D에도 동일 cap).
   - 게다가 ATR-trail/MFE precise-exit이 평균 **12분**에 먼저 청산 → 1D thesis 전개 전 절단 → 7.5% win.
2. **Exit→재진입 churn**: 청산되면 `concurrent_same_side_open`(`_production_tick.py:638`)이 False → 재진입 cooldown(`reentry_cooldown_active` :657, novelty 면제)이 무력화 → 즉시 재매수. MU: 13:31 buy→14:04 sell(-$76)→14:07 buy→14:20 sell→14:52 buy→15:15 sell→15:16 buy.
3. **held force-seat이 cap 무력화** (`_candidate_sweep_select.py:256-274`): 231 held positions가 cap(200) 무관하게 매 cycle focus에 force-seat → active=0 떨어진 뒤(14:58~)에도 BAR가 held churn 계속(15:00 77 fills).

→ Alpaca의 active 플래그 자체는 정상 동작(below_rank_topN: equity vol_24h가 OKX/Capital 공통 z-rank 풀에서 밀림). 손실 원인은 **active 무시가 아니라 1D-thesis에 분-단위 exit + exit-재진입 churn**.

## 근본원인 #2 — OKX/Capital under-fire

- **OKX**: focus 103 중 **trade_eligible=1은 9개(9%)**. tick entry는 `eligible_only=True`(`_production_layers.py:884` `COALESCE(wf.trade_eligible,1)=1`)로 trade set만 봄 → 91%가 진입 후보에서 제외. EntranceJudge(`core/probes/entrance.py`) `opportunity_score >= trade_floor(0.30)` + OKX non-USD-quote 제약(`_okx_quote_trade_eligible`)이 OKX를 과도하게 깎음. flow_not_block 취지에 역행(원래 floor 완화가 목적이었으나 OKX에서 재차 strangle).
- **Capital**: trade_eligible 60/96으로 덜 깎여 micro_reversion 발화 가능. 단 `TICK_ENGINE_VENUE_SIGNALS` (`core/ticks/config.py:36`)에서 capital={micro_reversion only} — price-quote only(size/tape 없음)라 flow_pressure/burst_rider 구조적 불가. 신호 단일. session_breakout(bar, 60% win)이 31 fills로 소량 → carve-out 확대 여지.

## Fix 권고 (각 = 트레이딩 거동/파라미터 → /debate + Jin 고지 필요, 단발 커밋 금지)

1. **[최대 임팩트] Alpaca 1D-exit horizon 정합**: 1D-timeframe equity 전략의 ATR-trail/MFE/loser-timeout을 timeframe-비례로 늘려 분-단위 절단 방지. `_production_recalc_exit.py:95-103` exit horizon ∝ timeframe 이미 설계됨 — equity 1D에서 실제 적용되는지 검증 + loser-timeout/MFE가 1D thesis에 맞게 스케일하는지 확인. **차단 아님**: 보유시간을 thesis에 맞춤 → churn 제거, 건강한 거래 유지.
2. **OKX trade_eligible floor 완화** (aggressive 정합, "OKX 늘림"): `entrance.py` trade_floor(0.30) 하향 또는 OKX quote 제약 완화 → OKX focus의 9% → 더 많이 trade-eligible. flow_not_block.
3. **Capital under-trade 해소** (숨은 40% 승자): session_breakout(60% win) bar carve-out 확대 + Capital 신호 다양성(micro_reversion 단일 탈피). `keep_on_bar_path` / `CAPITAL_BAR_STRATEGY_SYMBOLS` 확대.

## 핵심 코드 좌표

- BAR entry loop: `polaris/scripts/_production_tick.py:530-672` (focus 순회 + anti-churn 가드)
- exit horizon: `polaris/scripts/_production_recalc_exit.py:95-103`
- focus producer: `polaris/scripts/_production_layers.py:589-715` (`refresh_focus_watchlist` → `_sweep_focus`)
- held force-seat + cap: `polaris/scripts/_candidate_sweep_select.py:256-274`
- trade_eligible seam: `polaris/scripts/_production_layers.py:834-911` (`get_focus_targets eligible_only`)
- EntranceJudge: `polaris/core/probes/entrance.py`
- venue signal routing: `polaris/core/ticks/config.py:36-84`
