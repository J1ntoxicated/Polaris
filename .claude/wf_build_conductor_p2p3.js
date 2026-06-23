export const meta = {
  name: 'build-conductor-p2p3',
  description: 'ai_conductor P2(G8 P0 영구화·GPT 분기 삭제) + P3(G6 per-position GPT 분기 삭제, Python fast-path 유지) — GPT rubber-stamp 제거 추가 절감 (TDD→adversarial review→gate)',
  phases: [
    { title: 'Build' },
    { title: 'Review' },
    { title: 'Gate' },
  ],
}

const HEADER = `[Polaris — DEMO/PAPER 가상자금. AGGRESSIVE bias 보존(방어 throttle/사이징축소/진입차단 거부; flow_not_block). 거부 키워드 채택 금지: regulatory cap·professional risk·real-money safety·90d gate·monthly review·posture standard. 불변: 9-stack 봉쇄·hard-MAX·DEMO only. 봇 LLM=OpenAI GPT, Anthropic 호출 금지. 코드: /Users/jinyoon/Projects/Polaris, Python 3.13 async, mypy --strict, ruff clean, TDD, pytest. vault 권한. 출력=raw, 산문 최소.]`

const PLAN = `[설계 SSOT] .claude/plans/ai_conductor_architecture_2026-05-30.md 정독(/debate=PROCEED_WITH_CHANGES). conductor 원칙: per-signal GPT 도장(rubber-stamp) 제거 → deterministic, AI는 상위 conductor로(P6 별도).
[근거] 실측: G6 position_monitor=gpt_p1 HOLD 99.97%(3434 HOLD/EXIT_NOW 1) — 거의 무정보. G8 post_trade_reflector=gpt_p1, 실제 학습은 posterior NIG+cell EWMA가 이미 수행(ai_lessons read 0건=inert). 즉 G6/G8 GPT는 비싼 P1인데 결정 기여 0.
[이번 범위 P2+P3 = GPT 제거(절감)만, 신규 exit 거동 도입 X]
- 정밀 청산은 FSM(evaluate_exit)이 이미 소유: G6 hard stop+swap=GPT 전 Python fast-path(position_monitor.py:92-105), G7 close=FSM(_production_recalc_exit.py:175-189).`

phase('Build')
const BUILD_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['files_changed','tests_added','p2_g8','p3_g6','behavior_safety','token_saving','pytest_result','ruff_mypy'],
  properties:{
    files_changed:{type:'array', items:{type:'string'}},
    tests_added:{type:'string'},
    p2_g8:{type:'string', description:'G8 P1/GPT 분기 삭제 + P0 Python 템플릿 영구화. ai_lessons inert라 거동 0 확인'},
    p3_g6:{type:'string', description:'G6 per-position GPT 분기 삭제 + Python fast-path(hard stop/swap) 유지. GPT HOLD 100%였으니 거동~동일'},
    behavior_safety:{type:'string', description:'FSM/exit 정밀도 미접촉·hard stop/swap fast-path 유지·신규 exit 거동 도입 X·9-stack 무관 확인'},
    token_saving:{type:'string', description:'제거된 P1 GPT 호출(G6 316/G8 17) 추정'},
    pytest_result:{type:'string'},
    ruff_mypy:{type:'string'},
  },
}
const build = await agent(`${HEADER}\n${PLAN}\n
[임무] ai_conductor P2+P3 — G8/G6 per-signal GPT 분기 삭제(절감), 거동 보존. TDD(실패 테스트 먼저→구현→pass).
**P2 G8**(polaris/core/pipeline/agents/post_trade_reflector.py): P1/GPT 분기(약 346-473) 삭제, P0 Python 템플릿 lesson을 영구 경로로. ⚠실제 학습(posterior NIG μ/p_pos + cell EWMA)은 pnl_r/won만 소비하며 G8 GPT 우회 → ai_lessons read 0건(전역 grep 확인) → 거동 변화 0. conductor synthesis(per-N-closes 배치)는 P6라 이번 제외.
**P3 G6**(polaris/core/pipeline/agents/position_monitor.py): per-position GPT 분기(약 279-373) 삭제, Python fast-path(EXIT_NOW hard stop rail + SWAP_STRATEGY) 그대로 유지. ⚠GPT가 HOLD 99.97%였으니 삭제해도 거동~동일(GPT가 ADJUST_EXIT를 사실상 안 냄). ⚠신규 결정트리(winner-widen/momentum-failure EXIT_NOW)는 신규 exit 거동 → ai_conductor needs_debate #5(/debate 항목) → 이번 도입 금지. GPT 제거만.
⚠불변: G7 FSM(evaluate_exit) 미접촉(정밀 청산 소유 유지), G6 hard stop/swap fast-path 유지, 신규 exit/진입 거동 도입 X, SIGNAL/exit 정합, 9-stack/sizing 무관, aggressive(절감이지 throttle 아님).
TDD: 실패 테스트(G8 GPT 미호출·P0 lesson 경로·거동 동일 / G6 GPT 미호출·hard stop fast-path 유지·HOLD 거동 보존) → 구현 → pytest 실제 pass. ruff+mypy --strict clean. **작업 완료 후 반드시 StructuredOutput(schema) 호출.**`,
  {label:'build:conductor-p2p3', phase:'Build', schema:BUILD_SCHEMA, agentType:'general-purpose'})

phase('Review')
const REVIEW_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['verdict','blocking','nits','behavior_ok','fsm_intact','aggressive_ok'],
  properties:{
    verdict:{type:'string', enum:['approve','approve-with-nits','reject']},
    blocking:{type:'array', items:{type:'string'}},
    nits:{type:'array', items:{type:'string'}},
    behavior_ok:{type:'string', description:'G8 P0 영구화 거동 0(ai_lessons inert)·G6 GPT 삭제가 hard stop/swap fast-path 안 깸·신규 exit 거동 미도입 확인'},
    fsm_intact:{type:'string', description:'G7 FSM(evaluate_exit) 정밀 청산 미접촉 확인'},
    aggressive_ok:{type:'string', description:'GPT 제거가 절감이지 throttle/축소 아님. 거부키워드 0.'},
  },
}
const review = await agent(`${HEADER}\n${PLAN}\n
[임무] builder≠reviewer adversarial review(반박 기본). P2/P3 빌드 검증.
[빌드]\n${JSON.stringify(build)}\n
파일 직접 Read: (1) G8 P1/GPT 삭제가 거동 0인가(ai_lessons read 0건 inert, 학습은 posterior/EWMA가 별도 — 전역 grep로 재확인). (2) G6 per-position GPT 삭제가 hard stop rail + SWAP fast-path를 안 깨나(HOLD 99.97% rubber-stamp라 제거해도 거동~동일). (3) ⚠신규 exit 거동(winner-widen/momentum EXIT_NOW)이 도입 안 됐나(/debate 항목이라 금지). (4) ⚠G7 FSM evaluate_exit 미접촉(정밀 청산 소유). (5) aggressive(절감, throttle 아님). (6) 9-stack/sizing 무관. (7) 테스트 충분·ruff/mypy clean.
blocking 보수적(진짜 거동 회귀/FSM 손상/신규 exit 거동/aggressive 위반만). schema 반환.`,
  {label:'review:conductor-p2p3', phase:'Review', schema:REVIEW_SCHEMA, agentType:'general-purpose'})

phase('Gate')
let final = { build, review, fix: null }
if (review.verdict === 'reject') {
  const fix = await agent(`${HEADER}\n${PLAN}\n[임무] review reject. blocking 해결(TDD, 거동 보존, 신규 exit 거동 금지).\n[review]\n${JSON.stringify(review)}\n[원빌드]\n${JSON.stringify(build)}\nblocking 수정→pytest green→ruff/mypy clean. 반드시 StructuredOutput 반환.`,
    {label:'fix:conductor-p2p3', phase:'Gate', schema:BUILD_SCHEMA, agentType:'general-purpose'})
  const rereview = await agent(`${HEADER}\n[임무] 재검증. fix 다시 adversarial review.\n[fix]\n${JSON.stringify(fix)}\nschema 반환.`,
    {label:'rereview:conductor-p2p3', phase:'Gate', schema:REVIEW_SCHEMA, agentType:'general-purpose'})
  final = { build, review, fix, rereview }
}
return final
