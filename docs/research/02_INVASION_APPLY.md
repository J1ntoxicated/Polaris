# 02 — INVASION v10 적용 포인트 매핑

> 각 레포의 핵심 개념을 INVASION 레이어/파일에 정확히 어디에 어떻게 붙이는지 매핑.

---

## L4 Signal — signal/engine.py, signals/providers*.py

### freqtrade FreqAI → ML 시그널 프로바이더 추가
**대상 파일**: `signals/providers_extended.py`
**추가 클래스**: `MLSignalProvider`

```
FreqAI 개념                   INVASION 구현
─────────────────────────────────────────
feature_engineering_expand_all → 캔들 + 펀딩 + OI 피처 자동 생성
set_freqai_targets            → 미래 N캔들 수익률 레이블
continual_learning            → 주기적 모델 재학습 (hourly_stats tick 활용)
LightGBM predict              → composite_score 보조 입력
```

**구현 방향**:
- `signals/providers_extended.py`에 `MLSignalProvider` 클래스 추가
- LightGBM or XGBoost 모델을 `data/models/` 에 저장
- `candle_tech` tick (300s)마다 피처 업데이트, `hourly_stats` tick (3600s)마다 재학습

### Qlib Alpha158 → 피처 엔지니어링
**대상 파일**: `signals/providers_extended.py` → `MacroRegimeProvider` 강화
**적용 피처**: Alpha158 중 크립토/FX 적합한 것 추출
```
ROCP(5), ROCP(10), ROCP(20)   → 단기 모멘텀
MA(5)/MA(20), MA(10)/MA(30)   → 크로스 신호
STD(5), STD(10), STD(20)      → 변동성
CORR(close, volume, 5)        → 가격-거래량 상관
```

### mlfinlab CUSUM → 이벤트 기반 시그널 샘플링
**대상 파일**: `signal/engine.py`
**적용**: 노이즈 신호 필터링 — 가격 구조 변화 감지 시에만 시그널 평가 트리거

---

## L5 Strategy — strategy/evolver.py, tournament.py

### freqtrade Hyperopt → GA Fitness 함수 개선
**대상 파일**: `strategy/evolver.py`
**현재**: fitness = win_rate × avg_pnl (단순)
**개선**: Sharpe Ratio 기반 fitness
```python
# 현재
fitness = win_rate * avg_pnl_pct

# 개선 (freqtrade SharpeHyperOptLoss 참고)
returns = [trade.pnl_pct for trade in trades]
sharpe = mean(returns) / std(returns) * sqrt(252)
fitness = sharpe  # 또는 sortino_ratio
```

### Jesse Monte Carlo → 오버피팅 탐지
**대상 파일**: `analytics/` 새 파일 `monte_carlo.py` 추가
**적용**: 전략 evolver 후 Monte Carlo 검증 → 오버피팅 전략 자동 제거
```
트레이드 순서 랜덤 셔플 × 1000회 → 수익 분포 확인
실제 결과가 분포의 상위 5% 이내면 → 운(luck) 판정 → 제거
```

### mlfinlab Triple Barrier → 전략 레이블 개선
**대상 파일**: `prediction/bayesian.py`
**현재**: 거래 결과를 win/loss 이진 레이블
**개선**: Triple Barrier로 TP/SL/시간 구분 레이블 → Bayesian prior 정확도 향상

---

## L6 Trade — trade/pipeline.py, exit.py, sizer.py

### freqtrade 다층 트레일링 SL → exit.py 개선
**대상 파일**: `trade/exit.py`
**현재**: trail_tiers 고정 구조
**개선**: pnl 구간별 동적 트레일링
```
pnl < 0.5%  → hard_stop 유지 (현재 그대로)
pnl 0.5~1%  → trail을 entry 대비 -0.3%로 타이트하게
pnl 1~2%    → trail을 peak 대비 -0.5%로
pnl > 2%    → trail을 peak 대비 -0.3%로 (수익 보호 강화)
```

### mlfinlab Kelly Criterion → sizer.py 개선
**대상 파일**: `trade/sizer.py`
**현재**: base × tier × regime × score × streak × session × ticker (7승수)
**개선 검토**: Kelly fraction을 8번째 승수로 추가
```python
# Kelly Criterion
kelly_f = (win_rate - (1 - win_rate) / profit_ratio)
kelly_f = max(0, min(kelly_f, 0.25))  # 최대 25% 캡
size = base_size * kelly_f_multiplier(kelly_f)
```

### Hummingbot 인벤토리 스큐 → sizer.py
**적용**: 동일 그룹 내 포지션 편향에 따라 신규 사이즈 비대칭 조정
```
CRY 그룹 long 포지션 많음 → 신규 long 사이즈 축소
CRY 그룹 short 포지션 많음 → 신규 short 사이즈 축소
```

---

## L5 Strategy — strategy/ 새 전략 추가

### quant-trading → 새 전략 포팅
**대상 파일**: `strategy/` 폴더에 새 전략 파일 추가

#### Dual Thrust (FX/선물 브레이크아웃)
```
적합 그룹: FX (Capital.com), 선물
로직:
  range = max(HH - LC, HC - LL)  # 전일 레인지
  upper = open + 0.5 * range     # 상단 → long
  lower = open - 0.5 * range     # 하단 → short
  k1, k2 = 0.4~0.6 (hyperopt으로 최적화)
파라미터 등록: param_registry에 dual_thrust_k1, dual_thrust_k2 추가
```

#### London Breakout (FX 세션 브레이크아웃)
```
적합 그룹: FX (Capital.com EUR/USD, GBP/USD)
로직:
  런던 오픈 전 (06:00~07:00 UTC) 레인지 계산
  07:00 UTC 이후 상단/하단 돌파 시 진입
  뉴욕 오픈 (13:00 UTC) 전후 청산
파라미터: london_range_start, london_range_end, breakout_multiplier
```

---

## L1 Data — data/collectors/

### Qlib Point-in-time 설계 → data/store.py 검증
**적용**: 캔들 데이터 저장 시 룩어헤드 바이어스 여부 검증
- `candles` 테이블 ts 기준으로 과거 데이터만 참조하는지 확인
- 백테스터에서 미래 데이터 사용 여부 체크 로직 추가

### quant-trading VIX Calculator → data/collectors/ 추가
**대상**: `data/collectors/vix_calc.py` 신규
**적용**: CBOE VIX 방식으로 크립토 내재변동성 계산 → signal provider 입력

---

## L8 Ops — ops/, analytics/

### backtesting.py Optuna → analytics/grid_search.py 업그레이드
**현재**: 단순 grid search
**개선**: Optuna Bayesian 탐색으로 교체
```bash
pip install optuna
```
```python
import optuna

def objective(trial):
    params = {
        'min_signal_score': trial.suggest_float('min_signal_score', 50, 80),
        'trail_pct': trial.suggest_float('trail_pct', 0.5, 3.0),
    }
    # 백테스트 실행 → Sharpe 반환
    return sharpe_ratio

study = optuna.create_study(direction='maximize')
study.optimize(objective, n_trials=100)
```

### Jesse Monte Carlo → analytics/monte_carlo.py 신규
**적용**: 전략 진화 후 오버피팅 탐지
**트리거**: `evolution` tick (3600s) 완료 후 자동 실행

---

## L7 AI — ai/live.py, ai/feedback.py

### FinRL 리워드 함수 → ai/feedback.py 개선
**현재**: trade.closed 후 단순 pnl 피드백
**개선**: Sharpe 기반 리워드 계산
```python
# ai/feedback.py on_trade_closed
returns = [t.pnl_pct for t in recent_trades[-20:]]
sharpe = mean(returns) / (std(returns) + 1e-8) * sqrt(252)
volatility_penalty = std(returns)
reward = sharpe - 0.1 * volatility_penalty
# 이 reward를 AI Governor 컨텍스트에 포함
```

### mlfinlab Meta-Labeling → trade/pipeline.py Gate 8~9
**현재**: Gate 8 = Gemini LiveSignalAugmenter, Gate 9 = Gemini LiveEntryJudge
**개선**: Gate 8 전에 ML 메타모델 필터 추가 (Gate 7.5)
```
1차 모델: SignalEngine → direction (long/short)
2차 모델: MLMetaFilter → 진입 여부 (0 or 1)
→ 2차 모델이 0이면 Gate 8 도달 전에 차단
```

---

## 적용 불가 / 제외 항목

| 레포 | 기능 | 제외 이유 |
|------|------|----------|
| nautilus_trader | 직접 통합 | Rust/Cython 빌드 복잡, 우리 구조와 충돌 가능 |
| FinRL | PPO Sizer 직접 통합 | 학습 시간, 안정성 불확실 — 실험 환경에서만 |
| Hummingbot | 직접 통합 | 독립 시스템으로 우리 구조와 아키텍처 충돌 |
| hftbacktest | 라이브 적용 | 틱 데이터 수집 인프라 필요 — 검증 도구로만 |
