export const meta = {
  name: 'build-g1-ranker',
  description: 'P1: G1 universe_scanner GPT 분기 제거 → deterministic scored ranker (TDD → adversarial review → 거동 게이트)',
  phases: [
    { title: 'Build' },
    { title: 'Review' },
    { title: 'Gate' },
  ],
}

const HEADER = `[Polaris — DEMO/PAPER 가상자금. AGGRESSIVE bias 보존(방어 throttle/사이징축소/진입차단/focus 축소 거부; flow_not_block). 거부 키워드 채택 금지: regulatory cap·professional risk·real-money safety·90d gate·monthly review·posture standard. 불변: 9-stack collapse 봉쇄·hard-MAX·DEMO only. 봇 LLM=OpenAI GPT, Anthropic 호출 금지. 코드: /Users/jinyoon/Projects/Polaris, Python 3.13 async, mypy --strict, ruff clean, TDD(실패→구현→pass), pytest. vault 권한 있음. 출력=raw 데이터, 산문 최소.]`

const PLAN = `[설계 SSOT] .claude/plans/ai_conductor_architecture_2026-05-30.md (G1 줄 + /debate 교정 정독). /debate 결론=PROCEED_WITH_CHANGES.`

phase('Build')
const BUILD_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['files_changed','tests_added','key_decisions','behavior_preserved','pytest_result','ruff_mypy'],
  properties:{
    files_changed:{type:'array', items:{type:'string'}},
    tests_added:{type:'string', description:'추가 테스트 수 + 무엇을 검증'},
    key_decisions:{type:'string', description:'scored ranker 설계 결정(weights 기본값·RECOMPUTE 트리거·GPT 제거 방식)'},
    behavior_preserved:{type:'string', description:'거동 보존 확인: 항상 PASS·12-48 clamp·augment-from-top-vol·fallback 동일'},
    pytest_result:{type:'string', description:'pytest 결과(green 수/실패). 반드시 실제 실행.'},
    ruff_mypy:{type:'string', description:'ruff + mypy --strict 결과'},
  },
}
const build = await agent(`${HEADER}\n${PLAN}\n
[임무] P1: G1 universe_scanner를 deterministic scored ranker로 컷오버. TDD 엄수(실패 테스트 먼저 → 구현 → pass).
1. 정독: polaris/core/pipeline/agents/universe_scanner.py + polaris/core/pipeline/agents/g1_focus_gate.py (현재 deterministic_top_n, GPT 분기, clamp/augment).
2. 구현:
   - deterministic_top_n을 scored ranker로 일반화: score = w1*log(vol_24h_usd) + w2*cell_matrix_quartile + w3*realized_vol(or ATR proxy from market_view) + w4*per-symbol recent hit-rate(gate_events 있으면, 없으면 0). top-N clamp 12-48.
   - GPT 분기(call_gpt) 제거. should_call_gpt_g1 fingerprint+cooldown 로직은 "RECOMPUTE 트리거"로 재사용(listing change OR top-K turnover>25% OR cooldown elapse 시 재계산, 아니면 cached focus 재사용).
   - weights w1-4 = env-override(예: POLARIS_G1_W_VOL 등), 보수적 기본(vol 지배적: w1 크게). /debate 캘리브 대상 주석.
   - ⚠ 거동 보존 필수: 항상 PASS(selector, KILL/MODIFY 없음)·최소 12·augment-from-top-vol·clamp 12-48. fallback(no-client/error) 경로는 이미 deterministic이므로 그것과 동일 출력 보장.
   - ⚠ aggressive: focus 축소/throttle 아님(커버리지 유지, FOCUS_MAX=48 wide). 9-stack 무관(사이징 미변경).
3. TDD: 실패 테스트 먼저(scored ranker 정확성·clamp 12-48·PASS 보존·empty cell_summary 프로덕션 경로·fallback 동일). 구현. pytest 실제 실행해 pass 확인. ruff + mypy --strict clean.
schema 반환. pytest/ruff/mypy는 반드시 실제 실행 결과.`,
  {label:'build:g1-ranker', phase:'Build', schema:BUILD_SCHEMA, agentType:'general-purpose'})

phase('Review')
const REVIEW_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['verdict','blocking','nits','behavior_ok','aggressive_ok'],
  properties:{
    verdict:{type:'string', enum:['approve','approve-with-nits','reject']},
    blocking:{type:'array', items:{type:'string'}, description:'머지 블로커(거동 변화/aggressive 위반/테스트 공백/GPT 잔존). 없으면 빈 배열'},
    nits:{type:'array', items:{type:'string'}},
    behavior_ok:{type:'string', description:'PASS 100%·clamp·fallback 동일 검증 결과'},
    aggressive_ok:{type:'string', description:'focus 커버리지 축소/throttle 도입 없음 확인'},
  },
}
const review = await agent(`${HEADER}\n${PLAN}\n
[임무] builder≠reviewer adversarial review. 아래 G1 ranker 빌드를 적대적으로 검증(틀렸다 가정하고 깨려 시도).
[빌드 결과]\n${JSON.stringify(build)}\n
파일을 직접 Read 해서 검증: (1) 거동 보존 — 항상 PASS·12-48 clamp·augment·fallback 출력 동일. (2) scored ranker 정확성(weights·정규화·tie-break). (3) GPT 분기 완전 제거(call_gpt 잔존 없음, RECOMPUTE 트리거만). (4) weights 기본값이 커버리지를 좁히지 않는가(aggressive — focus 축소/throttle 금지). (5) 테스트가 거동 보존을 실제로 커버하나. (6) ruff/mypy clean. 9-stack은 G1 무관(사이징 미변경) 확인.
blocking은 보수적으로(진짜 거동 변화/aggressive 위반/GPT 잔존/테스트 공백만). schema 반환.`,
  {label:'review:g1-ranker', phase:'Review', schema:REVIEW_SCHEMA, agentType:'general-purpose'})

phase('Gate')
let final = { build, review, fix: null }
if (review.verdict === 'reject') {
  const fix = await agent(`${HEADER}\n${PLAN}\n
[임무] 위 adversarial review가 reject. blocking을 해결하라(TDD 유지, 거동 보존).
[review]\n${JSON.stringify(review)}\n[원 빌드]\n${JSON.stringify(build)}\n
blocking 각각 수정 → pytest 재실행 green → ruff/mypy clean. schema 반환.`,
    {label:'fix:g1-ranker', phase:'Gate', schema:BUILD_SCHEMA, agentType:'general-purpose'})
  const rereview = await agent(`${HEADER}\n[임무] 재검증. fix 결과를 다시 adversarial review.\n[fix]\n${JSON.stringify(fix)}\nschema 반환.`,
    {label:'rereview:g1-ranker', phase:'Gate', schema:REVIEW_SCHEMA, agentType:'general-purpose'})
  final = { build, review, fix, rereview }
}
return final
