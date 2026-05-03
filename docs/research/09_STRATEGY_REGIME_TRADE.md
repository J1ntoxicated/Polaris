# 09 — Strategy, Regime, Trade Layer 분석

> 실제 코드 분석 기반
> (regime.py, pipeline.py, evolver.py, backtester.py,
>  exit.py, entry.py, portfolio.py, position.py,
>  tournament.py, dpm.py, ml_meta_filter.py,
>  param_orchestrator.py, gate_matrix.py)
>
> 결론: 유기적으로 잘 연결돼 있음. 이슈는 잔버그 3개 + 구조적 1개.

---

## 전체 흐름 (데이터 → 실행까지)

```
DataCollector (5min/30min)
    ↓
RegimeDetector (Crypto / Macro 독립)
    ↓ regime 주입
TradePipeline.scan_cycle() (60s)
    ├── SignalEngine.evaluate() × N tickers
    ├── ML Meta Filter Gate 7.5 (shadow)
    ├── AI SignalAugmenter Stage 1 (borderline)
    ├── StrategyEngine.select_strategy()
    ├── PortfolioManager.filter_candidates()
    ├── EntryGate.check()
    └── AI EntryJudge Stage 3
            ↓ execute_fn
        Position 생성
            ↓
TradePipeline.exit_cycle() (5s)
    ├── ExitEngine.check_position()
    ├── DPM.evaluate() (신호 재평가)
    └── AI ExitAdviser Stage 4 (CRITICAL)

StrategyEvolver (주기적)
    ├── Gaussian + Bayesian + AI mutation
    ├── StrategyBacktester.run()
    └── TournamentEngine.run_round() (15 trades / 6h)
```

---

## 1. Regime — 잘 작동 ✅

### 구조
- **Dual Detector**: CryptoRegimeDetector (alt_fg, funding, OI, taker, ADX) + MacroRegimeDetector (CNN F&G, HY spread, MOVE, VIX, DXY)
- **Crisis Escalation**: HY > 500 OR VIX > 40 → 양쪽 다 CRISIS override
- **MultiRegimeManager.for_group(group)**: crypto → crypto detector, 나머지 → macro detector
- **Hysteresis**: 3회 연속 같은 regime이어야 flip (CRISIS는 즉시 override)
- **온라인 학습**: 20 트레이드마다 threshold 조정 (wr < 35% → 더 tight, wr > 60% → 더 loose)

### 잘 된 것들
- 크립토와 매크로 분리 → 각자 적합한 신호로 레짐 결정
- Hysteresis로 잦은 flip 방지
- 레짐별 파라미터 프리셋 (CRISIS=min_score 20, 넓은 stop)
- pipeline에서 `_regime_for_group(group, ticker)` 호출로 그룹별 다른 레짐 주입

### 이슈

**🟡 _learn_thresholds → `_threshold_adjustments` 저장 안 됨**
```python
# regime.py
def _learn_thresholds(self):
    ...
    self._threshold_adjustments[regime_enum] = max(0.5, current * 0.9)
    # → 메모리에만 저장, 재시작 시 리셋
```
Bayesian predictor는 디스크에 저장하는데 regime threshold는 안 함.

수정: `_load_presets()` / `_save_presets()` 패턴으로 persistence 추가.

---

## 2. Pipeline — 잘 작동 ✅

### scan_cycle 흐름 (핵심)

```python
# 1. Safety halt check
# 2. Open positions / recent rejects 제외
# 3. 시장 마감 체크 (non-crypto)
# 4. 그룹별 레짐 주입
# 5. SignalEngine.evaluate()
# Gate 7.5: ML Meta Filter (shadow)
# Stage 1: AI SignalAugmenter (borderline)
# 6. StrategyEngine.select_strategy() + group mismatch 체크
# 7. PortfolioManager.filter_candidates() (regime-aware max_concurrent)
# 8. EntryGate.check()
# Stage 3: AI EntryJudge
# 9. execute_fn 실행
```

### 잘 된 것들
- **PLTR x61 루프 방지**: repeat_entry_3x_1h (3회 이상 같은 시간대) + price_move 예외 (2% 이상 이동 시 DCA 허용)
- **PLTR stale price 방지**: price_ts > 600s → reject
- **mid-batch race fix**: `portfolio.positions()` 다시 체크하면서 max_concurrent 방어
- **strategy group mismatch 체크**: forex 전략이 stock에 적용되는 버그 방지
- **regime-aware max_concurrent**: CRISIS → 8, TRANSITION → 6, 나머지 → preg 값

### 이슈

**🟡 reject_cooldown_sec 매 tick 호출**
```python
# scan_cycle() 루프 안에서
self._recent_rejects = {t: ts for t, ts in self._recent_rejects.items()
                        if _now - ts < preg("reject_cooldown_sec")}  # ← 매번 preg()
```
preg()이 dict 조회라 큰 문제는 아니지만, 루프 밖으로 빼는 게 더 깔끔:
```python
_reject_cooldown = preg("reject_cooldown_sec")
self._recent_rejects = {t: ts for t, ts in self._recent_rejects.items()
                        if _now - ts < _reject_cooldown}
```

---

## 3. Entry — 잘 작동 ✅

### EntryGate 체크 순서
```
1. Blacklist (static + auto-learned + regime-conditional)
2. Direction bias (학습된 방향 편향)
3. Cooldown (기본 3min, crisis/risk_off 60s)
4. Repeat-entry rate limiter (3x/1h, price move 2% 예외)
5. Market hours (non-crypto schedule SSOT)
6. Stale price (60s)
7. Min volatility (atr_pct < min_atr × group_mult)
8. Stagnant ticker (atr 낮고 mom_2m 거의 0)
9. Zero strength
10. Tech data 없음
11. Price deviation (24h range ±20% 이탈)
```

### 잘 된 것들
- Regime-conditional blacklist 지원 (특정 레짐에서만 블록)
- ATR-based 최소 변동성 게이트 (group별 multiplier 적용)
- GateMatrix shadow 평가 (log-only, 블로킹 없음 — 데이터 수집용)

---

## 4. Exit — 잘 작동 ✅

### ExitEngine 3 카테고리
```
STOP:  pnl <= hard_stop_pct (min_hold 무시, 항상 즉시)
TRAIL: BEP trail + 3-tier progressive + holdtime tightening + profit cap
TIME:  flat kill + max hold + profit decay + stagnant + pre-market close
```

### 잘 된 것들
- exit_params 진입 시 frozen → 전략별 다른 파라미터 적용 가능
- Group volatility profiles (stock: vol_mult 2.5x, hold_mult 8x → 긴 포지션)
- ATR-adaptive stop (atr_pct × -1.5 = dynamic stop)
- 3-tier progressive trailing (수익 구간별 trail 거리 달라짐)
- Regime-aware exit (crisis → stop 1.3x wider, max_hold 1.5x longer)

### 이슈

**🟡 strategy_exit 파싱에서 여러 키 체크 중복**
```python
# exit.py calc_entry_exits
_flat_kill = (_time.get("flat_kill_sec")
             or _time.get("flat_kill_s")      # legacy
             or preg("flat_kill_sec"))
_max_hold = (_time.get("max_hold_sec")
             or _time.get("max_hold_s")       # legacy
             or preg("max_hold_sec"))
```
`flat_kill_s` / `max_hold_s` 같은 레거시 키가 아직 코드에 남아있음. 전략 JSON 파일이 업데이트되면 정리 가능.

---

## 5. Portfolio — 잘 작동 ✅

### filter_candidates 체크
```
1. max_concurrent (regime-aware override)
2. 이미 open 포지션 제외 (canonical base 기준 → Tesla=TSLA=TSLA-USDT-SWAP)
3. max_correlated (같은 group 최대 N개)
4. net_exposure_ratio (롱/숏 노출 비율 cap)
5. Score 내림차순 정렬 후 슬롯 채우기
```

### 잘 된 것들
- canonical_base 기준 중복 체크 → 같은 기초자산의 다른 거래소 포지션 방지
- Adopted position 은 슬롯 카운트에서 제외
- RLock 사용 (재진입 가능 — 중요, 포지션 업데이트 중 필터 가능)

---

## 6. DPM (동적 포지션 관리) — 잘 작동 ✅

### 평가 순서
```
1. min_hold 게이트 (너무 이른 KILL 방지)
2. 30s rate limit
3. Entry score 비교
4. EMA-smoothed current score
5. Signal REVERSED → KILL (reversal confirm 2회 필요)
6. Signal WEAKENED → TIGHTEN (debounced)
7. SCALE_IN 두 경로:
   (a) Winning pyramid: 신호 강화 + 수익 중
   (b) Contrarian DCA: crisis/risk_off + 신호 유지 + 손실 중 (averaging down)
8. Breakeven protection
9. Time decay
```

### 잘 된 것들
- EMA smoothing (window=5) → whipsaw 방지
- Reversal confirm 2회 필요 → 잦은 KILL 방지
- Contrarian DCA 경로: crisis에서 손실 중인 포지션 averaging down (max 3회, 250% pyramid)

### 이슈

**🟡 _score_history maxlen vs EMA window 불일치 가능성**
```python
self._score_history[ticker] = deque(maxlen=self._EMA_WINDOW)
# → maxlen=EMA_WINDOW(=5)이면 history가 최대 5개
# EMA 계산에서 history[0]부터 순회하는데
# 5개 이하면 EMA가 빠르게 수렴 → OK, 하지만
# maxlen이 EMA_WINDOW보다 클 경우 낭비

# 실제로는 maxlen == EMA_WINDOW이므로 정상
# 하지만 preg("dpm_ema_window") 값이 변경되어도
# 이미 생성된 deque의 maxlen은 변경 안 됨 → hot-reload 불가
```

---

## 7. Strategy Evolution — 잘 작동, 한 가지 주의점

### 구조
```
StrategyEvolver
  ├── Gaussian mutation (±random, bound-clamped)
  ├── Bayesian mutation (성능 기반 방향 추론)
  ├── AI-guided mutation (Claude Sonnet → 파라미터 제안)
  └── StrategyBacktester.run() → FitnessFunction 평가

TournamentEngine (별도)
  ├── 15 trades OR 6h 마다 라운드
  ├── Group별 bracket 경쟁
  ├── Elo rating 기반 상대 평가
  └── ELIMINATED → status=disabled
```

### PARAM_BOUNDS 설계 좋음
```python
"exit.hard_stop_pct": (-3.0, -0.8),    # tightened: -5% is too deep
"signal.min_score": (35, 65),           # was 25-75: 25 is noise entry
"sizing.base_risk_pct": (0.5, 3.0),    # was up to 5%: too aggressive
```
경계 이유가 주석으로 설명됨. 합리적.

### 이슈

**🔴 Tournament.run_round() — strategies/trades 없으면 조용히 skip**
```python
def run_round(self, strategies: list[dict] = None, trades: list[dict] = None):
    if not strategies or not trades:
        log_event("TOURNAMENT", "No strategies or trades for round", "debug")
        return {}  # ← debug 레벨이라 놓치기 쉬움
```

`should_run_round()` → True인데 실제 round가 실행 안 되는 경우, 외부에서 strategies/trades를 주입해야 하는데 미주입 시 조용히 실패.

확인:
```bash
grep "Tournament.*Round" data/invasion.log | tail -10
# Round 번호가 올라가는지 확인
```

---

## 8. ML Meta Filter (Gate 7.5) — shadow mode, 개선 여지

### 현재 상태
- shadow mode (meta_filter_enabled=false) → 블로킹 없음, 로그만
- LightGBM classifier 학습 → pickle 저장
- features: composite_score, factor_count, agreement, fg_value, vix, atr_pct, recent_wr, direction, hour

### 이슈

**🟡 retrain에서 placeholder 너무 많음**
```python
features = [
    r["entry_strength"] or 0,
    0,   # factor_count — trades 테이블에 없음
    0,   # agreement — trades 테이블에 없음
    0,   # regime_score — placeholder
    50,  # fg_value — placeholder
    20,  # vix — placeholder
    0,   # atr_pct — placeholder
    0.5, # recent_wr — placeholder
    ...
]
```

핵심 피처 7개 중 6개가 placeholder. 이 상태로 학습한 모델은 entry_strength와 direction만 실질적으로 쓰는 것.

**수정:** signals 테이블 (score, factor_count, providers) + ai_decisions 테이블 (confidence) + market_context (fg, vix) JOIN해서 실 피처 채우기:
```sql
SELECT s.score, s.factor_count, s.factor_count as agreement,
       mc.fear_greed, mc.vix_value, t.pnl_pct
FROM trades t
LEFT JOIN signals s ON t.ticker = s.ticker 
    AND abs(s.ts - t.entry_ts) < 120
LEFT JOIN market_context mc ON abs(mc.ts - t.entry_ts) < 300
WHERE t.status='closed'
LIMIT 2000
```

---

## 9. GateMatrix (Shadow) — 이해 필요

**현재 역할:** 모든 결정 포인트에서 log-only 평가. 블로킹 없음. 실제 gate와 병렬로 작동.

**목적:** GateMatrix가 충분한 데이터를 쌓으면 실제 gate로 전환. 지금은 데이터 수집 단계.

실제 블로킹으로 전환 시 체크 필요:
```bash
grep "GATE_SHADOW" data/invasion.log | grep "would block" | sort | uniq -c | sort -rn | head -20
# 어떤 gate가 가장 많이 트리거되는지 확인
```

---

## 전체 유기성 평가

| 레이어 | 연결 상태 | 비고 |
|--------|-----------|------|
| Regime → Pipeline | ✅ | group별 regime 주입 |
| Pipeline → SignalEngine | ✅ | market_data에 regime 포함 |
| Pipeline → AI Stages | ✅ | 1,3,4 stage 순서대로 |
| Pipeline → Strategy | ✅ | group mismatch 방어 |
| Pipeline → Portfolio | ✅ | regime-aware max_concurrent |
| Portfolio → EntryGate | ✅ | 두 번 체크 (filter + execute 직전) |
| Position → ExitEngine | ✅ | frozen exit_params 사용 |
| Position → DPM | ✅ | 신호 재평가 30s |
| Trade → Evolver | ✅ | EventBus trade.closed |
| Trade → Tournament | 🟡 | strategies/trades 주입 필요 |
| Trade → Regime | ✅ | record_trade_outcome |
| Trade → Quality | ✅ | pattern 학습 |
| Trade → Bayesian | ✅ | online likelihood update |

**총평: 전체적으로 유기적으로 잘 연결돼 있음. 큰 구조적 문제 없음.**

---

## 우선순위

### 즉시 (버그 / 검증)
```
[ ] Tournament round 실제 실행 여부 확인
    grep "Tournament.*Round" data/invasion.log | tail -10

[ ] DPM Contrarian DCA 실제 발동 여부 확인
    grep "contrarian_dca" data/invasion.log | tail -10

[ ] Regime threshold persistence 추가 (재시작 시 학습 유지)
```

### 중기 (품질 개선)
```
[ ] ML Meta Filter retrain: placeholder 피처 → 실 DB 피처로 교체
[ ] GateMatrix shadow 데이터 분석 → 실 gate 전환 검토
[ ] exit.py 레거시 키 (flat_kill_s, max_hold_s) 정리
```

### 장기
```
[ ] regime._threshold_adjustments 디스크 persistence
[ ] DPM EMA window hot-reload 지원
```
