export const meta = {
  name: 'build-g3g4-shadow',
  description: 'ai_conductor P4/P5 SHADOW: G4/G3 deterministic 규칙을 GPT와 병행 계산 + 불일치 log (실결정 GPT 유지=거동0). cold=pass-through·net_edge gating 금지·aggressive (codex blocking) (TDD→adversarial review→gate)',
  phases: [
    { title: 'Build' },
    { title: 'Review' },
    { title: 'Gate' },
  ],
}

const HEADER = `[Polaris — DEMO/PAPER 가상자금. AGGRESSIVE bias 보존(방어 throttle/사이징축소/진입차단 거부; flow_not_block; 모호하면 통과). 거부 키워드 채택 금지: regulatory cap·professional risk·real-money safety·90d gate·monthly review·posture standard·fractional Kelly·표본 부족. 불변: 9-stack 봉쇄·hard-MAX·DEMO only. 봇 LLM=OpenAI GPT, Anthropic 호출 금지. 코드: /Users/jinyoon/Projects/Polaris, Python 3.13 async, mypy --strict, ruff clean, TDD, pytest. vault 권한. 출력=raw, 산문 최소.]`

const PLAN = `[설계 SSOT] .claude/plans/ai_conductor_architecture_2026-05-30.md + vault/50_research/debates/ai_conductor_transition_2026-05-30.md 정독(codex /debate=PROCEED_WITH_CHANGES, blocking 3).
[이번 = P4/P5 SHADOW 모드만 — 컷오버 아님]: G4(pre_entry_watcher.py)·G3(signal_validator.py)에 deterministic technical 규칙을 **추가 계산**하되 **실 pipeline 결정은 여전히 GPT**(거동 0). technical vs GPT 결정 + 불일치를 log → acceptance gate 데이터 수집(다음 세션 컷오버 판단용). 정밀도 핵심이라 shadow 선행(거동 위험 0).
[codex blocking 그대로]:
- 🔴 cold-start KILL 금지: cell n_eff<5(cold)는 KILL 절대 X(pass-through, "모호하면 통과"). warm(n_eff>=5)만 narrow KILL.
- 🔴 net_edge_r gating 금지: net_edge.py="placeholder, NOT alpha". G3/G4 KILL/MODIFY 근거로 쓰지 말 것. 대신 cell hit-rate/recent same-symbol 연속손실/spread-vs-baseline/listing-age.
- 🔴 G4 realized-vol KILL 금지("expanding=기회" 충돌). G4 spread/drift=MODIFY/flag default(KILL 아님), stale/crossed book만 KILL.
- P4(G4)가 P5(G3) 컷오버의 선행(이번은 둘 다 shadow라 순서 무관, 컷오버 시 enforce).`

phase('Build')
const BUILD_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['files_changed','tests_added','g4_shadow','g3_shadow','shadow_log','behavior_zero','pytest_result','ruff_mypy'],
  properties:{
    files_changed:{type:'array', items:{type:'string'}},
    tests_added:{type:'string'},
    g4_shadow:{type:'string', description:'G4 deterministic 규칙(stale/crossed KILL·spread/drift flag, realized-vol KILL 금지) 병행 계산 + 설계'},
    g3_shadow:{type:'string', description:'G3 deterministic 규칙(cold=pass-through·warm narrow KILL·net_edge 미사용·cell-독립 booster) 병행 계산'},
    shadow_log:{type:'string', description:'technical vs GPT decision + 불일치 기록 위치/스키마(acceptance gate 분석 가능하게)'},
    behavior_zero:{type:'string', description:'실 pipeline 결정=GPT 유지(거동 0) 확인 — technical은 계산+log만, 결정 미반영'},
    pytest_result:{type:'string'},
    ruff_mypy:{type:'string'},
  },
}
const build = await agent(`${HEADER}\n${PLAN}\n
[임무] P4/P5 SHADOW 모드 (TDD: 실패 테스트 먼저→구현→pass).
**P4 G4 shadow**(polaris/core/pipeline/agents/pre_entry_watcher.py): is_fast_path_eligible 패턴 확장한 deterministic technical 규칙 함수 추가 — PROCEED default, KILL=stale/crossed book만, spread/drift는 flag(KILL 아님), realized-vol KILL 금지. 이 technical 결정을 GPT 결정과 **병행 계산**, 둘 다 shadow log. ⚠실 next_gate/decision은 GPT 그대로(거동 0).
**P5 G3 shadow**(polaris/core/pipeline/agents/signal_validator.py): deterministic technical 규칙 — PASS default, **cold cell(n_eff<5)=절대 KILL 안 함(pass-through)**, warm(n_eff>=5) AND quartile=='bottom' AND avg_pnl_r<0 일 때만 narrow KILL, MODIFY=warm mid/cold quartile 시 보수적 scalar(net_edge 미사용). cell-독립 booster(recent same-symbol 연속손실·spread/baseline·listing-age)는 warm KILL 보강. GPT와 병행 계산, shadow log. ⚠실 decision/strength_scalar는 GPT 그대로(거동 0).
**shadow log 인프라**: technical_decision·gpt_decision·불일치 flag를 gate_events(payload_json 또는 신규 컬럼) 또는 별도 shadow 테이블에 기록 — KILL률/PASS률/일치율을 레짐·cell warm여부별로 분석 가능하게(다음 세션 acceptance gate).
⚠불변: 실 pipeline 결정=GPT(거동 0, 기존 테스트 그대로 pass), cold=pass-through, net_edge gating 금지, realized-vol KILL 금지, aggressive(deterministic KILL은 GPT보다 적거나 같게=flow 보존), 9-stack 무관.
TDD: 실패 테스트(technical 규칙 정확성·cold pass-through·net_edge 미사용·shadow log 기록·실결정 GPT 불변) → 구현 → pytest 실제 pass. ruff+mypy --strict clean. **작업 완료 후 반드시 StructuredOutput(schema) 호출.**`,
  {label:'build:g3g4-shadow', phase:'Build', schema:BUILD_SCHEMA, agentType:'general-purpose'})

phase('Review')
const REVIEW_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['verdict','blocking','nits','behavior_zero_ok','cold_passthrough_ok','no_netedge','aggressive_ok'],
  properties:{
    verdict:{type:'string', enum:['approve','approve-with-nits','reject']},
    blocking:{type:'array', items:{type:'string'}},
    nits:{type:'array', items:{type:'string'}},
    behavior_zero_ok:{type:'string', description:'실 pipeline 결정=GPT 유지(거동 0) — technical은 shadow log만, 결정 미반영 확인'},
    cold_passthrough_ok:{type:'string', description:'cold cell(n_eff<5) KILL 절대 안 함 확인'},
    no_netedge:{type:'string', description:'net_edge_r를 KILL/MODIFY 근거로 안 씀 확인'},
    aggressive_ok:{type:'string', description:'deterministic KILL ≤ GPT(flow 보존)·realized-vol KILL 없음·거부키워드 0'},
  },
}
const review = await agent(`${HEADER}\n${PLAN}\n
[임무] builder≠reviewer adversarial review(반박 기본). P4/P5 shadow 빌드 검증.
[빌드]\n${JSON.stringify(build)}\n
파일 직접 Read: (1) ⚠실 pipeline 결정=GPT 유지(거동 0) — technical 결정이 next_gate/decision/strength_scalar에 안 새나(shadow log만). (2) cold cell(n_eff<5) KILL 절대 X(pass-through). (3) net_edge_r를 KILL/MODIFY 근거로 안 씀. (4) realized-vol KILL 없음, G4 spread/drift=flag(KILL 아님). (5) aggressive — deterministic KILL이 GPT보다 적거나 같음(flow 보존, throttle 아님). (6) shadow log가 acceptance gate 분석 가능(레짐·warm별 KILL률). (7) 9-stack 무관·테스트 충분·ruff/mypy clean.
blocking 보수적(진짜 거동 누출/cold KILL/net_edge gating/aggressive 위반만). schema 반환.`,
  {label:'review:g3g4-shadow', phase:'Review', schema:REVIEW_SCHEMA, agentType:'general-purpose'})

phase('Gate')
let final = { build, review, fix: null }
if (review.verdict === 'reject') {
  const fix = await agent(`${HEADER}\n${PLAN}\n[임무] review reject. blocking 해결(TDD, 거동 0, cold=pass-through, net_edge 금지).\n[review]\n${JSON.stringify(review)}\n[원빌드]\n${JSON.stringify(build)}\nblocking 수정→pytest green→ruff/mypy clean. 반드시 StructuredOutput 반환.`,
    {label:'fix:g3g4-shadow', phase:'Gate', schema:BUILD_SCHEMA, agentType:'general-purpose'})
  const rereview = await agent(`${HEADER}\n[임무] 재검증. fix 다시 adversarial review.\n[fix]\n${JSON.stringify(fix)}\nschema 반환.`,
    {label:'rereview:g3g4-shadow', phase:'Gate', schema:REVIEW_SCHEMA, agentType:'general-purpose'})
  final = { build, review, fix, rereview }
}
return final
