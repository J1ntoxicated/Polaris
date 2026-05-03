# 04 — Phase 2: ML 시그널 도입

> Phase 1 완료 후 진행.
> ML 모델 추가 — 기존 시그널 프로바이더에 영향 없이 새 프로바이더로 추가.

---

## Task 2-A: ML 시그널 프로바이더 (FreqAI 방식)
**참고 레포**: freqtrade (FreqAI)  
**대상 파일**: `invasion/signals/providers_extended.py` 신규 클래스 추가  
**예상 공수**: 대 (1~2일)

### 목표
- LightGBM 모델이 캔들 + 시장 데이터로 방향/강도 예측
- 기존 14개 프로바이더에 `MLSignalProvider` 추가 (15번째)
- 백그라운드 재학습 (hourly_stats tick 활용)

### 피처 설계 (Alpha158 참고)
```python
features = {
    # 가격 모멘텀
    'rocp_5':  close.pct_change(5),
    'rocp_10': close.pct_change(10),
    'rocp_20': close.pct_change(20),

    # 이동평균 크로스
    'ma5_20':  ma(5) / ma(20) - 1,
    'ma10_30': ma(10) / ma(30) - 1,

    # 변동성
    'std_5':   close.rolling(5).std() / close,
    'std_20':  close.rolling(20).std() / close,

    # 거래량
    'vol_ratio': volume / volume.rolling(20).mean(),

    # 크립토 특화 (OKX 데이터)
    'funding_rate': funding_rate,
    'ls_ratio':     ls_ratio,
    'taker_ratio':  taker_buy_vol / (taker_buy_vol + taker_sell_vol),
    'oi_change':    open_interest.pct_change(1),

    # 기술적 지표
    'rsi_14':  rsi(14),
    'bb_pct':  (close - bb_lower) / (bb_upper - bb_lower),
}
```

### 레이블 설계 (Triple Barrier 방식)
```python
# 미래 N캔들 수익률 기반
label = 1  if future_return > +threshold
label = -1 if future_return < -threshold
label = 0  otherwise
```

### 구현 스텝
```
1. 의존성 추가
   pip install lightgbm scikit-learn

2. signals/providers_extended.py 에 MLSignalProvider 클래스 추가:
   class MLSignalProvider(BaseProvider):
       name = 'ml_signal'
       weight = 0.15  # 전체 composite score의 15%

       def score(self, ticker, direction, ctx) -> float:
           features = self._build_features(ticker)
           if features is None:
               return 50.0  # 데이터 없으면 중립
           pred = self.model.predict(features)
           # pred → 0~100 점수로 변환
           return self._pred_to_score(pred, direction)

       def _build_features(self, ticker):
           candles = DataStore().get_candles(ticker, limit=50)
           if len(candles) < 30:
               return None
           # ... 피처 계산 ...

       def retrain(self, ticker):
           # trades 테이블에서 과거 데이터 가져와서 재학습
           ...

3. data/models/ 폴더에 모델 파일 저장
   data/models/{ticker}_ml_signal.pkl

4. ticks/candle_tech.py 에 피처 업데이트 로직 추가

5. ticks/hourly_stats.py 에 재학습 트리거 추가:
   for ticker in active_tickers:
       MLSignalProvider().retrain(ticker)

6. param_registry에 추가:
   ml_signal_enabled: true
   ml_signal_weight: 0.15
   ml_retrain_min_trades: 30  # 최소 학습 데이터
   ml_feature_lookback: 50    # 캔들 수
```

### 검증
```bash
# 모델 학습 확인
python3 -c "
from invasion.signals.providers_extended import MLSignalProvider
p = MLSignalProvider()
p.retrain('BTC-USDT-SWAP')
score = p.score('BTC-USDT-SWAP', 'long', {})
print(f'ML score: {score}')
"
```

---

## Task 2-B: Qlib Alpha158 피처 추출
**참고 레포**: Qlib (Microsoft)  
**대상 파일**: `invasion/signals/providers_extended.py` → `MacroRegimeProvider` 강화  
**예상 공수**: 중 (4~6시간)

### 목표
- Alpha158 피처셋에서 크립토/FX에 적합한 피처 추출
- 기존 MacroRegimeProvider의 입력 피처 강화

### Qlib 설치 & 피처 추출
```bash
pip install pyqlib
```

### 크립토 적합 Alpha158 피처 (30개 선별)
```python
# qlib/contrib/data/handler.py 참고해서 직접 구현

# 가격 기반
ROCP  = close.pct_change(n)              # n = 5, 10, 20, 30, 60
ROEQ  = (close - close.shift(n)) / close.shift(n)
MA    = close.rolling(n).mean() / close
STD   = close.rolling(n).std() / close
BETA  = linregress(range(n), close[-n:])[0]  # 기울기

# 거래량 기반
KMID  = (close - open) / open
KLEN  = (high - low) / open
KUP   = (high - max(open, close)) / open
KLOW  = (min(open, close) - low) / open

# 크립토 특화 추가
FUND  = funding_rate  (OKX)
LSR   = ls_ratio      (OKX)
OI    = open_interest.pct_change(1)
```

### 구현 스텝
```
1. signals/alpha_features.py 신규 파일 생성
   AlphaFeatureBuilder 클래스
   → get_features(ticker, candles, market_data) → dict

2. MacroRegimeProvider.score() 에서 alpha_features 사용:
   features = AlphaFeatureBuilder().get_features(ticker, candles, market_data)
   # 기존 로직 + alpha features 결합

3. MLSignalProvider (Task 2-A)에서도 동일 피처 빌더 사용
   → 피처 일관성 유지
```

---

## Task 2-C: Sharpe 기반 GA Fitness
**참고 레포**: freqtrade (SharpeHyperOptLoss)  
**대상 파일**: `invasion/strategy/evolver.py`  
**예상 공수**: 소 (1~2시간)

### 구현
```python
# evolver.py의 _compute_fitness() 메서드 교체

def _compute_fitness(self, strategy_id: str, trades: list) -> float:
    if len(trades) < 5:
        return 0.0

    returns = [t['pnl_pct'] for t in trades]
    mean_r = sum(returns) / len(returns)
    std_r = (sum((r - mean_r)**2 for r in returns) / len(returns)) ** 0.5

    if std_r < 1e-8:
        return 0.0

    # Sharpe (annualized 근사 — 일 단위 가정)
    sharpe = mean_r / std_r * (252 ** 0.5)

    # Sortino (하방 변동성만)
    downside = [r for r in returns if r < 0]
    down_std = (sum(r**2 for r in downside) / max(len(downside), 1)) ** 0.5
    sortino = mean_r / (down_std + 1e-8) * (252 ** 0.5)

    # 거래 수 페널티 (너무 적으면 불신)
    trade_penalty = min(1.0, len(trades) / 20)

    return (0.6 * sharpe + 0.4 * sortino) * trade_penalty
```

---

## Task 2-D: ML 메타 필터 (Meta-Labeling)
**참고 레포**: mlfinlab  
**대상 파일**: `invasion/trade/pipeline.py`  
**예상 공수**: 중 (4~6시간)

### 목표
- Gate 8 (LiveSignalAugmenter) 전에 ML 메타필터 추가 (Gate 7.5)
- 1차 시그널(방향)에 대해 2차 ML 모델이 진입 여부 결정
- False positive 감소

### 구현 스텝
```
1. trade/ml_meta_filter.py 신규 생성
   class MLMetaFilter:
       def should_enter(self, ticker, direction, signal_ctx) -> bool:
           features = self._build_meta_features(ticker, signal_ctx)
           prob = self.model.predict_proba(features)[0][1]
           threshold = param_registry.get('meta_filter_threshold')  # default 0.55
           return prob >= threshold

2. pipeline.py Gate 8 앞에 추가:
   # Gate 7.5: ML Meta Filter
   if param_registry.get('meta_filter_enabled'):
       if not MLMetaFilter().should_enter(ticker, direction, signal_ctx):
           return None  # 차단

3. 메타 피처 (시그널 컨텍스트 기반):
   composite_score, 각 provider별 score, regime, time_of_day,
   recent_win_rate (last 10 trades), cooldown_remaining, etc.

4. 학습 데이터:
   signals 테이블의 acted_on=True 거래들
   → pnl_pct > 0 이면 label=1, 아니면 label=0

5. param_registry:
   meta_filter_enabled: true
   meta_filter_threshold: 0.55
   meta_filter_min_samples: 50
```

---

## Phase 2 완료 체크리스트

```
[ ] Task 2-A: MLSignalProvider (signals/providers_extended.py)
[ ] Task 2-B: Alpha158 피처 빌더 (signals/alpha_features.py)
[ ] Task 2-C: Sharpe GA Fitness (strategy/evolver.py)
[ ] Task 2-D: ML 메타 필터 Gate 7.5 (trade/pipeline.py)
```

## 주의사항
- ML 모델 추가 후 반드시 paper trading으로 2주 이상 검증 후 실거래 전환
- 모델 파일 (`data/models/`) 은 git에 커밋하지 말 것
- `ml_signal_enabled: false` 로 시작해서 단계적으로 켜기
- 재학습 중 모델 교체 시 atomic swap 처리 필요 (race condition 방지)
