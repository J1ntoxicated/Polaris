# Auto Invasion — 레포 분석 & 적용 플랜

> INVASION v10 개선을 위한 오픈소스 레포지토리 분석 결과.
> Claude Code에서 이 파일들을 참고해서 코드 플랜 실행할 것.

---

## 파일 구조

| 파일 | 내용 |
|------|------|
| `01_REPOS_ANALYSIS.md` | 10개 레포 구조 & 핵심 기능 분석 |
| `02_INVASION_APPLY.md` | INVASION 각 레이어별 적용 포인트 매핑 |
| `03_PHASE1_PLAN.md` | Phase 1 — 즉시 적용 (위험 낮음) |
| `04_PHASE2_PLAN.md` | Phase 2 — ML 시그널 도입 |
| `05_PHASE3_PLAN.md` | Phase 3 — 구조 개선 (장기) |

---

## 레포 우선순위 요약

| 순위 | 레포 | Stars | 핵심 가치 | Phase |
|------|------|-------|----------|-------|
| 1 | freqtrade | 48K | FreqAI(ML 시그널), Hyperopt(파라미터 최적화) | 2 |
| 2 | nautilus_trader | 6K | 이벤트 버스 설계, 백테스트↔라이브 parity | 3 |
| 3 | Qlib | 16K | Alpha158 피처셋, LGBM/LSTM 파이프라인 | 2 |
| 4 | hftbacktest | 4K | 오더북 틱 백테스팅, exit 검증 | 3 |
| 5 | FinRL | 10K | RL 포지션 사이징, 리워드 함수 설계 | 3 |
| 6 | backtesting.py | 5K | 경량 전략 검증, Optuna 통합 | 1 |
| 7 | Jesse | 6K | Monte Carlo 오버피팅 탐지 | 1 |
| 8 | Hummingbot | 18K | 마켓메이킹, 인벤토리 스큐 | 3 |
| 9 | quant-trading | 5K | 전략 코드 모음 (Dual Thrust, London Breakout) | 1 |
| 10 | mlfinlab | 4.7K | Triple Barrier 레이블링, Kelly 사이징 | 1 |

---

## 적용 순서

```
Phase 1 — 즉시 (위험 낮음, 효과 빠름)
  mlfinlab       → Triple Barrier 레이블 개선 (prediction/bayesian.py)
  quant-trading  → Dual Thrust, London Breakout 전략 추가
  backtesting.py → 전략 빠른 검증 도구 셋업
  Jesse          → Monte Carlo 오버피팅 탐지 모듈

Phase 2 — 중기 (ML 도입)
  freqtrade FreqAI → ML 시그널 프로바이더 추가 (signals/providers_extended.py)
  Qlib Alpha158   → 피처 추출 → providers_extended.py 이식

Phase 3 — 장기 (구조 개선)
  nautilus_trader → 백테스트 파이프라인 재설계 (strategy/backtester.py)
  hftbacktest     → exit 로직 틱 단위 검증 (trade/exit.py)
  FinRL           → Sizer RL 실험 (trade/sizer.py)
  Hummingbot      → MM 전략 추가
```

---

## INVASION v10 레이어 참조

```
L0 Config    → config/param_registry.py (282 params)
L1 Data      → data/collectors/ (25개 수집기)
L2 Exchange  → OKX / Capital.com / Alpaca / Binance(data only)
L3 Regime    → market/regime.py (5 states)
L4 Signal    → signal/engine.py (14 providers: 8base + 3price + 3ext)
L5 Strategy  → strategy/engine.py + evolver.py (GA) + tournament.py (Elo)
L6 Trade     → trade/pipeline.py (9-gate entry)
L7 AI        → ai/live.py (Gemini 90% + Claude critical)
L8 Ops/Risk  → ops/defense.py + param_governor.py
L9 Scheduler → scheduler.py (19 jobs, 1s~3600s)
L10 Dashboard→ dashboard/ (6-window)
```
