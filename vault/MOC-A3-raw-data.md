---
type: moc
status: active
date_created: 2026-06-02
tags: [moc, axis-a3, raw-data, sqlite, telemetry]
---

# MOC-A3 — 로우데이터 (the DATABASE)

A3 축 = **실제 데이터베이스** = vault 밖 SQLite (`data/polaris*.sqlite`)의 raw-data 층. 이 축은 자기 vault 노트가 없다 — vault 가 A1↔A2↔A3 를 잇는 버스로서 여기를 **연결만** 한다 (RAG-like). 봇이 거래하며 쓰고, 학습기가 읽어 델타를 만든다.

## DB 테이블 (data/polaris*.sqlite)
- **ai_lessons** — post-trade 학습기 델타 (학습 출력, 노브 auto-tune 입력)
- **trades** — 체결 라운드트립 (entry/exit, fee, pnl)
- **positions** — 오픈/클로즈 포지션 ledger
- **quote_ticks** — 라이브 틱 시계열 (Layer 1 canonical feed)

## 아카이브
- `data/lessons_archive/` — 2627 telemetry .md 이관됨 (구 per-lesson 노트). 원본 raw 는 DB ai_lessons 가 SSOT, .md 는 cold 보관.

## 생산 / 소비 컴포넌트 (vault ↔ DB 다리)
- [[layer-1-canonical-baseline]] — quote_ticks / bars 를 **쓰는** 정규화 feed
- [[layer-5-learner-network]] — ai_lessons 를 **읽고 쓰는** 학습기, hourly auto-tune
- [[ADR-007-learner-network|ADR-007]] — Learner Network 결정 (ai_lessons 스키마 근거)
- [[ADR-010-venue-roundtrip-activation|ADR-010]] — Venue Round-Trip + DB 격리 (live vs paper sqlite 분리)

---
## 축 연결
- [[MOC-A1-design-dev]] — 이 데이터 스키마를 설계한 결정/컴포넌트
- [[MOC-A2-bot-ops]] — 이 데이터를 생산하는 봇 거래/운영
