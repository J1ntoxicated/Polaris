# RUNBOOK — Watchlist Intel 수집 방법·스케줄·아카이브

이 문서는 "어떻게 조사하는가"의 운영 매뉴얼. 규칙(헌법)은 `INSTRUCTIONS.md` +
`INSTRUCTIONS_CRYPTO_MACRO.md`, 출력 계약은 `CONTRACT.md` + `CONTRACT_CRYPTO_MACRO.md`.
충돌 시 헌법이 이긴다. DEMO/PAPER · flow_not_block (추가/랭크업만, 차단 없음).

## 1. 스케줄 (정보 리프레시 주기)
- **데일리 런**: 매일 22:00 시드니 로컬 (Cowork scheduled task `polaris-watchlist-intel`,
  실행 모델 Sonnet 5). US 프리마켓(EDT 07~08시경)에 해당. 앱이 켜져 있어야 실행되고,
  꺼져 있었으면 다음 앱 실행 시 돌아감.
- **평일**: 주식 전 축 + 크립토 축 + 매크로 축(FF 캘린더). **주말**: 크립토 축만
  (OKX 24/7), 매크로/CFD·주식 프리마켓 축은 세션 바운드라 스킵.
- **US 휴장일**: 직전 거래일 데이터 기준으로 수집하고 expiry는 "다음" 세션 마감으로.
- 축별 자체 주기: FINRA 숏볼륨=매 거래일, HighShortInterest 크로스체크=격주,
  Schwab Weekly Trader's Outlook=매주 금요일 발행, 인덱스 리밸런스=분기(3/6/9/12 첫 주).

## 2. 축별 소스 → 신선도 검증 → 폴백 (2026-07-04 실측 기준)
모든 소스는 **payload 안의 날짜를 먼저 확인**한다. 날짜가 없거나 오늘/직전 거래일과
안 맞으면 그 fetch는 버린다(§4 캐시 플레이북).

| 축 | 1차 소스 | 신선도 체크 | 폴백 (1차 실패/스테일 시) |
|---|---|---|---|
| 프리마켓 갭업 | stockanalysis.com/markets/premarket/ | 페이지 "Updated" 날짜 | WebSearch "premarket movers <date>" + Benzinga premarket |
| 실적 캘린더 | finviz screener earningsdate_* | (JS 차단 잦음) | WebSearch + Kiplinger 주간 캘린더 + Schwab 아웃룩의 Earnings 섹션 |
| 섹터 RS | finviz groups.ashx?g=sector&v=140 | 두 URL 변형(o=name / o=-perf1w) 교차 비교 | stockanalysis 섹터 페이지, WebSearch 섹터 recap |
| 52주 신고가×거래량 | finviz ta_newhigh (JS 차단 잦음) | — | WebSearch "52-week highs <date>" + TheStreet 라이브블로그 + Schwab 아웃룩 Notable 52wk Highs 리스트 |
| FINRA 숏볼륨 | cdn.finra.org CNMSshvolYYYYMMDD.txt | 파일명 날짜 = 직전 거래일 | 대용량이라 tool-result 파일로 저장됨 → Grep으로 심볼 추출. 절단 구간 밖 심볼은 해당 축 0점(추정 금지) |
| 뉴스 촉매 | TheStreet 데일리 라이브블로그(신선 확인됨), Reuters/CNBC/Barron's | 기사 published_time 메타 | finviz news.ashx는 스테일 캐시 이력 있음 → 주의. Google News RSS는 fetch로 빈 응답 이력 → WebSearch로 대체 |
| 티커 실존 검증 | stockanalysis.com/stocks/{sym}/ | "At close" 날짜 + Last checked | finviz quote.ashx는 JS 차단 이력. FINRA 파일에 심볼 존재 = 보조 검증 |
| OKX 상장/폐지 | www.okx.com/api/v5/support/announcements?annType=announcements-new-listings / -delistings | `pTime`(ms epoch)으로 ≤48h 판정 — 스냅샷 디프 불필요 | — |
| 토큰 언락 | cryptorank.io/token-unlock | 표의 날짜 라벨 | 스테일 캐시 이력 → WebSearch (tokenomist/dropstab/defillama unlocks) — 검증 가능 수치 없으면 0건 |
| BTC ETF 플로우 | bitbo.io/treasuries/etf-flows/ (신선 확인됨) | 표 최신 행 날짜 = 직전 거래일 | WebSearch (Farside/SoSoValue 스니펫) |
| ETH ETF 플로우 | WebSearch 스니펫 (지침대로) | 기사 날짜 | — |
| 크립토 뉴스 | Cointelegraph/CoinDesk RSS | (fetch가 XML을 binary로 반환 — 사용 불가 이력) | WebSearch "crypto <symbol> news <date>" |
| OKX 심볼 검증 | www.okx.com/api/v5/market/ticker?instId=BASE-QUOTE | code 0 = 실존. 단 **가격/ts는 캐시 오염 이력 → 시세로 쓰지 말 것** | — |
| 매크로 캘린더(평일) | nfs.faireconomy.media/ff_calendar_thisweek.json | date 필드(ET -04:00 → UTC 변환) | EIA 주간 스케줄 페이지 |
| 시장 컨텍스트/뷰 | Schwab Weekly Trader's Outlook, TheStreet 라이브블로그, 애널리스트 액션(TipRanks/TheFly 스니펫) | 발행일 메타 | WebSearch |

## 3. 직접 fetch 금지 (검증된 실패 목록)
Farside · CoinGlass · spglobal · coinmarketcal (403/JS-only, 헌법 명시) +
finviz screener/quote (JS 차단), Google News RSS·Cointelegraph/CoinDesk RSS
(이 환경의 fetch에서 빈/binary 응답). fetch 실패를 curl/파이썬으로 우회하는 것 금지.

## 4. 스테일 캐시 플레이북 (2026-07-04 사건에서 학습)
이 환경의 web_fetch는 오래된 캐시를 반환할 수 있다 (실측: finviz news 4월분,
stockanalysis 무버 6/18분, CryptoRank 6/18분, OKX ticker 5월분).
1. 모든 payload에서 날짜부터 찾는다 (메타 published_time, 표 날짜 행, epoch ts).
2. 날짜 불일치 → URL 변형(정렬 파라미터 추가 등)으로 캐시 키를 바꿔 재시도 1회.
3. 그래도 스테일 → 그 소스는 폐기, §2 폴백으로. **스테일 데이터로 후보 만들지 않는다.**
4. 어떤 축이 통째로 죽으면 그 축 0점 처리하고 요약에 명시 (추정으로 메꾸지 않는다).

## 5. 산출물 + 날짜별 아카이브
- 라이브 피드: `data/intel/alpaca_seed.json` (매 런 덮어쓰기, CONTRACT 스키마)
- **날짜별 아카이브 (매 런 필수)**:
  - `data/intel/history/alpaca_seed_YYYY-MM-DD.json` (피드 사본)
  - `data/intel/history/market_context_YYYY-MM-DD.md` (컨텍스트 사본)
- 시장 컨텍스트 최신본: `data/intel/market_context.md` — 지수/브레드스/금리·Fed 확률/
  원자재/ETF 플로우/섹터 RS/스트래티지스트·펀드 뷰/애널리스트 액션/이벤트 캘린더.
  후보가 아닌 "세션 맥락"은 seed가 아니라 여기에 넣는다 (bot은 seed만 읽음 — fail-safe).
- 쓰기 경로: cowork 마운트 밖이므로 Desktop Commander(DC) 도구로 쓴다.

## 6. 검증 (매 런, 쓰기 후 필수)
파이썬으로: JSON 파싱 → 필수 키 → venue ∈ {alpaca, okx, capital} → thesis_tag 허용값
→ score ∈ [0,1] → evidence ≥1 URL → `_sample` 부재 → expiry_ts 미래(다음 US 세션 마감)
→ 금지어 0히트 (INSTRUCTIONS.md Hygiene 리스트). 실패 시 파일을 남기지 말고 수정.

## 7. 원칙 리마인더
근거 URL 없으면 드랍 · 추측 금지 · 확신 낮으면 적게 · catalyst_ts는 소스가 보여준
날짜만(시각 불명 시 자정 UTC 절단 표기) · 점수는 랭크업 텀이지 게이트/사이즈가 아님.
