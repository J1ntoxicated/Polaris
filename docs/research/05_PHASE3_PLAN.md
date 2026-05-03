# 05 — Phase 3: 구조 개선 (장기)

> Phase 2 완료 후 진행.
> 기존 구조 변경 포함 — Jin 승인 필요 항목 있음.

---

## Task 3-A: Exit 로직 틱 단위 검증
**참고 레포**: hftbacktest  
**대상**: `invasion/trade/exit.py` 검증 및 슬리피지 모델 개선  
**승인 필요**: No (검증/개선 범위)  
**예상 공수**: 중 (1일)

### 목표
- 현재 1s 틱 기반 exit이 실제 틱 데이터와 얼마나 차이나는지 측정
- 슬리피지 모델 개선

### 구현 스텝
```
1. hftbacktest 설치
   pip install hftbacktest

2. OKX 틱 데이터 수집 스크립트 작성
   tools/collect_tick_data.py
   → OKX WS에서 trade tick 실시간 수집 → parquet 저장

3. 기존 백테스터와 hftbacktest 결과 비교
   - 동일 기간, 동일 전략
   - exit 타이밍 차이 측정 (평균 몇 틱 지연?)
   - 슬리피지 실측치 계산

4. 결과 기반 exit.py 개선:
   - hard_stop 실행 시 슬리피지 보정 파라미터 추가
   - param_registry: exit_slippage_bps (basis points)

5. 백테스터 슬리피지 모델 업데이트:
   strategy/backtester.py 의 fill_price 계산에 slippage 반영
```

### 검증 지표
```
평균 exit 지연: target < 2 ticks
슬리피지 실측: target < 3 bps (0.03%)
백테스트 vs 실거래 exit price 차이: target < 5 bps
```

---

## Task 3-B: 다층 트레일링 SL 개선
**참고 레포**: freqtrade (커스텀 스탑로스)  
**대상 파일**: `invasion/trade/exit.py`  
**승인 필요**: No  
**예상 공수**: 소~중 (2~3시간)

### 현재 구조
```python
# exit.py trail_tiers (추정)
trail_pct = param_registry.get('trail_pct')  # 단일값
```

### 목표 구조 (freqtrade 참고)
```python
# pnl 구간별 동적 트레일링
def compute_trail_stop(pos, current_pnl):
    peak_pnl = pos.get('peak_pnl', current_pnl)

    if current_pnl < 0.005:      # 0.5% 미만
        return pos['entry_price'] * (1 - hard_stop_pct)

    elif current_pnl < 0.010:    # 0.5~1%
        trail = peak_pnl - 0.003  # peak 대비 -0.3%
    elif current_pnl < 0.020:    # 1~2%
        trail = peak_pnl - 0.005  # peak 대비 -0.5%
    else:                         # 2% 이상
        trail = peak_pnl - 0.003  # peak 대비 -0.3% (수익 보호)

    return pos['entry_price'] * (1 + trail)
```

### param_registry 추가
```
trail_tier1_threshold: 0.005   # 0.5%
trail_tier2_threshold: 0.010   # 1.0%
trail_tier3_threshold: 0.020   # 2.0%
trail_tier1_pct: 0.003
trail_tier2_pct: 0.005
trail_tier3_pct: 0.003
```

---

## Task 3-C: Kelly Criterion 포지션 사이징
**참고 레포**: mlfinlab  
**대상 파일**: `invasion/trade/sizer.py`  
**승인 필요**: Yes (포지션 사이징 변경은 리스크 직결)  
**예상 공수**: 중 (4~6시간)

### 현재 구조
```
size = base × tier × regime × score × streak × session × ticker
(7 multipliers)
```

### 목표: Kelly fraction을 8번째 승수로 추가
```python
def kelly_multiplier(ticker, direction) -> float:
    # ticker_performance 테이블에서 최근 성과 가져오기
    perf = DataStore().get_ticker_performance(ticker, direction, window='7d')
    win_rate = perf.get('win_rate', 0.5)
    avg_win = perf.get('avg_pnl_pct', 0.01)   # 평균 수익
    avg_loss = abs(perf.get('avg_loss_pct', 0.01))  # 평균 손실

    if avg_loss < 1e-8:
        return 1.0

    # Kelly fraction
    b = avg_win / avg_loss   # profit ratio
    f = win_rate - (1 - win_rate) / b

    # half Kelly 적용 (기본값 0.5 — Ops 데이터 기반 재튜닝 대상)
    f = max(0, min(f * 0.5, 0.25))

    # 0~1 사이 multiplier로 변환
    # f=0 → 0.5배, f=0.25 → 1.5배
    return 0.5 + f * 4.0

# sizer.py에서 추가
kelly_mult = kelly_multiplier(ticker, direction)
final_size = ... * kelly_mult
```

### param_registry 추가
```
kelly_enabled: false  # 처음엔 꺼두고 paper trading 검증 후 켜기
kelly_fraction: 0.5   # half Kelly
kelly_cap: 0.25       # 최대 kelly fraction
kelly_min_trades: 20  # 최소 데이터 필요
```

---

## Task 3-D: 백테스트 파이프라인 개선
**참고 레포**: nautilus_trader  
**대상 파일**: `invasion/strategy/backtester.py`  
**승인 필요**: No (기존 코드 유지, 별도 스크립트)  
**예상 공수**: 대 (2~3일)

### 목표
- 백테스트↔라이브 코드 parity 개선
- 멀티 전략 동시 백테스트 지원
- 파라미터 그리드 병렬 실행

### 구현 방향
```python
# tools/backtest_runner.py 신규

from invasion.strategy.backtester import InvasionBacktester

configs = [
    {'trail_pct': 1.0, 'min_signal_score': 60},
    {'trail_pct': 1.5, 'min_signal_score': 65},
    {'trail_pct': 2.0, 'min_signal_score': 70},
]

results = []
for cfg in configs:
    bt = InvasionBacktester(params=cfg)
    result = bt.run(
        start='2025-01-01',
        end='2025-04-01',
        tickers=['BTC-USDT-SWAP', 'ETH-USDT-SWAP']
    )
    results.append((cfg, result))

# 결과 비교 (Sharpe, MaxDD, WinRate)
best = max(results, key=lambda x: x[1]['sharpe'])
```

### nautilus_trader 참고 포인트
- `BacktestNode`: 여러 설정 동시 실행, 각 런 독립 상태
- 데이터는 run 전에 모두 로드, sort=False로 각각 추가 후 마지막에 한 번 sort
- 결과는 `TradingNode.get_last_quote_tick()` 등으로 접근

---

## Task 3-E: 마켓메이킹 전략 (실험적)
**참고 레포**: Hummingbot  
**대상**: 신규 exchange adapter + strategy  
**승인 필요**: Yes (새 거래 방식)  
**예상 공수**: 대 (1주+)

### 목표
- OKX 크립토 스팟/선물에서 Pure Market Making 전략 추가
- 스프레드 수익 + 펀딩 피 수익 복합

### Hummingbot PMM 참고 핵심
```
핵심 로직:
  mid_price = (best_bid + best_ask) / 2
  bid = mid_price * (1 - bid_spread)
  ask = mid_price * (1 + ask_spread)

인벤토리 스큐 (Hummingbot 방식):
  inventory_ratio = base_balance / total_value
  target_ratio = 0.5
  skew = (target_ratio - inventory_ratio) * skew_factor
  bid = mid * (1 - bid_spread + skew)
  ask = mid * (1 + ask_spread + skew)

주문 갱신:
  매 N초마다 기존 주문 취소 → 새 호가 제출
```

### 주의사항
- OKX 스팟 API와 선물 API 분리 필요
- 인벤토리 관리 복잡 (기존 PortfolioManager와 충돌 가능)
- Jin 승인 후 paper trading 충분히 검증 필수

---

## Task 3-F: RL 포지션 사이징 (실험적)
**참고 레포**: FinRL  
**대상**: 별도 research 환경  
**승인 필요**: Yes  
**예상 공수**: 대 (2주+)

### 목표
- PPO 에이전트가 포지션 크기 결정
- 현재 7승수 규칙 기반 → 학습 기반으로 실험

### FinRL 참고 구조
```python
# 환경 설계
state = [
    current_pnl, open_positions, equity, regime_code,
    composite_score, vix, fear_greed, funding_rate,
    recent_win_rate, drawdown, time_of_day
]
action = [0.0 ~ 1.0]  # 포지션 크기 비율
reward = sharpe_delta - 0.1 * volatility_penalty

# 학습
from stable_baselines3 import PPO
model = PPO('MlpPolicy', env, verbose=1)
model.learn(total_timesteps=100_000)
```

### 주의
- 실거래 적용 전 paper trading 최소 1개월
- 기존 sizer.py는 fallback으로 유지
- `rl_sizer_enabled: false` 기본값

---

## Phase 3 완료 체크리스트

```
[ ] Task 3-A: Exit 틱 단위 검증 (hftbacktest)
[ ] Task 3-B: 다층 트레일링 SL 개선 (exit.py)
[ ] Task 3-C: Kelly Criterion 사이징 (sizer.py) — Jin 승인 필요
[ ] Task 3-D: 백테스트 파이프라인 개선 (backtester.py)
[ ] Task 3-E: 마켓메이킹 전략 — Jin 승인 필요
[ ] Task 3-F: RL 포지션 사이징 (실험) — Jin 승인 필요
```

## Jin 승인 필요 항목 목록
```
3-C: Kelly Criterion → 포지션 사이징 변경 (리스크 직결)
3-E: 마켓메이킹 전략 → 새 거래 방식 도입
3-F: RL 포지션 사이징 → 실험적 대규모 변경
```
