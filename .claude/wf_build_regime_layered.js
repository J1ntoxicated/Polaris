export const meta = {
  name: 'build-regime-layered',
  description: 'STEP 5: regime 계층 합성 — fuse_evidence asset-class 가중 + signal 확장 + weighted 합성 + evidence-crisis candidate_source + confidence 동적화 (TDD→adversarial review→gate)',
  phases: [
    { title: 'Build' },
    { title: 'Review' },
    { title: 'Gate' },
  ],
}

const HEADER = `[Polaris — DEMO/PAPER 가상자금. AGGRESSIVE bias 보존(방어 throttle/사이징축소/진입차단 거부; flow_not_block). 거부 키워드 채택 금지: regulatory cap·professional risk·real-money safety·90d gate·monthly review·posture standard. 불변: 9-stack 봉쇄·hard-MAX·DEMO only. 봇 LLM=OpenAI GPT, Anthropic 호출 금지. 코드: /Users/jinyoon/Projects/Polaris, Python 3.13 async, mypy --strict, ruff clean, TDD(실패→구현→pass), pytest. vault 권한. 출력=raw, 산문 최소.]`

const PLAN = `[설계 SSOT] .claude/plans/stream_regime_integration_2026-05-30.md STEP 5 + vault/50_research/debates/regime_layered_synthesis_2026-05-31.md 정독(codex /debate=PROCEED_WITH_CHANGES).
[현 골격] L3=compute_real_regime(_production_indicators.py:322) · L2/L1=fuse_evidence(fuser.py, prefix 분기+conviction floor 1.5) · 합성=compute_and_flip_regime(_production_layers.py:307-318, binary override) · confirm=detect_regime_flip(regime_flip.py:66, 2-close, crisis 즉시) · 키=regime_state(venue,underlying_group_id).
[불변] SIGNAL-only(regime label만, size/block/exit/halt 절대 X) · 2-close confirm gate(evidence bypass 금지) · 소비자는 regime만 읽음(G3/G7만 confidence/evidence_json) · per venue×group 키 그대로(확장 X) · aggressive(throttle/축소 X) · 무중단(additive).`

phase('Build')
const BUILD_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['files_changed','tests_added','phases_done','blocking_resolved','behavior_safety','pytest_result','ruff_mypy'],
  properties:{
    files_changed:{type:'array', items:{type:'string'}},
    tests_added:{type:'string', description:'추가 테스트 수 + 검증 항목'},
    phases_done:{type:'string', description:'P1-P5 각 단계 구현 내용'},
    blocking_resolved:{type:'string', description:'evidence-only crisis candidate_source 태그 — price-crisis immediate vs evidence-crisis 2-close 처리'},
    behavior_safety:{type:'string', description:'SIGNAL-only·2-close 불변·소비자 regime만·키 미확장 보존 확인'},
    pytest_result:{type:'string', description:'pytest 실제 실행 결과'},
    ruff_mypy:{type:'string', description:'ruff + mypy --strict 결과'},
  },
}
const build = await agent(`${HEADER}\n${PLAN}\n
[임무] STEP 5 regime 계층 합성 — codex phased P1-P5 순차 구현 (TDD 엄수: 각 단계 실패 테스트 먼저→구현→pass).
**P1 fuse_evidence asset-class 차등 가중**(fuser.py): prefix별 source-type multiplier(crypto: funding/F&G↑, forex/commodity: macro↑, equity: macro+gap) — **0.75-1.25 범위 제한**, 기존 점수 base. label+scores/source_weights/asset_class를 evidence(반환 dict)에 기록. ⚠기존 반환 contract(regime_hint,confidence,evidence) 유지, routing isolation(crypto vs macro 분기) 테스트로 고정.
**P2 compute_real_regime_signal**(_production_indicators.py): (label, strength, evidence) 반환 함수 추가. strength = return 크기·efficiency·(선택)EMA20/50 cross·24h ATR ratio 보강. ⚠기존 compute_real_regime은 label-only wrapper로 호환 유지. ⚠label flip 조건 즉각 확대 금지(test drift) — strength는 보강값일 뿐 label 산출 동일.
**P3 weighted 합성**(_production_layers.py compute_and_flip_regime): binary override(if hint: candidate=hint) → compose_regime_candidate(price_candidate, price_strength, evidence_scores) **내부 함수**. price base + evidence를 conviction 비례 tilt(강하면 영향↑, 약하면 price 유지). ⚠size/block/exit 미접촉, detect_regime_flip 호출면 유지, SIGNAL-only.
**P4 🔴BLOCKING evidence-crisis candidate_source**: evidence-only crisis가 detect_regime_flip 즉시-flip(regime_flip.py:157-166)을 타면 2-close bypass. candidate_source 태그(price/evidence) 추가 → **price-derived crisis=immediate 유지, evidence-derived crisis=2-close confirm**. detect_regime_flip crisis 분기에 source 구분.
**P5 confidence 동적화**: regime_state.confidence를 0.5 고정 탈피 — compute_and_flip_regime 내 합성 직후(detect 호출 前) L3 strength + L2/L1 일치도로 산출. 소비자는 regime만, G3/G7만 confidence/evidence_json 사용.
각 P 후 pytest 실제 실행. 전체 ruff + mypy --strict clean. **반드시 작업 완료 후 StructuredOutput(schema) 호출로 반환** — pytest/ruff/mypy는 실제 실행 결과.`,
  {label:'build:regime-layered', phase:'Build', schema:BUILD_SCHEMA, agentType:'general-purpose'})

phase('Review')
const REVIEW_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['verdict','blocking','nits','signal_only_ok','confirm_gate_ok','aggressive_ok'],
  properties:{
    verdict:{type:'string', enum:['approve','approve-with-nits','reject']},
    blocking:{type:'array', items:{type:'string'}, description:'머지 블로커. 없으면 빈 배열'},
    nits:{type:'array', items:{type:'string'}},
    signal_only_ok:{type:'string', description:'regime이 size/block/exit/halt 절대 안 건드림 확인'},
    confirm_gate_ok:{type:'string', description:'2-close confirm 불변 + evidence-crisis candidate_source 2-close 처리 정합 확인'},
    aggressive_ok:{type:'string', description:'asset-class 가중이 은밀한 throttle/축소 아님(0.75-1.25 제한, label만). 거부키워드 0.'},
  },
}
const review = await agent(`${HEADER}\n${PLAN}\n
[임무] builder≠reviewer adversarial review(반박 기본). STEP 5 regime 계층 빌드 검증.
[빌드]\n${JSON.stringify(build)}\n
파일 직접 Read 검증: (1) SIGNAL-only — regime이 size/block/exit/halt 절대 안 건드림(여전히 candidate label만, fuse는 SUGGESTION). (2) 2-close confirm 불변 + 🔴evidence-crisis가 candidate_source로 2-close 타고 price-crisis만 immediate(BLOCKING 해결 확인). (3) asset-class 가중 0.75-1.25 제한·기존 점수 base(은밀한 throttle/축소 도입 X, aggressive 보존). (4) compute_real_regime wrapper 호환(label flip 조건 미변경, strength는 보강만). (5) confidence 동적화가 소비자(regime만 읽는) 안 깸. (6) per venue×group 키 미확장. (7) 무중단(additive)·테스트 충분·ruff/mypy clean.
blocking 보수적으로(진짜 SIGNAL-only 위반/confirm bypass/aggressive 위반/회귀만). schema 반환.`,
  {label:'review:regime-layered', phase:'Review', schema:REVIEW_SCHEMA, agentType:'general-purpose'})

phase('Gate')
let final = { build, review, fix: null }
if (review.verdict === 'reject') {
  const fix = await agent(`${HEADER}\n${PLAN}\n[임무] review가 reject. blocking 해결(TDD 유지, 불변 보존).\n[review]\n${JSON.stringify(review)}\n[원빌드]\n${JSON.stringify(build)}\nblocking 각각 수정 → pytest green → ruff/mypy clean. 반드시 StructuredOutput 반환. schema.`,
    {label:'fix:regime-layered', phase:'Gate', schema:BUILD_SCHEMA, agentType:'general-purpose'})
  const rereview = await agent(`${HEADER}\n[임무] 재검증. fix를 다시 adversarial review.\n[fix]\n${JSON.stringify(fix)}\nschema 반환.`,
    {label:'rereview:regime-layered', phase:'Gate', schema:REVIEW_SCHEMA, agentType:'general-purpose'})
  final = { build, review, fix, rereview }
}
return final
