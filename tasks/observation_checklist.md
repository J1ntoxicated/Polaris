# Hourly Observation Checklist

11 commit 배포 후 관찰 지점 (2026-04-21 15:05 AEST 이후).

## 🎯 Learner 작동
- `profit_cap_{crypto|forex|indices|commodity|etf|stock}` preg 값 변화
- `fsm_harvest_trail_mult` (0.5 → ?)
- `bep_activate` (0.5% → ?)
- `time_exit_max_age_sec_{group}` (forex 3600/indices 4500/commodity 5400 → ?)
- `regime_size_mult_{regime}` 자동 튠
- 로그: `EXIT_LEARNER`, `MAX_HOLD_LEARNER`, `REGIME_LEARNER`, `SESSION_LEARNER` 건수

## 🔄 Live Recalc
- `LIVE_EXIT_RECALC` 로그 발동
- TTL 30s cache 동작
- Regime flip 시 pos.exit_params 변화 검증

## 📊 북극성 Asymmetry (KPI)
- Winner avg vs Loser avg (baseline: +0.32% / -0.33%)
- TP 평균 (baseline: 0.45%)
- Expectancy per trade (baseline: -$1.97)

## 🎪 Normalize 계열
- Cell matrix `SKIP` / `AMPLIFY` 발동 건수
- `CELL_MATRIX_SKIP` 로그
- `LIQUIDITY_CLAMP` 건수 (baseline ratio 적용 후)
- `AtrExpAmp` (threshold 2.0 normalized)
- `CONVICTION` (multi-strategy)

## 📈 Exchange 밸런스
- OKX / CAP / Alpaca 1h: entry n / WR / pnl
- CAP forex 65min avg -0.055% 개선 여부
- OKX 66 open 회전 속도

## 🚨 긴급 임계
- 1h DD > -$500
- WR < 15% on 30+ trades
- ERROR / CRITICAL / STOP BLIND spike
- Adopted 누적 pnl
