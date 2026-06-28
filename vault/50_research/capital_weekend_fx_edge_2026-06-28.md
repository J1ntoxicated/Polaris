---
type: research
status: validated
date_created: 2026-06-28
tags: [research, backtest, weekend, capital, fx, eurusd, reject, fee, flow-not-block, demo]
---

# Capital weekend FX 엣지 (#92) — VERDICT: NO_EDGE / 0 BUILD

DEMO/PAPER · aggressive 보존 · flow_not_block · -1.0R rail 불변.
backlink: [[weekend_gap_drift_backtest_2026-06-27]] · [[weekend_liquidity_range_maker_family_2026-06-27]] ·
[[all_strategy_edge_diagnosis_2026-06-24]]. (전 OKX 크립토 주말 리서치와 독립 수렴.)

## 1. 주말-tradeable Capital 전수 (live API, 2026-06-28 Sun 03:3z)
demo-api-capital `/api/v1/markets` searchTerm 스윕 + `/markets/{epic}` 확인:
- **EURUSD_W** = 유일 in-scope(비-crypto) 주말-tradeable. CURRENCIES, marketStatus=TRADEABLE.
  openingHours(UTC): Fri 21:05→00:00 · Sat 00:00-05:00/07:00-20:00/20:05-00:00 · Sun 00:00-19:00.
  표준 EURUSD = CLOSED(주중만). `_W` = Capital 주말 변형 접미. **다른 `_W`/Weekend 변형 0건.**
- crypto-CFD(BTCUSD/ETHUSD/BCHUSD/XRPUSD…) TRADEABLE이나 **OKX track A 전용**(asset-class routing mandate, Jin 2026-05-30) → out-of-scope.
- FX167/지수20/원자재33 전부 CLOSED(라이브 DB snapshot과 일치: capital forex 'live'=1, 나머지 closed).
→ Capital 주말 능동거래 후보 = **EURUSD_W 1개뿐**.

## 2. 실측 비용 (load-bearing): 주말 딜러 스프레드 = 16bps RT
EURUSD_W 라이브 snapshot bid 1.13796 / offer 1.13886 = **7.91bps each-way ≈ 16bps round-trip**.
표준 EURUSD 주중 ~0.5-1bps 대비 ~10-16배. 주말 인터뱅크 FX가 **진짜 닫혀** 딜러가 합성 마크를 내며
얇은 책 리스크를 스프레드로 가격함. maker 회피 불가(CFD 딜러 단일호가, post-only 오더북 없음 → entry도 taker spread).

## 3. 백테스트 — 전 각도 REJECT (real EURUSD=X 1H, 730d, ~145 주말)
데이터 핵심 사실: yfinance EURUSD=X bars-by-weekday = Sat **0봉** / Sun **78봉**(주말당 1봉, 늦은 Sydney reopen)
vs 평일 ~3400봉. **주말 intra-window 실가격 시계열이 존재하지 않음** → Capital 주말 마크는 합성. 따라서
실재하는 유일 주말 FX 시그널 = Fri-close→Mon-open **gap**(주말 윈도우 전체 이동의 상한).

**(A) 주말 gap (Fri close → Mon open), n=145:**
- mean **+0.19bps** / median +1.06 / std 21.8 → **방향성 drift ≈ 0**, pct-up 51%(코인플립).
- **abs-mean 이동 = 13.68bps < 16bps 비용 floor.** 즉 주말 전체 이동폭 자체가 RT 스프레드보다 작다.
- OOS 반분할: IS mean +0.79 / OOS −0.40 (부호 비일관).

**(B) 방향 전략 net (비용 차감):**
| 전략 | net @16bps | net @20bps RT | net @32bps(=7.91×4) |
|---|---|---|---|
| **ORACLE**(완벽 방향 예지, 거래불가 상한) | **−2.32** | −6.32 | −18.32 |
| LONG-the-gap | −15.81 | −19.81 | −31.81 |
| FADE-the-gap(평균회귀) | −16.19 | −20.19 | −32.19 |
| Fri-6h 모멘텀 carry | −17.45 | −21.45 | −33.45 |
- **완벽 오라클조차 −2.32bps**(16bps RT). 실현 전략 전부 −16~−17bps. 슬리피지 10/15/20bps 가산 시 더 악화.
- Fri 모멘텀 carry hit=37.9%(<50, 약한 anti-momentum) → 캐리도 페이드도 엣지 없음.

## 4. 결론: NO_EDGE — 빌드 X (#56 무엣지 KILL 정직판정)
구조적 부등식: **주말 EURUSD 전체 이동(13.7bps) < Capital 주말 딜러 스프레드(16bps RT).** 이건 신호
엔지니어링으로 못 넘는 hard wall — 딜러가 "주말 이동이 작고 작은 게 known"이라서 그만큼 스프레드를 매김.
- maker로 못 우회(CFD 딜러 단일호가, 오더북 없음). taker도 −16bps. 어느 실행레이어도 음수.
- 전 OKX 크립토 주말 리서치(#75-80, gap/drift/persistence/reversion/vol-cycle 전부 REJECT)와 **독립 수렴**:
  주말 윈도우는 변동성만 키우고 방향성 tradeable edge를 만들지 않는다. Capital FX는 더 극단(주말 실시장 부재).
- 검증 엣지 본질(저빈도 추세, [[project_validated_edge_is_slow_trend_not_scalp]]) 재확인: 주말 EURUSD_W에 그런
  추세 없음(drift≈0). 유일 검증 주말 엣지 = OKX weekend_thin_book_flush(#77, 크립토 microstructure flush) — FX엔 그 프리미엄 없음.

## 5. 빌드 설계 (Jin override 시에만 — 현 권고는 빌드 X)
만약 override: `WeekendEURUSDStrategy(venue="capital", asset_class="fx", SUPPORTED_SYMBOLS={"EURUSD_W"})`,
weekend-only 게이트(Sat/Sun UTC), STRATEGY_REGISTRY 등록 + dispatch_eligible, -1.0R rail 불변,
발화경로 검증(weekend-live EURUSD_W fresh bar→generate_raw_signal 도달, #58 INERT 교훈). **단 위 백테스트로
어떤 archetype/파라미터도 net-음수 → 등록해도 손실 churn 양산. #56 mandate상 빌드 비권고.**
