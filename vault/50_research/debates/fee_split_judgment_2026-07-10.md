---
type: debate
status: converged
date_created: 2026-07-10
rounds: 2
participants: [claude-opus-conductor, gpt-5.5-codex]
tags: [score_f, pts, fee-split, trading-metric]
---

# 수익/수수료 분리 판정 (Jin 제안) — GPT 2라운드 수렴

**Jin 원제안**: 전략 생존 판정은 수익(그로스)로, 수수료는 별도 축으로 나중에
실행 최적화 단계에서 핸들링. "수수료 들어가서 개뻘짓하는 기분."

**증거**: 24h 그로스 +$192 vs fee $1,414. rsi_bb 7d 그로스 +$763인데 fee로
넷 음수 → 강등 압력. 역대 킬 2건은 전부 그로스 음수 판정(수수료 킬 0).
현행 score = net/max(|fee|,1bp×notional) — 수수료가 분자·분모 이중 지배.

## R1 (GPT verdict)
- **CONCEDE**: 현행 지표는 생존 판정용으로 고장. 합성 taker 수수료가 라벨
  지배, 실 엣지 조기 강등. rsi_bb 강등은 시기상조.
- **BREAKS**: 보고-전용 fee축은 구조적 비수익화 처너 방치 · Kelly 사이즈업은
  정률(bps) 수수료를 희석 못함 · 건수 윈도우는 처닝 우대.
- **COUNTER**: 생존 FSM=gross_bps + fee 축은 **SCALE(tier 증폭) 자격만** 게이트.

## R2 (급소 5 방어 — 수렴)
1. best_case_friction = **구조적 실행가능 플로어**(maker-capable→maker floor,
   taker-only→taker floor+spread p05); 실측 q20은 낮추기만 가능(치킨-에그 해소).
2. LCB = winsorized weighted t-LCB, 신뢰도 60%→80% 램프(N_eff 12→20);
   N<12 전이 금지(베이스라인 거래 지속), 12-19 상향/HOLD만 — 콜드스타트 무기아.
3. 처닝 완화 = 시간가중 윈도우(H=max(6h,12×신호주기), N_eff cap 30) — 빈도
   스로틀 0.
4. **SCALE 게이트 바인딩 시 베이스라인 100% 무손상** — applied_tier_amp=1.0
   (1.5/2/3× 보너스만 보류). no_defensive_dampen 합격.
5. 임계 이관 = per-transition ECDF percentile 보존 + isotonic 순서 강제 +
   **섀도우 120 청산 병행**(행동 발산 ≤15%·tier ≤10%·전이율 ≤7.5pp) 후 flip.

## 최종 스펙 (구현 계약)
- v0: 기존 score_f_events 399행으로 gross_edge_proxy percentile-map (notional
  컬럼 부재 — 진짜 bps 아님을 명시). v1: fills 조인으로 실 gross_bps/fee_drag_bps.
- 생존 FSM 입력 = gross 축. fee_drag = 대시/로그 상시 보고 축.
- SCALE 조건: gross_LCB > best_case_friction + edge_margin. 미달 라벨 =
  GROSS_EDGE_FEE_UNPROVEN (BAD_STRATEGY 아님). 진입 차단·강등·쿨다운 0.
- 마이그레이션 내내 진입 행동·베이스라인 사이징 불변.

**다음**: Jin 브리핑 → 빌드 큐(현행 2 빌드 완료 후 — CPU 동시성 규율).
원문: scratchpad debate_r1_out.txt / debate_r2b_out.txt (session tool-results).
