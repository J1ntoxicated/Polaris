# Stream/asset_class/regime 통합 교정 (Jin "다 연결", Workflow w184teviz)

**Jin**: G1 즉시절감 + regime 계층 + stream 교정을 한꺼번에(다 연결). asset_class 하나가 universe→stream→regime L2→cell→leverage→G1 전체 관통.

## 근원 (오태깅 아님 — selection 편향 2단)
1. **Capital이 crypto 알트 CFD 의도 fetch** — `_capital.py:33-42 CAPITAL_P0_CATEGORY_TOKENS`에 "crypto"+"currenc" → crypto_currencies_group walk → 290 crypto-CFD 적재. 태깅(`_classify_capital_node`)은 정확.
2. **24/7 crypto가 세션-제한 FX/지수/금 압도** — `_ranking.py:35-43 _is_valid_candidate` state!='live' hard-drop → 세션닫힘 FX(live1/closed114)·금(0/67)·지수(0/20) 전멸, crypto(290 live) 독점.
3. **단일 SSOT 비강제**: `config.py:207 B_capital_cfd.asset_classes={forex,index,commodity}`(crypto 제외 의도)를 universe 선택이 강제 안 함(`resolve_stream().asset_classes` 대조 0건).
- **하류 오염(다 연결 실증)**: crypto 오선택 → regime_state crypto그룹 + leverage 2x(FX 30x인데) + alt-data funding 라우팅 전부 crypto-편향.

## 통합 빌드 순서 (의존 검증됨)
- **STEP 0 (선행)** — 자산군 분배. **✅ Jin 결정 2026-05-30 = (a) 완전 배제**: A=OKX crypto 전담(롱,24/7), B=Capital FX/지수/금 전담. crypto 토큰 제거 + `resolve_stream` 화이트리스트 enforce. Capital은 세션 열릴 때만(오프세션=OKX가 24/7 담당). cell asset_class 차원·regime 계층은 STEP 5에서 /debate.
- **STEP 1 (즉시·독립, P0 절감)** — G1 GPT 제거(2850콜→0) deterministic vol-ranker. asset_class 무관(vol 지배). asset-class 쿼터는 STEP 6로 분리.
- **STEP 2 (근원, P0)** — Capital universe SCOPE 교정. 옵션A `_capital.py:33-42` "crypto" 제거+"currenc" FX-only / 옵션B persist 직전 `resolve_stream("capital").asset_classes` 화이트리스트.
- **STEP 3 (P0, STEP2 직후)** — 세션 비대칭 해소. CFD 'closed' = hard-drop 대신 **'세션 대기(watch)'** 라우팅(flow_not_block), 또는 활성셋 자산군별 분리(crypto가 세션슬롯 잠식 방지). `_ranking.py:41`+`_capital.py:166-168`.
- **STEP 4 (위생, P0 동반)** — coherence guard: 런타임 `asset_class ∈ resolve_stream("capital").asset_classes` 검증. `config.py:207` doc-only→enforced. regression catch.
- **STEP 5 (regime 계층 L1/L2/L3, P1)** — ✅codex /debate=PROCEED_WITH_CHANGES([[regime_layered_synthesis_2026-05-31]]). **골격 이미 존재**: L3=`compute_real_regime`(_production_indicators.py:322, drawdown/ER/return) · L2/L1=`fuse_evidence`(prefix 분기+conviction floor 1.5) · 합성=`compute_and_flip_regime`(_production_layers.py:307-318, **binary override**) · 키=`regime_state(venue,underlying_group_id)`=per venue×**group**(symbol 아님). **키 확장/별도 테이블 불필요**(소비자는 regime만 읽음, L1/L2는 evidence_json 구조화). 개선 4 + 🔴BLOCKING:
  - ① 합성 binary→**가중**: `compose_regime_candidate(price_candidate, price_strength, evidence_scores)`를 compute_and_flip_regime 내부에만. price base + evidence conviction 비례 tilt. SIGNAL-only(label만, size/block/exit X), 2-close confirm 그대로.
  - ② **asset-class 차등 가중**: `fuse_evidence` 내부(routing 소유자). crypto=funding/F&G↑, fx/commodity=macro↑, equity=macro+gap. source-type multiplier 0.75-1.25 제한, 기존 점수 base. routing isolation 테스트로 고정.
  - ③ **confidence 동적화**: `compute_real_regime`→`compute_real_regime_signal`(label,strength,evidence) 확장(기존 wrapper label-only 호환). confidence 합성=compute_and_flip_regime 내, detect_regime_flip 호출 前. classify_regime은 포맷/검증 역할 유지.
  - ④ **L3 강화**(선택): EMA20/50 cross+24h ATR ratio = **strength 보강값만**, label flip 조건 즉각 확대 금지(test drift).
  - 🔴**BLOCKING(Phase 4 前 필수)**: evidence-only crisis가 detect_regime_flip:157-166 즉시 flip → 2-close bypass 충돌. **candidate_source 태그**로 price-crisis(immediate 유지) vs evidence-crisis(2-close) 구분.
  - **phased**: P1 fuse_evidence(label+scores/weights/asset_class→evidence_json, contract 유지) → P2 compute_real_regime_signal 추가(wrapper 호환) → P3 weighted 합성(size/block/exit 미접촉) → P4 evidence-crisis candidate_source 2-close → P5 confidence 동적화 저장(소비자 regime만, G3/G7만 confidence/evidence).
- **STEP 6 (G1 asset-class-aware, P1, STEP5 후)** — Jin "레짐 셀렉션 다이나믹·자산군별" 충족. `watchlist.py:184` focus selection에 자산군별 top-N 쿼터. + Capital `vol_24h_usd=0.0`(`_capital.py:178`) G1 배제 별개 이슈 해결.

## needs_debate (STEP 0 — 아키텍처)
자산군 분배(a/b/c) · cell_matrix asset_class 5번째 차원 · G1 aware 방식(scanner vs 상류 쿼터) · regime 계층화 방식(키확장 vs 별도 테이블) · CFD 'closed' 세션 정책(거동 변경).

## risks
무중단(STEP2 후 다음 갱신 사이클에 Capital universe 290→소수, 세션닫힘이면 0 — STEP3가 완충) · 거동보존 X(의도적 변경, crypto:LIT cell/regime row 고아화→TTL/마이그레이션) · cluster cap 사각(crypto-CFD None→무제한, STEP2가 해소) · regime 키확장 부분머지 시 lookup miss(atomic 필요) · 부차 오태깅(CVX/XOM→commodity, P0 아님 추적).
