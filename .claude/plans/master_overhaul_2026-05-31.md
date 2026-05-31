# Polaris Master Overhaul Plan — 2026-05-31 (Jin 비전 ↔ 현 구조 종합)

SSOT for the "싹 갈아엎기" overhaul. Inputs synthesized: engine adaptive-loop audit + full code-structure scan (186 files/39.5K LOC) + strategic research (AI-appropriateness / benchmarks / open-source / profitability) + globe-viz research + Jin's vision. auto_invasion mk3 borrowables = appended when that review lands.

## 0. North Star (Jin)
**자본 라이프사이클**: OKX 크립토=공장(earn) → Capital CFD=증폭기(opportunistic leverage L/S) → Alpaca=금고(장투 저장). 자본이 생산→증폭→보존으로 순환.
**Self-learning / self-evolving**: 거래→second-brain(vault) 기록→그 지식으로 새 전략·엑싯·시그널 **자가생성**→학습→향상→반복. ticker↔전략↔엑싯 리니지 기반 학습. 포지션 주기 리뷰→전략·엑싯 live 변경.
불변: DEMO/PAPER, AGGRESSIVE(flow_not_block, 방어throttle X), 9-stack 봉쇄, 거부키워드 0, 개발 GPT 금지(Claude만), 봇 LLM은 batch-tier만.

## 1. 핵심 리서치 결론 (의사결정 근거)
- **수익성(가장 중요)**: Polaris는 real 0.10% fee 기준 **+EV 가능**. 증거 = cell_matrix에 tsmom/bull_trend **+1.18R(n=87)** 입증 pocket. 현 손실 = ①그 edge를 chop/bear/crisis(−0.5~−2.3R)에 무차별 거래 ②churn. **고칠 코드는 대부분 이미 빌드됐고 꺼져있을 뿐**(entry_admission.py shadow, G3/G4 _shadow_rules, meta_label collection-only, regime layering live). → 갈아엎기가 아니라 **켜기+정밀화**가 핵심.
- **AI 적정성(학술+자체 audit 일치)**: LLM은 trade-level 신뢰불가(instruction-following=rubber-stamp; G7 46/46 HOLD). **"technical 결정, AI는 batch 지휘."** per-signal LLM은 컷, AI는 batch-tier로 재배치 = ①alt-data/뉴스 파싱 ②**self-evolve 전략 생성**(Chain-of-Alpha: LLM 제안→backtest 검증→승격) ③레짐 narrative(deterministic 탐지 뒤 2단계) ④이상감지. 비용↓+정밀도↑ 같은 방향. 학습 코어=NIG posterior+cell EWMA 유지.
- **벤치마크**: go-live 게이트 = 3-tier(상대: buy&hold BTC/SPY·naive TSMOM/BB 대비 Sharpe spread / 위험조정 / 통계 PSR·deflated-Sharpe) 전부 **real-fee-net 동일 clock**. 시간-게이트(12주/90d) 금지 — edge 입증 기준. 기존 confidence panel 확장.
- **오픈소스 차용**: R&D-Agent(Q) loop = self-evolve 청사진(propose→backtest→SOTA-set) · FreqAI 재학습 loop · Lean walk-forward + Black-Litterman(3-스트림 multi-alpha 배분) · mlfinlab/de Prado meta-labeling · vectorbt/Nautilus(replay/parity). **Phase1 = SQLite bars 위 deterministic replay·walk-forward 하네스(real fee)** — 모든 변경의 검증대. 리스크: LLM 전략생성 overfit/alpha-decay → 정규화·novelty·dedup 필수.

## 2. 현 상태 (정직)
✅ 실재: 거래소 실봉+deterministic 지표(AI 아님) · per-ticker 레짐(crypto:BASE, deterministic, 2-close confirm) · 8게이트(G1/G6/G8 deterministic, G3/G4/G7 GPT-mini) · 포지션 tick 관리 FSM(ATR트레일·MFE·loser-timeout) · 엑싯 live 적응(승자만) · **학습→피드백 부분 실재**(cell EWMA+NIG posterior가 종료거래로 sizing/rotation 갱신).
❌ 갭(스텁/dormant/미구현): 레짐→전략 **선택** 없음 · **live 전략 swap** stub(apply=False) · **리니지(position_strategy_segments) 미기록** · **self-evolve 전략생성 없음** · tick_recalc/recompute_exit_params 스텁 · meta_labels 소비안함·posterior sizing 미반영 · 엑싯 regime-비적응·G7 승자만 · 풀 멀티-TF(4H/1D 누락) · 실시간 가격 스트림(현 bar-close) · 스트림별 alt-data/뉴스(크립토만) · Alpaca 회계 $0 · 자본 lifecycle 자동흐름 없음.

## 3. 단계별 로드맵 (각 단계: Workflow design→build TDD→Claude 적대리뷰→behavior-gate→커밋, real-fee-net 벤치마크로 검증)
- **P0 — "edge 켜기"(최고 ROI, 대부분 dormant 코드 활성화)**: 벤치마크/replay 하네스(P1과 병렬) 위에서 ①regime-conditioned 진입(entry_admission shadow→검증→컷오버: +EV 레짐만, chop/crisis −EV warm 억제, cold=통과) ②churn 제어(이미 B로 anti-churn 라이브; turnover 지표 확인) ③posterior를 sizing에 반영. → breakeven→+EV 전환 목표.
- **P1 — 벤치마크/replay/walk-forward 하네스**: SQLite bars 기반 deterministic replay, real-fee, go-live 3-tier 대시보드. 모든 후속 변경의 검증대.
- **P2 — AI 재배치**: per-signal G3/G4 deterministic 컷오버(shadow acceptance 후) + G7 deterministic exit 소유. LLM을 **batch conductor**로: alt-data/뉴스 파싱·레짐 narrative·이상감지.
- **P3 — self-evolve 루프**: 리니지 기록(position_strategy_segments 실배선) + 전략 swap live(apply=True 게이트) + **LLM 전략/엑싯 생성기**(vault 리니지 read→제안→replay backtest→incumbent 능가시만 승격; novelty/dedup/정규화) + meta-label 2단계 모델.
- **P4 — 데이터**: 풀 멀티-TF(1m→1D) per ticker · 실시간 가격 스트림(ticker/WS, G4 tick gap도 메움) · 스트림별 alt-data/뉴스(Capital FX·원자재·채권 / Alpaca 미장).
- **P5 — 자본 lifecycle**: Alpaca 계좌 probe(실 equity) · 크립토→CFD→Alpaca 자본 흐름 · Black-Litterman multi-alpha 배분.
- **대시보드 트랙(병렬, display-only)**: E4 글로브(3 은하+중앙 conductor+클릭 줌인+라이브 flow/pulse/heartbeat, 잦은 refresh) · 실시간 가격 표시(P4 의존) · 사운드 재생기 제거 · Alpaca $0 fix(P5) · Playwright 웹 검증 셋업→탭 내부 일일이 검증.

## 3b. auto_invasion 차용 (mk3=실행안된 플랜, mk1=작동 코드 `/Users/jinyoon/Projects/auto_invasion_mk1-main/`)
mk3는 10레이어 monolith를 한꺼번에 얹다 엎음(=9-stack collapse 재현). **mk1의 작동 코드를 증분 차용**:
- **self-evolve(P3)**: `invasion/evolution/evolver.py`(변이 Gaussian/Bayesian/AI/Structural) + `tournament.py`(cell별 Elo) + `subsystem_reviewer.py`(retire/adjust/rollback). Polaris의 누락된 전략생성 루프의 검증된 참조.
- **regime→전략 선택(P0/P3)**: `market/regime.py` + `strategy/router.py`(regime별 softmax 선택) + regime별 exit 임계(bep/trail/max_hold) 스케일. Polaris "레짐→전략" 갭.
- **스트림별 alt-data(P4)**: `data/collectors/` 플러그인 패턴(crypto: coinglass/defillama/cryptopanic · CFD: oanda position-book/COT · equity: FRED/alpaca_news/EDGAR).
- **리니지(P3)**: cell matrix SSOT를 (exchange×group×regime×strategy×direction×ticker×liquidity) + per-cell fitness로 확장.
- **param 거버넌스**: FROZEN/CONFIG/DYNAMIC/COMPUTED 4-tier + amplify-only(단 warm 후 Sharpe decay 은퇴).
**회피 anti-pattern(mk3가 엎어진 이유)**: 게이트 ≤5 유지(9-gate→drop-through 복권화 금지), 단일축 fitness(Sharpe부터), 글로벌 param 증식 대신 cell override, 3rd-party 통째 복사 금지(원리만 추출·재구현), 한 번에 다 얹지 말고 증분.

## 4. 작업 방식
Workflow 오케스트레이션 기본, Claude만(개발 GPT 0), TDD→fresh-Claude 적대리뷰→behavior-gate→커밋. 거동 변경은 SHADOW 선행+벤치마크 검증 후 컷오버. 매 단계 real-fee-net 곡선/벤치마크로 게이트.

관련: [[project_self_evolving_vision]] · [[project_ai_conductor_direction]] · [[conductor_g3g4_cutover_2026-05-31]] · 리서치 출처(R&D-Agent/Chain-of-Alpha/FreqAI/Lean/Bookmap/Healey 등) raw=workflow transcripts.
