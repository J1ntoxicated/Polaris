---
type: research
status: active
date_created: 2026-05-29
date_updated: 2026-05-29
tags: [research, profit-structure, backlog, alpha, execution, sizing]
---

# 수익구조 차용 백로그 (GitHub + 논문, 검증)

DEMO/PAPER, aggressive bias 유지 — 전부 수익 극대화·거래기회 확대·재배분 방향(축소/방어 throttle 아님). 포렌식([[2026-05-29_loss_forensic_fee_overtrading]])과 수렴: 손실 주범(과협소 universe / 슬리피지·fee / over-trade)에 top 항목이 직접 대응.

## TOP 3 즉시 시도
1. **Universe cross-sectional 랭킹 확장 (6→30+)** — 4-axis 하드컷(189→6)을 `vol_usd×realized_vol` 연속 랭킹 상위 N으로. 저유동일수록 anomaly 수익↑. cell-matrix가 후단서 약후보 down-route. Layer 0 `discover_universe`. 출처: Starkiller cross-sectional momentum(30d/7d, quintile spread); ScienceDirect S1057521921002349.
2. **Market→maker/limit(post-only) 실행 + 타임아웃 market 폴백** — taker(0.06%)+슬리피지 이중누수 차단, maker(0.02%). 대형은 TWAP/iceberg. `executing-orders`/OKX adapter. 출처: Talos TCA; NautilusTrader exec instructions.
3. **Vol-targeting 사이징을 T4 continuous scalar에 결합** — position = signal×(target_vol/realized_vol), ex-ante만. tier amplifier·cell·Kelly 보존, scalar만 vol-aware. Sharpe 구조 개선. 출처: Harvey et al. Vol Targeting; ScienceDirect S1386418116301379.

## 추가 백로그 (impact×난이도)
4. **Signal persistence / cooldown** — 5초 tick noise re-trigger 완화(MA 평활 또는 매도후 pair 쿨다운 N캔들). turnover·fee↓. freqtrade CooldownPeriod 패턴. **P&L halt 아님 = philosophy 일치**. → 포렌식 P1(over-trade)와 직결.
5. **Regime detection(vol/trend·HMM)을 cell score에 결합** — trend·low-vol 코어레짐서 추세전략 가중, 역추세 down. Layer 4/5. 출처: QuantStart/QuantifiedStrategies HMM.
6. **Meta-labeling(triple-barrier)** — 1차 신호 결과 라벨링 → 2차 act/skip+size. Post-Trade Reflector가 TP/SL/timeout 라벨 기록 → learner. 표본 적채 전엔 수집만. 출처: López de Prado AFML; Hudson&Thames.
7. **Funding carry (perp 확장 시)** — 시장중립 캐리, 비용후 +는 40%만. 아키텍처 변경 큼 → 보류.
8. **OSS 구조 차용**: freqtrade Protections(가드 stacking 인터페이스) / NautilusTrader(post-only·iceberg·OCO order intent 스키마) / awesome-systematic-trading 인덱스.

## 주의
- 단일 소스 과장(arXiv Sharpe 2.41, Starkiller in-sample) = 방향성 증거로만, 파라미터는 자체 백테스트 재검증. vol-targeting은 ex-ante(look-ahead 금지).
- 거부키워드 sweep 0건.

## 구현 순서 권고
(1)+(4) universe 확장+cooldown 동시 → (2) maker/limit → (3) vol-scaling. 1·2·4 = 오늘 손실 직접 대응 빠른 승리.
