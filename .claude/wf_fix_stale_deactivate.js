export const meta = {
  name: 'fix-stale-deactivate',
  description: 'STEP 2 갭 수정: refresh 시 화이트리스트 위반 stale active row 자동 deactivate (라이브 검증이 잡은 갭 — persist는 이번 fetch만 UPDATE해 미-fetch crypto stale 유지)',
  phases: [
    { title: 'Build' },
    { title: 'Review' },
  ],
}

const HEADER = `[Polaris — DEMO/PAPER 가상자금. AGGRESSIVE bias 보존(throttle/축소/차단 거부; flow_not_block). 거부 키워드 채택 금지: regulatory cap·professional risk·real-money safety·90d gate·monthly review·posture standard. 불변: 9-stack 봉쇄·hard-MAX·DEMO. 봇 LLM=GPT, Anthropic 호출 금지. 코드: /Users/jinyoon/Projects/Polaris, Python 3.13 async, mypy --strict, ruff clean, TDD, pytest. vault 권한. 출력=raw, 산문 최소.]`

const CTX = `[라이브 검증이 잡은 갭] STEP 2(Capital crypto 배제)는 새 fetch를 화이트리스트(resolve_stream("capital").asset_classes={forex,index,commodity})로 필터해 crypto를 안 가져온다. 그러나 persist_universe(discovery.py:299)는 인자로 받은 instruments(이번 fetch=FX/지수/금)만 UPDATE → 기존 is_active=1 crypto stale row(이전에 persist됨)를 안 건드려 is_active=1로 남음 → focus가 stale crypto를 뽑음. (라이브: capital crypto 45 is_active=1, last_seen_ts 6.4h stale. 이미 DB 수동 정리했으나 코드 견고성 필요.)
[수정] venue refresh 시 화이트리스트 위반 기존 active row를 자동 deactivate. asset_class_allowed_for_venue(venue, asset_class)=False AND is_active=1 → is_active=0, active_reason='off_venue_class'. ⚠OKX(crypto 허용)·Alpaca(equity 허용)는 위반 없어 no-op. ⚠STEP 3 session-wait(off-session FX/지수/금 is_active=0, reason=session_wait)와 충돌 금지(이건 화이트리스트 통과한 자산군). ⚠crypto fetch는 STEP 2가 이미 막으니 이 deactivate는 stale 정리 전용.`

phase('Build')
const BUILD_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['files_changed','tests_added','approach','no_collateral','pytest_result','ruff_mypy'],
  properties:{
    files_changed:{type:'array', items:{type:'string'}},
    tests_added:{type:'string'},
    approach:{type:'string', description:'deactivate 위치(refresh 경로 vs persist_universe) + SQL/로직'},
    no_collateral:{type:'string', description:'OKX(crypto)/Alpaca(equity) no-op + STEP3 session-wait 무충돌 확인'},
    pytest_result:{type:'string'},
    ruff_mypy:{type:'string'},
  },
}
const build = await agent(`${HEADER}\n${CTX}\n
[임무] 화이트리스트 위반 stale active row 자동 deactivate (TDD: 실패 테스트 먼저→구현→pass).
1. 정독: polaris/core/universe/discovery.py(persist_universe), polaris/scripts/_production_layers.py(refresh_*_universe_once), polaris/core/streams(asset_class_allowed_for_venue).
2. 구현: venue refresh/persist 시 해당 venue의 asset_class_allowed_for_venue=False AND is_active=1 row를 is_active=0, active_reason='off_venue_class'로. (persist_universe 끝 또는 refresh 후 venue 단위 UPDATE가 깔끔 — fetch instruments에 없어도 venue 전체 sweep.)
3. ⚠불변: OKX(crypto only)/Alpaca(equity only)는 위반 0 → no-op(테스트로 고정). STEP3 session-wait(화이트리스트 통과한 off-session 자산군 is_active=0 reason=session_wait)는 건드리지 말 것(asset_class는 allowed라 이 sweep 대상 아님). aggressive(crypto edge OKX로 라우팅, throttle 아님). 9-stack 무관.
TDD: 실패 테스트(capital crypto stale active→deactivate·OKX crypto active 유지·Alpaca equity 유지·session_wait FX active=0 미변경 또는 무관) → 구현 → pytest 실제 pass. ruff+mypy --strict clean. **작업 완료 후 반드시 StructuredOutput(schema) 호출.**`,
  {label:'build:stale-deactivate', phase:'Build', schema:BUILD_SCHEMA, agentType:'general-purpose'})

phase('Review')
const REVIEW_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['verdict','blocking','nits','no_collateral'],
  properties:{
    verdict:{type:'string', enum:['approve','approve-with-nits','reject']},
    blocking:{type:'array', items:{type:'string'}},
    nits:{type:'array', items:{type:'string'}},
    no_collateral:{type:'string', description:'OKX/Alpaca no-op + session_wait 무충돌 + aggressive 확인'},
  },
}
const review = await agent(`${HEADER}\n${CTX}\n
[임무] builder≠reviewer adversarial review. stale-deactivate 빌드 검증(반박 기본).
[빌드]\n${JSON.stringify(build)}\n
파일 직접 Read: (1) deactivate가 화이트리스트 위반 active만 끄나(crypto). (2) ⚠OKX(crypto)/Alpaca(equity) no-op(자기 자산군 허용). (3) ⚠STEP3 session-wait(allowed 자산군 off-session)를 안 건드리나(asset_class allowed라 sweep 대상 아님 — 충돌 금지). (4) aggressive(throttle 아님). (5) 테스트 충분·ruff/mypy clean.
blocking 보수적(진짜 collateral/session 충돌/aggressive 위반만). schema 반환.`,
  {label:'review:stale-deactivate', phase:'Review', schema:REVIEW_SCHEMA, agentType:'general-purpose'})

return { build, review }
