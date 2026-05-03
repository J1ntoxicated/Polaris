# 01 — 레포지토리 분석

> github.com 직접 검색 & 문서 기반으로 검증된 내용만 기록.

---

## 1. freqtrade
**github.com/freqtrade/freqtrade** | ⭐48K | Python | GPL-3.0

### 구조
```
freqtrade/
├── freqtrade/
│   ├── freqai/          # ML 파이프라인 핵심
│   │   ├── base_models/ # LightGBM, XGBoost, CatBoost, PyTorch
│   │   └── rl/          # 강화학습 모델
│   ├── optimize/
│   │   └── hyperopt.py  # Bayesian 파라미터 최적화
│   ├── strategy/
│   │   └── interface.py # IStrategy 기본 클래스
│   └── templates/
│       └── FreqaiExampleStrategy.py
```

### FreqAI 핵심 개념
- **피처 엔지니어링**: 전략 내에서 `feature_engineering_expand_all()` 정의 → 자동으로 멀티타임프레임 × 멀티페어 확장
- **레이블**: `set_freqai_targets()` 에서 미래 N캔들 수익률로 설정
- **지속 재학습**: `continual_learning: true` → 이전 모델 가중치 기반 점진적 학습
- **백테스트**: 과거 구간을 윈도우 단위로 분할, 각 윈도우마다 모델 재학습 → 룩어헤드 바이어스 없음

### Hyperopt 핵심 개념
- Bayesian 탐색 (optuna 기반), 500~1000 epoch가 적정
- Loss function 선택: `SharpeHyperOptLoss`, `SortinoHyperOptLoss`, `OnlyProfitHyperOptLoss`
- 전략 파라미터에 `DecimalParameter`, `IntParameter` 달면 자동 탐색 대상

### 커스텀 스탑로스 구조 (다층 트레일링)
```python
pHSL = DecimalParameter(-0.200, -0.040, default=-0.10)  # 하드 스탑
pPF_1 = DecimalParameter(0.008, 0.020, default=0.016)   # 1차 이익 임계
pSL_1 = DecimalParameter(0.008, 0.020, default=0.011)   # 1차 스탑 레벨
pPF_2 = DecimalParameter(0.040, 0.100, default=0.070)   # 2차 이익 임계
pSL_2 = DecimalParameter(0.020, 0.070, default=0.030)   # 2차 스탑 레벨
# pnl < PF_1 → hard stop / PF_1~PF_2 → 선형 보간 / PF_2 이상 → SL_2 + 초과분
```

### 사용 방법
```bash
pip install freqtrade
freqtrade new-strategy --strategy MyStrategy --template advanced
freqtrade hyperopt --hyperopt-loss SharpeHyperOptLoss --strategy MyStrategy -e 500
freqtrade backtesting --strategy FreqaiExampleStrategy --freqaimodel LightGBMRegressor
```
> 우리 봇과 직접 통합 X. 코드 구조 참고 후 INVASION에 직접 구현.

---

## 2. nautilus_trader
**github.com/nautechsystems/nautilus_trader** | ⭐6K | Python/Rust/Cython | LGPL-3.0

### 구조
```
nautilus_trader/
├── nautilus_core/   # Rust 네이티브 코어
│   ├── backtest/    # BacktestEngine (Rust)
│   ├── common/      # Clock, MessageBus
│   └── execution/   # ExecutionEngine
├── nautilus_trader/ # Python 제어 플레인
│   ├── backtest/    # BacktestNode (고수준 API)
│   ├── live/        # LiveExecutionEngine
│   └── trading/     # Strategy 기본 클래스
```

### 핵심 개념
- **Research-to-live parity**: 백테스트와 라이브가 동일 전략 코드 사용
- **나노초 클락**: 백테스트와 라이브 모두 동일 time model
- **MessageBus**: pub/sub, 토픽 기반, Rust 네이티브로 초저지연
- **BacktestNode (고수준)**: 여러 설정 동시 실행, 각 런은 독립 엔진
- **BacktestEngine (저수준)**: 직접 제어, 컴포넌트 교체 가능

### 사용 방법
```bash
pip install nautilus_trader
# 또는 Docker
docker pull ghcr.io/nautechsystems/jupyterlab:nightly
docker run -p 8888:8888 ghcr.io/nautechsystems/jupyterlab:nightly
```

### 백테스트 예시
```python
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import BacktestRunConfig

configs = [BacktestRunConfig(...), BacktestRunConfig(...)]
node = BacktestNode(configs=configs)
results = node.run()  # 병렬 실행, 각 런 독립 상태
```

---

## 3. Qlib (Microsoft)
**github.com/microsoft/qlib** | ⭐16K | Python | MIT

### 구조
```
qlib/
├── qlib/
│   ├── data/         # DataServer, DataHandler, Processor
│   ├── model/        # LGBModel, LSTMModel, GRU, Transformer 등
│   ├── strategy/     # TopkDropoutStrategy, WeightStrategyBase
│   ├── backtest/     # Exchange, Account, Position
│   └── workflow/     # Experiment, Recorder, Task
├── examples/
│   └── benchmarks/
│       ├── LightGBM/ # workflow_config_lightgbm_Alpha158.yaml
│       └── MLP/      # workflow_config_mlp_Alpha360.yaml
```

### Alpha158 피처셋 (158개)
- **가격 파생**: ROCP, ROEQ, MA, STD, BETA, RSQR, RESI, MAX, MIN, QTLU, QTLD
- **거래량 파생**: KMID, KLEN, KMID2, KUP, KUP2, KLOW, KLOW2, KSFT, KSFT2
- **멀티 타임프레임**: 5/10/20/30/60일 롤링
- 총 158개 피처 자동 생성

### 레이어 구조
```
Infrastructure: DataServer → Trainer → Dataloader
Workflow:       InfoExtractor → ForecastModel → DecisionGenerator → Executor
Interface:      Analyser (리포트, IC, 포트폴리오 분석)
```

### 사용 방법
```bash
pip install pyqlib
python scripts/get_data.py qlib_data --name qlib_data_simple --target_dir ~/.qlib/qlib_data/cn_data
qrun examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml
```

---

## 4. hftbacktest
**github.com/nkaz001/hftbacktest** | ⭐4K | Python/Rust | MIT

### 핵심 개념
- **오더북 완전 재현**: Level-2 (Market-By-Price), Level-3 (Market-By-Order) 지원
- **레이턴시 모델**: 피드 레이턴시 + 오더 레이턴시 분리 시뮬레이션
- **큐 포지션**: 지정가 주문이 오더북 큐에서 몇 번째인지 추적
- **Numba JIT**: `@njit` 데코레이터로 Python 속도 한계 극복
- Binance/Bybit 실거래 예제 포함

### 핵심 API
```python
from hftbacktest import HftBacktest, HashMapMarketDepthBacktest

@njit
def my_strategy(hbt):
    while hbt.elapse(10_000_000) == 0:  # 10ms마다
        depth = hbt.depth(0)
        mid = (depth.best_bid + depth.best_ask) / 2
        # 오더 제출
        hbt.submit_buy_order(0, order_id, price, qty, GTX, LIMIT, False)
        hbt.wait_order_response(0, order_id, timeout=5_000_000_000)
```

### 사용 방법
```bash
pip install hftbacktest
# OKX 틱 데이터 수집 후 백테스트
```

---

## 5. FinRL
**github.com/AI4Finance-Foundation/FinRL** | ⭐10K | Python | MIT

### 구조
```
finrl/
├── agents/
│   ├── elegantrl/      # ElegantRL 기반 DRL
│   ├── rllib/          # Ray RLlib 기반
│   └── stablebaseline3/ # Stable Baselines3 기반
├── meta/
│   ├── env_stock_trading/      # 주식 거래 환경
│   ├── env_cryptocurrency_trading/ # 크립토 거래 환경
│   └── env_portfolio_allocation/   # 포트폴리오 환경
└── applications/
    └── cryptocurrency_trading/
```

### 핵심 개념
- **State space**: 가격, 기술적 지표, 보유량, 잔액 등 벡터
- **Action space**: {-1, 0, 1} (매도/홀드/매수) 또는 연속값 (포지션 크기 비율)
- **Reward**: 포트폴리오 가치 변화 (변동성 페널티 추가 가능)
- **DRL 알고리즘**: PPO, SAC, TD3, A2C, DDPG, DQN

### FinRL-Trading (최신)
```
src/
├── strategies/
│   ├── base_strategy.py   # 추상 전략
│   └── ml_strategy.py     # Random Forest 주식 선택
├── trading/
│   └── alpaca_manager.py  # Alpaca 직접 연동
```

### 사용 방법
```bash
pip install finrl
# 또는 최신
git clone https://github.com/AI4Finance-Foundation/FinRL-Trading
./deploy.sh --strategy adaptive_rotation --mode backtest
```

---

## 6. backtesting.py
**github.com/kernc/backtesting.py** | ⭐5K | Python | AGPL-3.0

### 핵심 개념
- 경량, 인터랙티브 HTML 차트 (Bokeh)
- `Strategy` 클래스 상속 → `init()` + `next()` 구현
- `Backtest.optimize()`: grid search + Optuna 선택 가능
- 수수료, 슬리피지 파라미터 내장

### 사용 방법
```bash
pip install backtesting
```
```python
from backtesting import Backtest, Strategy

class MyStrategy(Strategy):
    def init(self):
        self.ma = self.I(lambda x: pd.Series(x).rolling(20).mean(), self.data.Close)
    def next(self):
        if self.data.Close[-1] > self.ma[-1]:
            self.buy()

bt = Backtest(data, MyStrategy, cash=10000, commission=0.002)
stats = bt.run()
bt.plot()
```

---

## 7. Jesse
**github.com/jesse-ai/jesse** | ⭐6K | Python | MIT

### 핵심 개념
- 깔끔한 전략 API: `should_long()`, `should_short()`, `go_long()`, `go_short()`
- 내장 ML 파이프라인: `record_features()` → `train_model()` → `ml_predict()`
- **Monte Carlo 분석**: 트레이드 순서 셔플링 → 오버피팅 탐지
- Optuna 하이퍼파라미터 최적화 내장

### 전략 구조
```python
from jesse.strategies import Strategy
import jesse.indicators as ta

class MyStrategy(Strategy):
    def hyperparameters(self):
        return [
            {'name': 'slow_sma', 'type': int, 'min': 150, 'max': 210, 'default': 200},
        ]

    @property
    def slow_sma(self):
        return ta.sma(self.candles, self.hp['slow_sma_period'])

    def should_long(self) -> bool:
        return self.close > self.slow_sma

    def should_short(self) -> bool:
        return self.close < self.slow_sma

    def go_long(self):
        self.buy = 1, self.close
        self.stop_loss = 1, self.close * 0.98
        self.take_profit = 1, self.close * 1.03
```

### 사용 방법
```bash
pip install jesse
jesse make-project myproject
jesse backtest --debug
jesse monte-carlo  # 오버피팅 탐지
```

---

## 8. Hummingbot
**github.com/hummingbot/hummingbot** | ⭐18K | Python/C++ | Apache-2.0

### 핵심 전략
1. **Pure Market Making (PMM)**: 단일 페어, 스프레드 기반 bid/ask 동시 호가
2. **Cross-Exchange MM**: 한 거래소 메이커 + 다른 거래소 테이커 헤지
3. **AMM Arbitrage**: DEX AMM과 CEX 간 차익거래

### PMM 핵심 파라미터
```
bid_spread / ask_spread: 호가 스프레드
order_refresh_time: 주문 갱신 주기 (초)
order_amount: 주문 수량
inventory_skew: 포지션 편향에 따라 호가 비대칭 조정
price_ceiling / price_floor: 가격 상하한 밴드
```

### 아키텍처
- **Clock-based loop**: 1s 틱 기반 (우리 scheduler와 동일 개념)
- **Connector 추상화**: 거래소별 REST/WS 표준화
- **Controller 패턴**: 결정(전략) + 실행(오더) 분리

### 사용 방법
```bash
git clone https://github.com/hummingbot/hummingbot
docker compose up -d
docker attach hummingbot
# > start --script simple_pmm.py --conf conf_simple_pmm_sol.yml
```

---

## 9. quant-trading
**github.com/je-suis-tm/quant-trading** | ⭐5K | Python | MIT

### 포함 전략 목록
| 전략 | 타입 | 적합 자산 |
|------|------|----------|
| Dual Thrust | 브레이크아웃 | FX, 선물 |
| London Breakout | 세션 브레이크아웃 | FX (GBP/USD 등) |
| Bollinger Bands | 평균회귀 | 전체 |
| RSI Pattern Recognition | 모멘텀 | 전체 |
| Pair Trading | 통계적 차익 | 상관 자산 쌍 |
| Heikin-Ashi | 추세추종 | 전체 |
| Parabolic SAR | 추세추종 | 전체 |
| MACD | 모멘텀 | 전체 |
| Monte Carlo | 리스크 분석 | 포트폴리오 |
| VIX Calculator | 변동성 | 지수 |
| Options Straddle | 변동성 매매 | 옵션 |

### Dual Thrust 핵심 로직
```python
# 오프닝 레인지 기반 브레이크아웃
range = max(HH - LC, HC - LL)  # 전일 고/저 기반 레인지
upper = open + k1 * range      # 상단 돌파 → 매수
lower = open - k2 * range      # 하단 돌파 → 매도
```

### 사용 방법
```bash
git clone https://github.com/je-suis-tm/quant-trading
# 각 전략별 독립 .py 파일 → 직접 참고해서 INVASION 포맷으로 포팅
```

---

## 10. mlfinlab
**github.com/hudson-and-thames/mlfinlab** | ⭐4.7K | Python | BSD-3

> "Advances in Financial Machine Learning" (Marcos Lopez de Prado) 구현체

### 핵심 기능
1. **Triple Barrier Method**: TP/SL/시간 3개 장벽 중 먼저 닿는 것으로 레이블
2. **Meta-Labeling**: 1차 모델(방향) + 2차 모델(진입 여부 필터링)
3. **Kelly Criterion 베팅 사이징**
4. **CUSUM 필터**: 가격 구조 변화 감지 → 이벤트 샘플링
5. **정보 기반 바**: Tick/Volume/Dollar Bars (시간 기반 아님)

### Triple Barrier 개념
```
상단 장벽 (Take Profit): 수익률 +X% 도달 → 레이블 +1
하단 장벽 (Stop Loss):   수익률 -Y% 도달 → 레이블 -1
수직 장벽 (Time Limit):  N시간 경과      → 레이블  0
→ 셋 중 먼저 닿는 장벽이 해당 거래의 레이블 결정
```

### Meta-Labeling 개념
```
1차 모델 (Primary): 방향 예측 (long/short)
2차 모델 (Meta):    진입 여부 예측 (0 or 1)
→ 1차 × 2차 = 최종 포지션 (방향 × 진입 여부)
→ False positive 크게 줄임
```

### 사용 방법
```bash
pip install mlfinlab
```
```python
import mlfinlab as ml

# Triple Barrier 레이블링
triple_barrier_events = ml.labeling.get_events(
    close=prices,
    t_events=cusum_events,
    pt_sl=[1, 2],           # TP 1배, SL 2배 변동성
    target=daily_vol,
    min_ret=0.005,
    num_threads=4,
    vertical_barrier_times=t1,
    side_prediction=sides   # 1차 모델 방향
)
labels = ml.labeling.get_bins(triple_barrier_events, prices)
```
