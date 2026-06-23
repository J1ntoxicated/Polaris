export const meta = {
  name: 'build-capital-scope',
  description: 'STEP 2/3/4: Capital crypto 배제(화이트리스트 enforce) + 세션 비대칭 해소(closed→session-wait) + coherence guard (TDD→adversarial review→gate)',
  phases: [
    { title: 'Build' },
    { title: 'Review' },
    { title: 'Gate' },
  ],
}

const HEADER = `[Polaris — DEMO/PAPER 가상자금. AGGRESSIVE bias 보존(방어 throttle/사이징축소/진입차단 거부; flow_not_block). 거부 키워드 채택 금지: regulatory cap·professional risk·real-money safety·90d gate·monthly review·posture standard. 불변: 9-stack 봉쇄·hard-MAX·DEMO only. 봇 LLM=OpenAI GPT, Anthropic 호출 금지. 코드: /Users/jinyoon/Projects/Polaris, Python 3.13 async, mypy --strict, ruff clean, TDD(실패→구현→pass), pytest. vault 권한. 출력=raw, 산문 최소.]`

const PLAN = `[설계 SSOT] .claude/plans/stream_regime_integration_2026-05-30.md 정독 (근원·STEP 2/3/4·risks). Jin 결정 STEP0=(a) 완전 배제: A=OKX crypto 전담, B=Capital FX/지수/금 전담. ⚠이건 방어 throttle 아니라 "의도된 자산군 라우팅 교정"(crypto edge는 OKX track A로). 거부키워드 sweep clean.`

const CODEREF = `[code refs]
- _capital.py:33-42 CAPITAL_P0_CATEGORY_TOKENS ("crypto"+"currenc" → crypto_currencies_group 290 CFD fetch)
- _capital.py:166-202 (_capital_market_row_to_instrument state 매핑 + _classify_capital_node 태깅[정확])
- _ranking.py:35-43 _is_valid_candidate (state!='live' hard-drop = 세션닫힘 FX/지수/금 비대칭 배제)
- discovery.py:192-231 apply_active_filters / persist_universe:298-363
- config.py:203-231 B_capital_cfd.asset_classes={forex,index,commodity} (의도, 미강제) + resolve_stream
- canonical.py:25-67 compute_underlying_group_id (forex:/index:/commodity: 분기 — 태깅 정확, 손대지 말 것)`

phase('Build')
const BUILD_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['files_changed','tests_added','key_decisions','behavior_change','migration','pytest_result','ruff_mypy'],
  properties:{
    files_changed:{type:'array', items:{type:'string'}},
    tests_added:{type:'string', description:'추가 테스트 수 + 무엇 검증(crypto 배제·세션 부활·coherence reject)'},
    key_decisions:{type:'string', description:'STEP2 화이트리스트 enforce 위치 + token 제거 / STEP3 session-wait 방식 / STEP4 guard 위치'},
    behavior_change:{type:'string', description:'의도적 거동 변경 명시(Capital universe crypto→FX/지수/금, 세션 처리). 무엇이 어떻게 달라지나'},
    migration:{type:'string', description:'고아 crypto:LIT cell/regime_state row 처리(TTL/cleanup/무해 확인)'},
    pytest_result:{type:'string', description:'pytest 실제 실행 결과(green/실패)'},
    ruff_mypy:{type:'string', description:'ruff + mypy --strict 결과'},
  },
}
const build = await agent(`${HEADER}\n${PLAN}\n${CODEREF}\n
[임무] STEP 2/3/4 통합 빌드 (TDD 엄수: 실패 테스트 먼저 → 구현 → pass).
**STEP 2 (근원, Capital crypto 배제)**: resolve_stream("capital").asset_classes={forex,index,commodity} 를 universe 선택의 SSOT로 **강제**. 핵심=옵션 B: universe persist/active 직전(discovery.py persist_universe 또는 refresh 경로)에서 asset_class ∉ stream.asset_classes 인 행 drop(crypto 제거). + 옵션 A 효율: CAPITAL_P0_CATEGORY_TOKENS(_capital.py:33-42)에서 "crypto" 제거 + "currenc"가 crypto_currencies_group 흡수 안 하도록 정밀화(FX currency만). ⚠OKX("crypto")·Alpaca("equity")는 영향 없어야(resolve_stream 각자 asset_classes). 태깅(canonical/_classify_capital_node)은 정확하니 건들지 말 것.
**STEP 3 (세션 비대칭, _ranking.py)**: _is_valid_candidate(state!='live' hard-drop)이 세션-닫힘 CFD FX/지수/금을 영구 배제하는 것을 교정 — CFD venue 'closed'를 hard-drop 대신 "세션 대기(watch)"로 라우팅해 다음 세션 open 시 부활. flow_not_block 정합(hard block 아님, 세션 상태 라우팅). crypto 24/7는 영향 없음. 단 Jin (a)대로 Capital은 세션 열릴 때만 거래(오프세션 거래 0 = 정상, OKX 24/7 담당) — 즉 '대기'는 활성셋에서 빠지되 영구 삭제 아님(다음 세션 복귀). 무한 누적 방지.
**STEP 4 (coherence guard)**: 런타임 Capital 심볼 asset_class ∈ resolve_stream("capital").asset_classes 검증(config.py:207을 doc-only→enforced). regression catch — crypto 태그가 B-stream에 재유입되면 drop/flag.
**마이그레이션**: 기존 regime_state/cell_matrix의 crypto:LIT 등 Capital crypto row는 STEP2 후 고아 — 무해 확인(read miss→default) 또는 TTL/cleanup. 신규 쓰기 안 되면 자연 stale.
TDD 필수: 실패 테스트(Capital active universe에 crypto asset_class 0건·FX/지수/금만·CFD closed가 다음세션 부활·coherence가 crypto 재유입 reject·OKX/Alpaca 무영향) → 구현 → pytest 실제 pass. ruff + mypy --strict clean.
schema 반환. pytest/ruff/mypy 반드시 실제 실행.`,
  {label:'build:capital-scope', phase:'Build', schema:BUILD_SCHEMA, agentType:'general-purpose'})

phase('Review')
const REVIEW_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['verdict','blocking','nits','aggressive_ok','migration_ok','no_collateral'],
  properties:{
    verdict:{type:'string', enum:['approve','approve-with-nits','reject']},
    blocking:{type:'array', items:{type:'string'}, description:'머지 블로커. 없으면 빈 배열'},
    nits:{type:'array', items:{type:'string'}},
    aggressive_ok:{type:'string', description:'crypto 배제가 방어 throttle 아님 확인(자산군 라우팅 교정, crypto edge OKX로). 거부키워드 sweep.'},
    migration_ok:{type:'string', description:'고아 crypto row 무해/cleanup 정합'},
    no_collateral:{type:'string', description:'OKX(crypto)·Alpaca(equity) 스트림 무영향 확인. 9-stack/sizing 무관.'},
  },
}
const review = await agent(`${HEADER}\n${PLAN}\n${CODEREF}\n
[임무] builder≠reviewer adversarial review (반박 기본). STEP 2/3/4 빌드를 적대 검증.
[빌드]\n${JSON.stringify(build)}\n
파일 직접 Read 검증: (1) Capital universe가 실제로 crypto 배제 + FX/지수/금만(화이트리스트 enforce 위치 정확). (2) STEP3 session-wait가 영구삭제 아닌 부활(무한누적·starvation 없음, flow_not_block). (3) STEP4 coherence가 regression 잡나. (4) ⚠OKX/Alpaca 무영향(resolve_stream 분리). (5) crypto 배제가 방어 throttle/blanket block로 퇴화 안 함(자산군 라우팅 교정, aggressive 보존). (6) 마이그레이션(고아 row) 정합. (7) 9-stack/sizing 무관. (8) 테스트 충분·ruff/mypy clean.
blocking은 보수적으로(진짜 collateral 손상/aggressive 위반/regression만). schema 반환.`,
  {label:'review:capital-scope', phase:'Review', schema:REVIEW_SCHEMA, agentType:'general-purpose'})

phase('Gate')
let final = { build, review, fix: null }
if (review.verdict === 'reject') {
  const fix = await agent(`${HEADER}\n${PLAN}\n${CODEREF}\n[임무] review가 reject. blocking 해결(TDD 유지, Jin (a) 의도 보존).\n[review]\n${JSON.stringify(review)}\n[원빌드]\n${JSON.stringify(build)}\nblocking 각각 수정 → pytest green → ruff/mypy clean. schema 반환.`,
    {label:'fix:capital-scope', phase:'Gate', schema:BUILD_SCHEMA, agentType:'general-purpose'})
  const rereview = await agent(`${HEADER}\n[임무] 재검증. fix를 다시 adversarial review.\n[fix]\n${JSON.stringify(fix)}\nschema 반환.`,
    {label:'rereview:capital-scope', phase:'Gate', schema:REVIEW_SCHEMA, agentType:'general-purpose'})
  final = { build, review, fix, rereview }
}
return final
