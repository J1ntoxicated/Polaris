# 03 — Phase 1: 즉시 적용 플랜

> 위험 낮음. 기존 구조 변경 없이 추가/개선 위주.
> 각 태스크는 독립적으로 실행 가능.

---

## Task 1-A: Triple Barrier 레이블 개선
**참고 레포**: mlfinlab  
**대상 파일**: `invasion/prediction/bayesian.py`  
**예상 공수**: 소 (1~2시간)

### 현재 상태
- trade.closed 후 win/loss 이진 레이블로 Bayesian prior 업데이트

### 목표
- Triple Barrier 방식으로 레이블 세분화
  - TP 도달 → +1 (좋은 진입)
  - SL 도달 → -1 (나쁜 진입)
  - 시간 초과 → 0 (중립 — 기회비용)

### 구현 스텝
```
1. bayesian.py 의 update() 메서드 파악
2. exit_type 컬럼 활용 (trades 테이블에 이미 있음)
   - exit_type == 'trail' or 'hard_stop' → -1
   - exit_type == 'take_profit' → +1
   - exit_type == 'time_stop' or 'flat_kill' → 0
3. 레이블별 prior 업데이트 가중치 차등 적용
   - +1: 강한 업데이트
   -  0: 약한 업데이트
   - -1: 강한 다운데이트
4. param_registry에 tb_weight_tp, tb_weight_neutral, tb_weight_sl 추가
```

### 검증
```bash
python3 -c "import invasion.prediction.bayesian"
# trades 테이블에서 exit_type 분포 확인
python3 -c "
from invasion.data.store import DataStore
ds = DataStore()
trades = ds.get_closed_trades(limit=100)
print({t['exit_type']: 0 for t in trades})
"
```

---

## Task 1-B: Dual Thrust 전략 추가
**참고 레포**: quant-trading  
**대상 파일**: `invasion/strategy/` 새 전략 파일  
**예상 공수**: 중 (3~4시간)

### 목표
- FX 그룹 (Capital.com) + 선물 그룹에 Dual Thrust 브레이크아웃 전략 추가

### 구현 스텝
```
1. strategy/engine.py에서 전략이 어떻게 등록되는지 파악
2. 신규 전략 딕셔너리 항목 추가:
   {
     'name': 'dual_thrust',
     'match_groups': ['FX', 'CMD'],
     'match_dirs': ['long', 'short'],
     'entry_params': {
       'k1': 0.5,   # 상단 돌파 배수
       'k2': 0.5,   # 하단 돌파 배수
       'lookback': 1 # 전일 기준
     },
     'exit_params': { ... },
     'sizing_params': { ... }
   }
3. 시그널 로직 구현:
   - candles 테이블에서 전일 OHLC 가져오기
   - range = max(HH - LC, HC - LL)
   - upper = open + k1 * range → long 진입 조건
   - lower = open - k2 * range → short 진입 조건
4. param_registry에 dual_thrust_k1, dual_thrust_k2 등록
5. strategies DB 테이블에 삽입
```

### 검증
```bash
python3 -m invasion --headless &
sleep 10
# logs에서 dual_thrust 전략 선택 여부 확인
grep "dual_thrust" data/invasion.log
```

---

## Task 1-C: London Breakout 전략 추가
**참고 레포**: quant-trading  
**대상 파일**: `invasion/strategy/` 신규 + `invasion/utils/market_hours.py`  
**예상 공수**: 중 (3~4시간)

### 목표
- Capital.com FX 페어 (EUR/USD, GBP/USD 등)에 London Breakout 전략 추가

### 구현 스텝
```
1. market_hours.py에서 런던 세션 시간 상수 확인/추가
   LONDON_OPEN_UTC = 7   # 07:00 UTC
   LONDON_RANGE_START_UTC = 6  # 06:00 UTC (pre-open 레인지)

2. 전략 로직:
   - 06:00~07:00 UTC: 레인지 High/Low 기록
   - 07:00 UTC 이후: High 돌파 → long, Low 돌파 → short
   - 13:00 UTC (NY 오픈) 이후 신규 진입 중단
   - 20:00 UTC 이전 모든 포지션 청산 (flat_kill)

3. FX 그룹 전용 플래그 추가:
   match_groups: ['FX']
   session_filter: 'london'

4. param_registry에 등록:
   london_range_hours: 1
   london_entry_delay_min: 0
   london_cutoff_hour: 13
```

### 검증
```bash
# Capital.com FX 포지션 로그 확인
grep "london_breakout" data/invasion.log
```

---

## Task 1-D: Optuna 기반 파라미터 최적화
**참고 레포**: backtesting.py, Jesse  
**대상 파일**: `invasion/analytics/grid_search.py`  
**예상 공수**: 소~중 (2~3시간)

### 목표
- 현재 단순 grid search → Optuna Bayesian 탐색으로 업그레이드

### 구현 스텝
```
1. 의존성 추가
   pip install optuna

2. analytics/grid_search.py 에 OptunaTuner 클래스 추가

3. 탐색 파라미터 정의:
   min_signal_score: suggest_float(50, 85)
   trail_pct_1: suggest_float(0.3, 2.0)
   trail_pct_2: suggest_float(0.5, 3.0)
   hard_stop_pct: suggest_float(1.0, 5.0)
   max_positions: suggest_int(3, 15)

4. objective 함수:
   → 백테스트 실행 (strategy/backtester.py 활용)
   → Sharpe Ratio 반환

5. 실행:
   study = optuna.create_study(direction='maximize')
   study.optimize(objective, n_trials=200, n_jobs=4)
   best_params = study.best_params

6. 결과를 param_registry에 자동 적용 또는 리포트 생성
```

### 검증
```bash
python3 -c "
from invasion.analytics.grid_search import OptunaTuner
tuner = OptunaTuner()
result = tuner.run(n_trials=10)  # 빠른 테스트
print(result.best_params)
"
```

---

## Task 1-E: Monte Carlo 오버피팅 탐지
**참고 레포**: Jesse  
**대상 파일**: `invasion/analytics/monte_carlo.py` 신규  
**예상 공수**: 소 (1~2시간)

### 목표
- 전략 진화(evolver) 후 오버피팅 전략 자동 탐지 및 제거

### 구현 스텝
```
1. analytics/monte_carlo.py 신규 생성

2. MonteCarloValidator 클래스:
   def validate(strategy_id, trades, n_simulations=1000):
       base_sharpe = compute_sharpe(trades)
       shuffled_sharpes = []
       for _ in range(n_simulations):
           shuffled = random.sample(trades, len(trades))
           shuffled_sharpes.append(compute_sharpe(shuffled))
       
       percentile = percentileofscore(shuffled_sharpes, base_sharpe)
       is_overfit = percentile > 95  # 상위 5% → 운으로 판정
       return is_overfit, percentile

3. evolution.py 의 _run_evolution() 완료 후 호출:
   for strategy in evolved_strategies:
       is_overfit, pct = MonteCarloValidator.validate(strategy.id, trades)
       if is_overfit:
           log_event(f"Strategy {strategy.id} flagged as overfit ({pct:.1f}%ile)")
           strategy.status = 'overfit'  # 토너먼트 제외
```

### 검증
```bash
python3 -c "
from invasion.analytics.monte_carlo import MonteCarloValidator
# 더미 트레이드로 테스트
import random
trades = [{'pnl_pct': random.gauss(0.5, 1.0)} for _ in range(50)]
is_overfit, pct = MonteCarloValidator.validate('test', trades)
print(f'overfit={is_overfit}, percentile={pct:.1f}')
"
```

---

## Phase 1 완료 체크리스트

```
[ ] Task 1-A: Triple Barrier 레이블 (prediction/bayesian.py)
[ ] Task 1-B: Dual Thrust 전략 추가
[ ] Task 1-C: London Breakout 전략 추가
[ ] Task 1-D: Optuna 파라미터 최적화 (analytics/grid_search.py)
[ ] Task 1-E: Monte Carlo 오버피팅 탐지 (analytics/monte_carlo.py)
```

## Pre-flight (각 Task 전)
```bash
python3 -c "import invasion.main"   # import 정상 확인
```

## Post-flight (각 Task 후)
```bash
python3 -c "import invasion.main"
python3 -m invasion --headless      # 5초 후 Ctrl+C
grep "ERROR" data/invasion.log | tail -5
```
