# SPOT Bot — Deployment 보류 (Jin 액션 필요)

**Date**: 2026-04-30 10:13 AEST
**Status**: 코드 배포 ✅ + DB 스키마 ✅ + 단위 테스트 64/64 ✅ — **단 OKX demo 인증 미설정으로 실거래 endpoint 401**

## 발견 (자동 배포 시도 중)

```
HTTPError: 401 Client Error: Unauthorized for url: https://www.okx.com/api/v5/trade/order
```

## Root cause

**메인 봇은 OKX 실거래 private endpoint 호출 X** — `invasion/exchange/okx/paper.py` 내부 시뮬레이터 사용. `.env` 의 `OKX_API_KEY/SECRET/PASSPHRASE` 는 public 데이터 fetch + paper 시뮬용. **실제 OKX private trade 권한 없음**.

SPOT 봇은 OKX V5 의 `x-simulated-trading: 1` 헤더로 **진짜 OKX demo 환경** 호출 → 별도 demo-trading.okx.com 에서 발급한 demo 전용 API key 필요.

## Jin 액션 (한 번만)

1. **OKX demo 키 발급** (https://www.okx.com/account/my-api → "Demo Trading" 탭)
   - API key, Secret, Passphrase 생성 (Read + Trade permission)
2. **`.env` 에 추가**:
   ```
   OKX_DEMO_API_KEY=...
   OKX_DEMO_SECRET=...
   OKX_DEMO_PASSPHRASE=...
   ```
3. **재시작**:
   ```bash
   set -a; source .env; set +a
   nohup python3 -m invasion.spot --headless --log-level INFO \
     > logs/spot_$(date +%Y%m%d_%H%M).log 2>&1 &
   ```

## 자동 시도 결과

- 배포 분기: `feat/spot-scalp-paper-bot` HEAD `71fb0e12`
- 18 commits + 4 phase tags + 64 tests passing
- Dry-run boot 정상
- 실시 시도 → 401 (위 root cause)
- DB 정리됨 (test pollution 제거: 0 trades / 0 cells)
- 봇 process 정지

## 대안 (Jin 결정)

만약 별도 demo keys 발급이 어렵다면:

**Option B: in-memory paper 시뮬레이터 추가** (메인 봇 패턴 차용)
- OKX private endpoint 호출 X
- WS price 받아 fill 시뮬 (slippage 가정 고정값)
- 장점: 즉시 가동
- 단점: maker queue 정확도 낮음 (fill quality 측정 부정확)

이 옵션 원하면 신호 줘. 약 250 line 추가 (paper_fill_spot.py 신규 + router_spot 분기).

## 메인 봇 영향

**0** — SPOT 봇은 별도 process + 별도 sqlite. 메인 봇 (PID 98050) 정상 운영 중. SPOT 봇 부재가 메인에 영향 X.

## 다음 step

Jin 복귀 후:
1. 위 액션 1-3 또는 Option B 결정
2. 실시 1주 운영 시작 → `docs/spot/phase-5-checklist.md`
