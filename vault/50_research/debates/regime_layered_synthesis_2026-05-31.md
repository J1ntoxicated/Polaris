---
type: debate
status: resolved
date_created: 2026-05-31
tags: [debate, regime, ai-conductor, layered]
related: [[ai_conductor_transition_2026-05-30]], `project_ai_conductor_direction`
---

# /debate — Regime 계층 합성 (L1 macro / L2 asset-class / L3 ticker)

**합주**: Claude 코드 정밀 분석 + codex external review. **결론 = PROCEED_WITH_CHANGES.** Jin "레짐 다이나믹(티커/거래소별)" — 계층 골격이 이미 존재(키 확장 불필요), 개선은 합성 가중·asset-class 차등·confidence·L3 strength.

## 현 골격 (코드 확인)
- **L3** per-ticker price: `compute_real_regime`(_production_indicators.py:322) — drawdown→crisis / efficiency_ratio<→chop / signed return→bull·bear.
- **L2/L1** asset-class+macro: `fuse_evidence`(fuser.py) — prefix 분기(crypto→F&G/funding, 나머지→FRED VIX/HY), conviction floor 1.5.
- **합성**: `compute_and_flip_regime`(_production_layers.py:307-318) — **binary override**(hint면 통째 교체, price=crisis면 price 우선).
- **confirm**: `detect_regime_flip`(regime_flip.py:66) 2-close(crisis 즉시). evidence read-only context.
- **키**: `regime_state(venue, underlying_group_id)` per venue×group. classify_regime(stub) 미사용.

## 판정 (Claude ∩ codex)
| # | 개선 | 판정 | 핵심 |
|---|------|------|------|
| 1 | 키 확장 불필요 | **확정** | PK=(venue,group), 소비자 regime만 읽음. L1/L2 히스토리는 evidence_json `{l1,l2,l3,score,age,source}` 구조화. ⚠"per venue×symbol"→"per venue×group" 정정. |
| 2 | binary→가중 합성 | **AGREE-CHG** | `compose_regime_candidate(price_cand, price_strength, evidence_scores)`를 compute_and_flip_regime **내부에만**. SIGNAL-only(label만), 2-close 그대로. |
| 3 | asset-class 차등 가중 | **AGREE-CHG** | `fuse_evidence` 내부(routing 소유자). crypto=funding/F&G↑, fx/commodity=macro↑, equity=macro+gap. source-type multiplier **0.75-1.25** 제한, 기존 점수 base, routing isolation 테스트 고정. |
| 4 | confidence 동적화 | **AGREE** | `compute_real_regime`→`compute_real_regime_signal`(label,strength,evidence). confidence 합성=compute_and_flip_regime 내 detect 前. classify_regime=포맷/검증 역할. |
| 5 | L3 강화(EMA/ATR) | **AGREE-CHG** | 교체 금지. return/ER/drawdown base 유지 + EMA20/50 cross·24h ATR = **strength 보강값만**. label flip 조건 즉각 확대 금지(test drift). |

## 🔴 BLOCKING (Phase 4 前 필수)
evidence-only `crisis`가 `detect_regime_flip:157-166` 즉시 flip 경로 → "evidence bypass 금지 / 2-close 불변"과 충돌. **`candidate_source` 태그** 추가: price-derived crisis=immediate(유지), evidence-derived crisis=2-close confirm.

## Phased 머지 (무중단, 2-close 불변, 소비자 regime만 읽음)
- **P1** `fuse_evidence`: label+scores/source_weights/asset_class를 evidence_json 기록. 반환 contract 유지.
- **P2** `compute_real_regime_signal`(label,strength,evidence) 추가. 기존 `compute_real_regime` wrapper=label-only 호환.
- **P3** `compute_and_flip_regime` weighted 합성. size/block/exit 미접촉, detect_regime_flip 호출면 유지.
- **P4** evidence-only crisis candidate_source 2-close 처리(price-crisis immediate 유지).
- **P5** confidence 동적화 저장. 소비자 regime만, G3/G7만 confidence/evidence_json.

codex agent=abb4e8e0f59eb627e. 다음=phased 빌드(build TDD→adversarial review→gate).
