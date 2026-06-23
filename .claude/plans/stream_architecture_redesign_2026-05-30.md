Claims verified: `CFD_LEVERAGE_DEFAULT = 30.0` flat hardcode at engine line 47, venue-binary track/leverage at run_signal 123-124, `Track = Literal["A","B"]` at sizing/schema.py:202, `asset_class` conflation at base.py:43, 4 ALPACA env keys present. Synthesis follows.

---

# Polaris 3-Stream 통합 실행 계획 (설계 종합)

**DEMO/PAPER only. 설계 문서 — 코드 변경/커밋 0. AGGRESSIVE bias 보존. 거부키워드 sweep = 0건.** (regulatory/professional risk/fractional Kelly/12주/90d gate/monthly review/posture standard/표본 부족 — 본 문서 전체 0건. 기존 코드의 `KELLY_FRACTION_K` 산식 상수는 변경 대상 아님.)

5개 facet을 단일 계획으로 통합. 충돌 해소 결과는 §0에 먼저 명시.

---

## 0. Facet 간 충돌·중복 해소 (선결)

5개 설계가 **같은 추상화에 3개 다른 이름**을 제안했다. 통합 SSOT를 1개로 확정한다.

| facet | 제안한 레지스트리 | 차원 |
|---|---|---|
| 1 | `StreamConfig` (`core/streams/config.py`) | stream_id 중심, 가장 포괄적 |
| 3 | `StreamProfile` (`core/sizing/stream_profile.py`) | 사이징/세션/비용 중심 |
| 4 | `VenueProfile` (`venues/registry.py`) | venue 중심 룩업 |

**해소 = 단일 `StreamConfig` 레지스트리 1개로 통합** (facet 1을 골격으로 채택). 이유:
- venue 중심(facet4 `VenueProfile`)은 "한 venue=한 stream" 가정이 박혀 미래에 한 venue가 복수 stream 가질 때 깨짐. facet5가 명시적으로 `stream_id`를 1급 차원으로 두라고 요구 → **stream_id가 1급, venue는 속성**.
- 사이징 전용 profile(facet3 `StreamProfile`)은 `StreamConfig.sizing_profile` 서브객체로 흡수.
- **위치**: `polaris/core/streams/config.py` (사이징 레이어 아님 — stream은 라우팅/격리 차원이므로 사이징보다 상위). facet3의 `resolve_profile()`은 `StreamConfig.sizing_profile`을 반환하는 헬퍼로 잔존.

**product_class 의미 정정 — facet 전원 합의**: `StrategyMetadata.asset_class`(base.py:43, 주석 `"spot"/"fx"/"index"/"commodity"`)는 사실 **product_class**다. 진짜 asset_class(crypto/forex/equity)와 충돌. 해소 = `product_class` 신규 추가 + `asset_class`는 진짜 자산군으로 의미 정정 (둘 다 보존, breaking 회피).

**Track 확장 충돌**: facet4는 `Track = Literal["A","B"]`→`"C"` 추가가 sizing chain 변경이라 **/debate + codex 의무**라 표시. facet1/3은 "캡 추가일 뿐 mult 누적 아님 → mandate 위반 X"로 정당화. **통합 판정**: 둘 다 맞다 — track C는 9-stack 위반 아니나(headroom min의 새 cap 1개), track C 캡 **수치**(gross%/daily%)는 트레이딩 파라미터 → /debate 대상. 슬롯 추가는 자유, 수치는 debate.

---

## 1. 목표 3-스트림 아키텍처 요약도

```
                        ┌─────────────────────────────────────────────┐
                        │  StreamConfig 레지스트리 (SSOT, 1급 차원)     │
                        │  core/streams/config.py                       │
                        │  STREAMS: dict[StreamId, StreamConfig]        │
                        │  resolve_stream(venue, product_class) →lookup │
                        └─────────────────────────────────────────────┘
                                          │ 모든 venue 이진분기(20+곳) → lookup 1줄
        ┌─────────────────────────────────┼─────────────────────────────────┐
   STREAM A                          STREAM B                          STREAM C
   A_okx_crypto                      B_capital_cfd                     C_alpaca_equity
   ─────────────                     ─────────────                     ───────────────
   venue: okx                        venue: capital                    venue: alpaca
   product_class: spot               product_class: cfd                product_class: equity
   asset_class: {crypto}             asset_class:{forex,index,         asset_class: {equity}
                                       commodity}
   track: A                          track: B                          track: C  ⚠️신규
   leverage: 1.0 고정                 per-market constraint             1.0 (P1 ≤2)
                                       (FX~30/idx~20/cmd~20/cCFD~2)
   short: ✗                          short: ✓ (유일)                   ✗ (P1)
   session: 24/7 always_on           fx_indices_cal (세션)              us_equity_cal RTH/PDT/갭
   universe: okx_tickers             capital_navigation                alpaca_assets
   전략 4종 (롱only)                  전략 3종 양방향(=6경로)            전략 3종 (롱only)
   volume_burst/tsmom/               fx_breakout/xau_indices/          equity_tsmom/
   rsi_bb/spot_donchian              session_breakout (+숏 미러)        equity_rsi_bb/equity_gap_go★
   ─────────────                     ─────────────                     ───────────────
   글로브: 시안 #5fdfff               퍼플 #a87cff                       골드 #ffc84f
   POS 좌콜로니 / MKT 후좌180°        POS 우콜로니 / MKT 전좌(3-sub)     POS 상콜로니 / MKT 전우

  공통(불변): T4 sizing chain [base×continuous×tier×cell×listing×learner] · headroom_min() ·
              SINGLE_TRADE_ABSOLUTE_CEILING 0.09 · Layer7 strategy_id 격리 · circuit breaker(무결성-only)
```

핵심 원리: **stream은 라우팅/격리 차원이지 sizing damper가 아니다.** stream이 공급하는 것은 (a) leverage(notional 배수, 기존 intent 필드), (b) caps(headroom_min 입력), (c) 진입 게이트(net-edge/세션/PDT), (d) 어댑터 dispatch — **곱셈자 체인에 mult 추가 0** → 9-stack 봉쇄 유지.

---

## 2. product_class 차원 도입 + venue 분기 철폐

### 2.1 conflation 진단 (코드 검증 완료)

근본 문제는 "venue≡product≡asset 1:1"이 아니라 **두 개념이 동일 토큰 `asset_class`를 공유**:
- `base.py:43` `asset_class` = `"spot"/"fx"/"index"/"commodity"` → 실은 **product_class**
- `engine.py:94`, `schema_ddl_core.py:17` `asset_class` = `"crypto"/"forex"/..` → **진짜 asset_class**
- `compute_underlying_group_id`/`resolve_cluster_id`는 진짜 자산군 의미로 사용 → 전략 메타와 의미 불일치

검증된 venue 이진분기 (전부 lookup 치환 대상):

| 위치 | 현재 코드 (검증됨) | 치환 |
|---|---|---|
| `_production_run_signal.py:123` | `track = "A" if venue=="okx" else "B"` | `stream.track` |
| `_production_run_signal.py:124` | `leverage = 1.0 if venue=="okx" else CFD_LEVERAGE_DEFAULT` | `stream.leverage_source` |
| `engine.py:47` | `CFD_LEVERAGE_DEFAULT = 30.0` flat | per-market constraint.leverage |
| `_production_pipeline.py:125-170,353` | `if venue=="okx" ... else capital` | `stream.adapter_factory` / `stream.external_reject_codes` |
| `_production_close_effects.py:97` | `group="spot" if venue=="okx" else "cfd"` | `stream.product_class` |
| `sizing/schema.py:202` | `Track = Literal["A","B"]` | + `"C"` ⚠️ |
| `engine.py:157-162` | `venue_per_symbol_cap`: capital vs spot | `stream.sizing_profile` |
| `canonical.py:25-61` | equity 분기 없음(fallback 동작) | 명시 `equity:{sym}` |
| `universe/schema.py:41` | `ATR_FLOOR_BY_CLASS` equity 키 없음 | `"equity":1.0` 추가 |

### 2.2 StreamConfig SSOT (통합 spec — 신규 `core/streams/config.py`, ≤200 LOC)

```
StreamId = Literal["A_okx_crypto", "B_capital_cfd", "C_alpaca_equity"]
Track    = Literal["A", "B", "C"]   # schema.py:202 확장

@dataclass(frozen=True, slots=True)
class SizingProfile:               # facet3 StreamProfile 흡수
    leverage_source: str           # "fixed_1" | "per_market_constraint" | "fixed_low"
    per_symbol_cap_pct: float      # env-override 유지 (POLARIS_CAP_*)
    base_risk_pct: float           # 0.02 공통
    cluster_ids: tuple[str,...]

@dataclass(frozen=True, slots=True)
class StreamConfig:
    stream_id: StreamId
    venue: str                     # okx|capital|alpaca
    product_class: str             # spot|cfd|equity   ← 1급 (base.py:43 의미)
    asset_classes: frozenset[str]  # {crypto}|{forex,index,commodity}|{equity}
    track: Track
    allow_short: bool              # F | T | F(P1 T)
    adapter_factory: Callable[..., VenueAdapter]
    universe_source: str
    strategy_roster: frozenset[str]
    sizing_profile: SizingProfile
    session_calendar: str          # always_on | fx_indices_cal | us_equity_cal
    cost_model: str                # spot_taker | cfd_spread | equity_commission
    external_reject_codes: frozenset[str]

STREAMS: dict[StreamId, StreamConfig]
VENUE_TO_STREAM: dict[str, StreamId]      # 역인덱스
resolve_stream(venue, product_class) -> StreamConfig
```

모든 호출처: `s = resolve_stream(venue, pc); s.track / s.leverage_source / s.adapter_factory`. **9개 이진분기 → 데이터 조회 1줄.**

### 2.3 스키마 변경 (ADDITIVE only, 마이그레이션 안전)

모든 신규 컬럼 `DEFAULT ''` → 기존 행 자동 backfill. SQLite는 `IF NOT EXISTS` 없으므로 `pragma table_info` 체크 후 `ALTER TABLE ADD COLUMN` idempotent 헬퍼.

1. `universe` (schema_ddl_core.py:17 다음) — `product_class TEXT DEFAULT ''` + `stream_id TEXT DEFAULT ''`. 기존 `asset_class`는 진짜 자산군 의미로 유지(cluster_cap 의존).
2. `positions` (schema_ddl_core.py:323-337) — `product_class` + `stream_id`.
3. `StrategyMetadata` (base.py:43) — `product_class` 신규 추가, `asset_class`는 진짜 자산군으로 정정 (둘 보존 → 점진).
4. `SignalIntent` (engine.py:81-106) — `product_class` + `stream_id` 추가. **새 스칼라 추가 없음** — leverage/session 필드 이미 존재.
5. `Bar`/`QuoteTick` — **변경 0** (시세는 product_class 직교).
6. `canonical.py:55-61` — `cls=="equity" → f"equity:{sym}"` 명시 분기.

**underlying_group_id 충돌 해소** (facet1): Capital `US500`(지수 CFD) vs Alpaca `SPY`(ETF) 둘 다 S&P500 노출. 원칙 = **노출 한도는 경제적 underlying으로 통합(과집중 방지), 실행은 stream_id로 분리**. P0는 단순 `equity:SPY`로 두고 cluster 충돌 없음, ETF→index alias 테이블은 P1.

---

## 3. 스트림별 스펙표

### 3.1 전략 로스터 (facet2 — 현 7전략 전부 하드코딩 long 확인)

**STREAM A (4종, 롱only 고정, 변경 0)**: volume_burst(1m 이벤트)·tsmom(1H 모멘텀)·rsi_bb_pullback(15m 평균회귀, `close>ma_200` 내장 추세필터로 롱only 천연 정합)·spot_donchian(1H 돌파). 4-edge 완비. spot 차입공매도 불가 → product_class 게이트가 `crypto_spot→short 차단` enforce. **신호 로직 무변경.**

**STREAM B (3종 → 양방향 6경로)**: 숏은 신규 전략 추가가 아니라 **현 3종 대칭 미러** (MarketView에 `donchian_low_*`/`bb_upper`/ATR 이미 존재, 미사용). 신규 correlation_group 0, 신규 파라미터 0:

| 전략 | 롱 트리거 | 추가 숏 트리거 |
|---|---|---|
| fx_breakout_basket | `close>donchian_high_40 & adx>20` | `close<donchian_low_40 & adx>20` |
| xau_indices_trend | `close>donchian_high_30 & mom_20>0` | `close<donchian_low_30 & mom_20<0` |
| session_breakout | `close>open+1.5×ATR` | `close<open−1.5×ATR` (동일 open window) |

내부: 롱 분기 먼저→미발화 시 숏 분기 (상호배타). `adx>20`은 방향 무관 게이트. product_class=`cfd`만 숏 허용. **P1 선택**: `cfd_rsi_bb_fade`(역추세 균형용).

**STREAM C (3종, P0 롱only)**: equity_tsmom(tsmom 재사용, TF→1D)·equity_rsi_bb_pullback(rsi_bb 재사용, 대형주 dip-buy)·**equity_gap_go(신규)** — 주식 고유 갭: `open≥prev_close×(1+gap_pct~2%) & open>prev_high`→롱. MarketView에 `prev_close`/`gap_pct` 추가 필요. volume_burst/spot_donchian은 C 제외(PDT 충돌 / edge 중복). P1 숏 = equity_tsmom 미러 + gap_down. 신규 코드 = 1종 + 2 메타-리스킨.

### 3.2 사이징/세션/비용 통합표 (facet3, T4 불변)

| 입력 | A crypto_spot | B cfd | C us_equity |
|---|---|---|---|
| leverage | 1.0 고정 | **per-market** translator (FX~30/idx~20/cmd~20/cCFD~2) — 하드코드 30.0 제거 | 1.0 (P1 ≤2) |
| direction | long only | long+short | long only (P1 short) |
| base_risk_pct | 0.02 | 0.02 | 0.02 |
| continuous/tier/cell/listing/learner | **전 스트림 공통, 무변경** | 공통 | 공통 |
| hard-MAX 0.09 + headroom_min | **불변** | 불변 | 불변 |
| session | always_on (중립 1.0, 24/7) | fx_indices_cal (asia/eu/us + 주말갭) | us_equity_cal (RTH 13:30–20:00 UTC/pre/after/closed + PDT) |
| cost_model | OKX 현물 taker/maker bps | spread×2 (overnight은 보유중 monitor) | per-share commission + spread + SEC/TAF |
| cluster | crypto:BTC+ETH | cfd:XAU+indices, FX_majors | equity:MEGA_CAP (신규) |

**B leverage 버그가 본 통합의 최대 실효 이득**: 현재 30:1이 지수/원자재/crypto-CFD를 과대 사이징 중. `size_usd_to_lots`는 이미 per-market `pip_value_usd`로 변환하므로 intent.leverage만 per-market로 바로잡으면 정합. **이건 trading 파라미터 → /debate 대상.**

**비용→net-edge** (facet3, 방어 throttle 아님): `net_edge_r = gross_edge_r − roundtrip_cost_r`. G4 pre_entry_watcher/G5 직전에서 `net_edge_r ≤ 0`이면 skip. g6_call_gate.py:20이 명시한 "cost optimisation, not defensive throttle"과 동일 철학. **+net-edge면 전량 통과, 크기 축소 0** → flow_not_block 보존. cost는 expectancy 판단에만, 사이징 곱셈자로 절대 안 넣음.

**세션/PDT** (facet3/4): deterministic Python 캡(governing-risk, LLM 아님). PDT = `daytrade_count≥3`이면 **신규 day-trade성 진입만 랭킹 다운(차단 아님), overnight hold 자유**. 무결성 캡(venue reject 방지)이지 P&L halt 절대 X.

---

## 4. Alpaca 연동 (facet4)

### 4.1 선결조건 — .env 키 (4개 ALPACA 키 존재 확인)
- `ARCHIVE_ALPACA_LIVE_*` = **사용 금지** (DEMO mandate). `ARCHIVE_ALPACA_PAPER_API_KEY`(`PK...` prefix 정상) = 스트림C용.
- 어댑터는 `ALPACA_PAPER_API_KEY`/`_SECRET` 1차 → `ARCHIVE_ALPACA_PAPER_*` fallback. **PAPER base_url 하드코딩 + LIVE 키 거부** 이중 안전장치.
- **blocking**: 실호출 1회 검증 (`GET /v2/account`, `account_blocked`/`trading_blocked` false). `ARCHIVE_` prefix가 "만료" 의도면 신규 발급 필요.

### 4.2 신규 모듈 `polaris/venues/alpaca/` (OKX 패턴 미러, 서명 없음→더 단순)
- `adapter.py` — 헤더 인증 `APCA-API-KEY-ID`/`APCA-API-SECRET-KEY` (key/secret 로그 금지). base: `paper-api.alpaca.markets`(거래) + `data.alpaca.markets`(시세). 메서드: fetch_bars/fetch_quote/place_market_order(`notional=USD` 사용, share-rounding 회피)/cancel/fetch_account/fetch_positions/fetch_order. `AlpacaOrderResponse` dataclass.
  - **idempotency 필수 미러** (OKX #12 교훈): `client_order_id` 멱등키 + `_place_order_with_retry`/`_lookup_existing_order` (재시도 전 `?client_order_id=` 중복확인).
- `constraint_translator.py` — `GET /v2/assets` → `AssetConstraint`(tradable/fractionable/shortable/easy_to_borrow). 시작 시 1회 캐시.
- `calendar.py` — `GET /v2/clock`(is_open, 캐시 1분, 휴일/조기마감 권위) + PDT(`daytrade_count`/`pattern_day_trader`).
- **session.py 미작성** — Alpaca 정적 헤더 = 토큰 만료 없음 (불필요 추상화 금지).

### 4.3 canonical/universe 통합
- `canonical.py` equity 분기, `universe/schema.py` `ATR_FLOOR_BY_CLASS["equity"]=1.0` (crypto 2% 플로어 적용 시 주식 전멸 — hard-block 아닌 랭킹 신호이므로 aggressive 보존), 신규 `core/universe/_alpaca.py`(`_capital.py` 미러), `discovery.py`/`_production_layers.py`/`production_paper_loop.py`에 refresh 배선. `UniverseInstrument`는 free-form str이라 변경 0.

### 4.4 close-leg
Alpaca close = `DELETE /v2/positions/{symbol}` 한 방 (OKX보다 간단). close reject는 strategy fault 아님 (commit 1a315a3 교훈 — `_is_external_reject`에 Alpaca market-closed/PDT 코드 추가, 안 하면 부당 HARD_HALT).

---

## 5. 대시보드 재구성 (facet5)

**핵심 피벗**: 글로브(`sphere-render.js`)는 이미 venue를 1급 인코딩(EXCHANGE_COLOR L58, POS_SUB_CENTERS L413, MKT_SECTORS L433). conflation은 데이터 계층 문제이지 시각화 문제 아님. → "재배선"이 아니라 "stream 의미 라벨 + product_class 서브차원(cap에만)".

**레이아웃 = 탭 거부, 3-레인 동시감시** (AGGRESSIVE 본질 = 한 stream 드로다운 중 타 stream 진입). 현 30/70 split 유지, 우측 70%를:
- row1 `auto` = TOTAL strip (NetPnL Σ/Equity Σ/uPnL Σ/DD, 항상 상단)
- row2 `auto` = 3-stream 레인 헤더 (색 보더)
- row3 `minmax(0,2.4fr)` = 3-column body (`1fr 1fr 1fr`): 각 컬럼 = KPI mini→positions(B는 L/S 뱃지)→gate funnel→universe(B는 fx/idx/cmd 3행) 세로 스택, `border-left:4px solid var(--stream-X)`
- row4 `minmax(0,1fr)` = shared bottom (recent trades 색-prefix / edge / learners / alerts — cross-stream 학습은 분리하면 표본 분산이라 공유)

**색 토큰** (글로브 hex와 동일): `--stream-a:#5fdfff; --stream-b:#a87cff; --stream-c:#ffc84f`.

**글로브**: POS 3콜로니=3심장 / MKT 섹터(cap 90°를 fx/idx/cmd 3-sub로 분할, product_class 명도변주) / tier=radius / strategy=asset_group색. 4차원 직교.

**2단계 롤아웃**:
- **단계1 (코드 무변경, 즉시)**: `board.js`에서 `venueToStream={okx:'A',cap:'B',alp:'C'}` 클라이언트 group-by. A/C 정확, B는 단일 레인.
- **단계2 (snapshot 확장)**: `DashboardSnapshot.streams: list[StreamSummary]` + `collect_snapshot`에 `GROUP BY venue/product_class`. stream-scoped equity/gate funnel/B 내부 분해. **`server.py` 무변경** (`dataclasses.asdict` 자동 직렬화).

---

## 6. 마이그레이션 시퀀스 (안전 순서 — 현 OKX 봇 중단 없이 점진)

**원칙**: 각 단계가 거동 동일성 회귀 통과 후 다음 진행. 신규 컬럼 `DEFAULT ''` + idempotent ALTER = 기존 DB 무중단.

```
[P0-0] 선결 (blocking)
  ├─ .env Alpaca paper 키 GET /v2/account 검증 (만료 시 재발급)
  └─ /debate: (a) B leverage 30.0→per-market 버그, (b) track C 캡 수치
              → codex 외부 review + GPT/Gemini 교차검증

[P0-1] 데이터 계층 (거동 무변경, 순수 추가) — 봇 무중단
  ├─ schema: universe/positions에 product_class+stream_id 컬럼 (DEFAULT '')
  ├─ idempotent ALTER 헬퍼 (pragma table_info 체크)
  ├─ backfill one-shot UPDATE: okx→spot, capital→cfd
  ├─ canonical.py equity 분기 + ATR_FLOOR equity 키
  └─ verify: 기존 OKX+Capital 거동 동일 (회귀 테스트)

[P0-2] StreamConfig 레지스트리 도입 (2-venue만, Alpaca 미포함)
  ├─ core/streams/config.py: STREAMS = {A_okx, B_capital} 만 등록
  ├─ venue 이진분기 9곳 → resolve_stream() lookup 치환 (한 곳씩)
  ├─ StrategyMetadata product_class 추가 (asset_class 의미 정정, 둘 보존)
  ├─ SignalIntent product_class+stream_id 추가
  └─ verify: 거동 100% 동일성 (track/leverage/adapter dispatch 결과 불변)
       ★ 이 시점까지 Alpaca/track C/숏 일절 없음 = 리스크 최소, 봇 무중단

[P0-3] B 숏 활성화 (Capital, 신규 venue 아님)
  ├─ fx/xau/session 3전략 숏 미러 분기 (신규 파라미터 0)
  ├─ product_class=cfd 게이트에서만 숏 허용
  ├─ B leverage per-market (P0-0 debate 승인 후)
  └─ codex review (신규 거동) + verify

[P0-4] Track C + Alpaca 어댑터 (신규 venue)
  ├─ sizing/schema.py Track "C" 추가 + track_c 캡 (P0-0 debate 수치)
  ├─ venues/alpaca/ 모듈 (adapter/constraint_translator/calendar)
  ├─ core/universe/_alpaca.py + refresh 배선
  ├─ StreamConfig에 C_alpaca_equity 등록 + VENUE_TO_STREAM[alpaca]
  ├─ _is_external_reject에 Alpaca reject 코드 (부당 HALT 방지)
  ├─ equity 전략 3종 (2 리스킨 + equity_gap_go 신규)
  ├─ RTH/PDT 게이트 (랭킹 다운, 차단 아님)
  └─ codex review + /debate (sizing) + verify

[P0-5] 대시보드 단계1 (코드 무변경, 즉시 가동 가능 — P0-1과 병행 가능)
[P1]   대시보드 단계2 (StreamSummary) + glove product_class 서브섹터
       + B cfd_rsi_bb_fade + C 숏 + ETF→index alias
```

**무중단 보장 근거**: P0-1/P0-2는 거동 동일성 회귀가 게이트 → OKX 봇 계속 실행 중 점진 적용. P0-4 Alpaca는 신규 stream이라 기존 A/B에 영향 0 (Layer7 strategy_id 격리가 이미 stream-safe — facet1). 봇/웹서버 kill 불필요.

---

## 7. 리스크 / 오픈 이슈 / Jin 결정 필요

**Jin 결정 필요 (decision gate):**
1. **B leverage 30.0→per-market** — 현재 라이브 버그(지수/원자재/crypto-CFD 과대 사이징). 트레이딩 파라미터 → **/debate 의무**.
2. **Track C 캡 수치** (gross%/daily%) — 슬롯 추가는 자유, 수치는 trading 파라미터 → **/debate 의무**. (본 설계는 슬롯만, 수치 미정.)
3. **Alpaca PDT** — DEMO지만 paper도 PDT 시뮬 가능. 무결성 캡(설계 가정) vs 완전 무시?
4. **CostModel 수수료율 출처** — universe discovery 시 fetch vs profile 상수?
5. **.env ARCHIVE_ prefix** — 만료/회수 의도? 신규 키 발급 필요 여부.

**리스크:**
- `StrategyMetadata.asset_class` 의미 정정 = breaking → 사용처 전수 grep 미완(본 설계 미수행). 안전책 = product_class 신규 추가 + asset_class 보존(점진).
- `_is_external_reject` Alpaca 코드 누락 시 reject가 strategy fault로 오분류 → 부당 HARD_HALT (commit 1a315a3 교훈).
- equity RTH 게이팅: 현 `derive_session`(session.py:33)은 asia/eu/us만 → us_equity_cal 신규 모듈 필요.
- Track "C" 확장은 `Track` 타입 읽는 모든 곳 동시 수정 (engine.py:96 등) + codex 의무.

**불변식 (5-axis 사전 체크 통과):**
- 9-stack 봉쇄: 곱셈자 체인 0 변경 (stream은 leverage/cap/expectancy로만 차별화) ✓
- hard-MAX min(): headroom_min + 0.09 ceiling 불변 ✓
- AGGRESSIVE: base/tier 3.0×/top 보존, cost는 +net-edge 전량통과, session always_on(A)/중립, defensive throttle 0 ✓
- circuit breaker: PDT/세션 = 무결성 캡(신규 진입 보류)만, P&L halt 0, 지는 전략은 learner/edge 재배분 ✓
- DEMO/PAPER: 전 캡 0.99 default, 가상자금, LIVE 키 거부 ✓

---

## 8. 신규 Task 분해 (빌드 단계별)

| Task | 의존 | 난이도 | 산출물 | review |
|---|---|---|---|---|
| **T0a** .env Alpaca 키 검증 | — | blocking | GET /v2/account OK/재발급 | — |
| **T0b** /debate: B leverage + track C 캡 | — | blocking | 수치 확정 | codex+GPT/Gemini |
| **T1** schema 컬럼+ALTER 헬퍼+backfill | T0 무관 | 낮음 | product_class/stream_id 컬럼, idempotent migration | 회귀 |
| **T2** canonical equity + ATR_FLOOR | — | 낮음 | equity 분기 | 회귀 |
| **T3** StreamConfig 레지스트리 (2-venue) | T1 | 중간 | core/streams/config.py + resolve_stream | codex |
| **T4** 이진분기 9곳 → lookup 치환 | T3 | 중간 | run_signal/pipeline/close_effects 치환, 거동 동일성 | codex+회귀 |
| **T5** StrategyMetadata/SignalIntent product_class | T3 | 중간 | base.py/engine.py 필드 추가 | codex |
| **T6** B 3전략 숏 미러 | T4,T0b | 중간 | fx/xau/session 숏 분기 | codex |
| **T7** B leverage per-market | T4,T0b | 중간 | constraint.leverage 배선 | codex+debate |
| **T8** Track "C" + track_c 캡 | T0b | 높음 | schema.py:202 확장, dict lookup | codex+**debate** |
| **T9** alpaca/ 어댑터 모듈 | T0a | 낮음 | adapter/constraint_translator/calendar (idempotency 미러) | codex |
| **T10** universe/_alpaca + refresh 배선 | T9,T2 | 낮음 | _alpaca.py + layers/loop wire | codex |
| **T11** StreamConfig C 등록 + _is_external_reject | T3,T8,T9 | 중간 | C_alpaca_equity, Alpaca reject 코드 | codex |
| **T12** equity 전략 3종 | T5,T10 | 중간 | equity_tsmom/rsi_bb 리스킨 + equity_gap_go 신규 (MarketView prev_close/gap_pct) | codex |
| **T13** RTH/PDT 게이트 | T9,T8 | 중간 | calendar 게이트 (랭킹 다운) | codex |
| **T14** net-edge CostModel | T5 | 중간 | G4/G5 net_edge_r≤0 skip | codex+debate |
| **T15** 대시보드 단계1 (venueToStream) | T1 | 낮음 | board.js group-by, 코드 무변경(snapshot) | — |
| **T16** 대시보드 단계2 (StreamSummary) | T1,T15 | 중간 | snapshot_models/snapshot/board/index.html/css | codex |
| **T17** 글로브 product_class 서브섹터 (P1) | T16 | 중간 | sphere-render cap 3-sub + 명도변주 | — |

**임계경로**: T0b → T8 → T11 → T12/T13 (track C + Alpaca 본체). **병렬 가능**: T1/T2/T15 즉시 착수, T9는 T0a만 의존. **신규 코드 표면**: alpaca 모듈 4파일 + streams/config.py + equity_gap_go 1전략 + B 숏 3미러 + 대시보드. 나머지는 치환/추가.

**핵심 결론**: 어댑터 작성은 쉽다(OKX 1:1 미러). 진짜 작업은 **venue 이진분기 → StreamConfig lookup 일반화(T3/T4)**와 **Track 3값 확장(T8, debate 필수)**. 마이그레이션은 데이터 추가(T1)→레지스트리(T3)→치환(T4)을 거동 동일성 게이트로 통과시킨 뒤 Alpaca를 신규 stream으로 얹어 **현 OKX 봇 무중단 점진 전환** 가능.

관련 신규 파일: `/Users/jinyoon/Projects/Polaris/polaris/core/streams/config.py`, `/Users/jinyoon/Projects/Polaris/polaris/venues/alpaca/{adapter,constraint_translator,calendar,__init__}.py`, `/Users/jinyoon/Projects/Polaris/polaris/core/universe/_alpaca.py`, `/Users/jinyoon/Projects/Polaris/polaris/strategies/equity_gap_go.py`. 핵심 변경 파일: `/Users/jinyoon/Projects/Polaris/polaris/scripts/_production_run_signal.py:123-124`, `/Users/jinyoon/Projects/Polaris/polaris/core/sizing/engine.py:47,81-106`, `/Users/jinyoon/Projects/Polaris/polaris/core/sizing/schema.py:202`, `/Users/jinyoon/Projects/Polaris/polaris/strategies/base.py:43`, `/Users/jinyoon/Projects/Polaris/polaris/core/data/canonical.py:25-61`.