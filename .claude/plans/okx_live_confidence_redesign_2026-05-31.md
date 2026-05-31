# OKX Live-Confidence Redesign — plan SSOT (2026-05-31)

**North star shift (Jin 2026-05-31)**: 목표 = **실거래(real OKX)를 열 컨피던스**. 트리거 = **real-fee-net equity 곡선 우상향**(Jin 선택). "컨피던스"=edge 입증(방어/보수 아님 — aggressive·"오직 수익" 유지, 거부키워드 0). 데모 0.7% fee는 7배 페널티(아래)일 뿐, 봇 판단·평가는 **real OKX 경제학** 기준.

## 확정 사실 (forensics + 권위 조회)
- 실제 OKX demo 계정 totalEq=$73,742(시작 $79k) → **−$5.3k ≈ 거의 100% 수수료**. 실현(가격) −$336(132W/247L 본전), 오픈 +$545.
- **fee = 0.7% (taker=maker), Lv1** — `/api/v5/account/trade-fee` + 실 체결로 확정. **우리 버그 아님, OKX demo 실부과.** maker=taker라 데모선 maker 무의미.
- 실 OKX SPOT Lv1 = **taker 0.10% / maker 0.08%** (공식). 데모는 **7배 페널티**.
- over-trading 정체 = **동시 스태킹 + 같은 바 재발화**: tsmom 88% fills, BTC 동시 12포지션. 원인 = `reentry.py` 쿨다운의 **strong-signal 면제**(strength≥0.85)인데 tsmom strength=0.5+5×momentum이 momentum 7%면 1.0 포화 → 매틱 자가면제. strength=raw 모멘텀(conviction 아님), chop서 튀어 면제가 정반대.
- **edge는 존재**(cell matrix): tsmom bull +0.7~1.1R(ASTER+0.76/HYPE+1.06/BTC+0.69), chop/crisis −0.5~−0.9R. 봇이 전 레짐 무차별 발화+churn으로 edge 희석.
- real-fee(0.10%) 환산 net ≈ −$981 (회전 마찰 위주). churn 줄이고 +EV만 거래하면 우상향 가능.

## 재설계 (4 컴포넌트, 각 spec→build TDD→Claude 적대리뷰→거동게이트→커밋, 매 단계 real-fee-net 곡선으로 검증)

### A. real-fee 회계 + 듀얼 equity 곡선 + 컨피던스 대시보드 [측정 기반, FIRST, 저위험]
- **real-fee cost model**: 상수 `OKX_REAL_TAKER_BPS=10, OKX_REAL_MAKER_BPS=8, OKX_DEMO_FEE_BPS=70`. `real_fee_usd(notional, is_maker)`.
- **듀얼 곡선**(snapshot): ⓐ real-fee-net = start + Σ(close gross pnl_usd) − Σ(notional×real_fee) · ⓑ demo-actual = 기존(stored fee_usd 0.7%, 실 계정과 일치). reconcile 체크: ⓑ≈−$4.8k, ⓐ≈−$1k.
- **컨피던스 지표**: real-fee-net 곡선 + 전략×레짐 net R + posterior LCB + 승률/profit-factor/turnover/fee-drag(real vs demo). shadow_acceptance.py real-fee로 갱신.
- 거동게이트: 트레이딩 거동 0(측정/표시만). dashboard server.py 무변경 지향.

### B. anti-churn + hold-to-thesis [거동 변경, 곡선 임팩트 최대, 거의 공짜]
- reentry 면제 strength→**novelty**(새 strategy-timeframe 바 `created_at_bar>last_entry_bar` OR side-flip). 쿨다운 창=timeframe 1바(flat 300s 아님).
- **동시 중복 차단**: 같은 (venue,symbol,strategy,side) 보유 중이면 클론 금지(12-concurrent 스태킹 차단).
- exit 호라이즌 ∝ timeframe(loser-timeout 15분이 1H 논지 조기청산하는 것 수정).
- 거동게이트: churn률 급감 + 새 바 진입은 보존(flow_not_block) + shadow_conn=None byte-identical 불필요(이건 실거동). 기존 인프라 재사용(`reentry.py`, `_production_tick.py:392`, `rotation_vacated_cooldowns` 패턴).

### C. edge-first 진입 [거동 변경] — regime-conditioned(+EV 레짐만; tsmom trend) + cost-aware(기대이동>real 왕복비용; net_edge 아님 — cell hit-rate/MFE 기반).
### D. conviction 집중 사이징 [거동 변경] — +EV cell 증폭, −EV 회피 (cell quartile ×1.5 기존 + posterior).

**순서**: A→B→C→D. 매 단계 real-fee-net 곡선 개선 확인. 구현=Claude만(GPT 금지 [[feedback_no_dev_gpt]]). 관련 [[conductor_g3g4_cutover_2026-05-31]] · [[project_operating_thesis_surgical_strike]].
