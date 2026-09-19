# TASKS.md — krx-stock-scoring

> 기준: `SPEC.md` **v2.3** · `PLAN.md` **v0.2** (2026-09-19)
> 체크할 때마다 아래 대시보드를 함께 갱신한다. 마일스톤을 닫을 때는 **ruff · mypy strict · pytest 전부 통과**가 전제다.

---

## 진도율 대시보드

| 마일스톤 | 진도 | % | 태스크 | 상태 |
|----------|------|---|--------|------|
| **M0 선행 실측·계약·뼈대** | `████████░░` | 80% | 8/10 | 🔄 |
| M1 유니버스·기술·최소 저장 | `░░░░░░░░░░` | 0% | 0/8 | 🔜 |
| M2 기본·수급·규칙 확정 | `░░░░░░░░░░` | 0% | 0/7 | 🔜 |
| M3 공시·사전·뉴스 보조 | `░░░░░░░░░░` | 0% | 0/6 | 🔜 |
| M4 운영·게시·복구 | `░░░░░░░░░░` | 0% | 0/7 | 🔜 |
| M5 웹·접근 보호 | `░░░░░░░░░░` | 0% | 0/6 | 🔜 |
| M6 점수 검증·출시 판정 | `░░░░░░░░░░` | 0% | 0/4 | 🔜 |
| M7 MCP·Skill | `░░░░░░░░░░` | 0% | 0/2 | 🔜 |
| **전체** | `██░░░░░░░░` | **16%** | **8/50** | 🔄 M0 |

범례: 🔜 대기 · 🔄 진행중 · ✅완료일

**사용자 준비물 (블로커)**

| 시점 | 항목 | 상태 |
|------|------|------|
| M0 | 깃허브 리포 `daehyub71/krx-stock-scoring` 생성 (public/private) | ⏳ |
| ~~M0~~ | ~~kss 전용 Supabase~~ — 전용 프로젝트(싱가포르, 19 MB) Session pooler 접속·스키마 적용 | ✅ 2026-09-19 |
| M0 | 의존성 승인 (psycopg·certifi·langgraph, dev: pytest·ruff·mypy) · 규칙 파일 TOML 채택 | ⏳ (langgraph는 2026-09-19 채택) |
| M0 | 공유 DB 513 MB(한도 초과) — Supabase 대시보드에서 요금제·초과 시 동작 확인 | ⏳ scoring 범위 밖, 보고만 |
| M2 | KRX 로그인 계정 (charts 값 재사용 여부) | ⏳ |
| M4 | fine-grained PAT + charts `daily.yml` dispatch 단계 승인 | ⏳ |
| M5 | DESIGN 시안 합의 · Vercel 프로젝트 | ⏳ |

---

## M0 — 선행 실측·계약·뼈대

- [x] 상위 DB 읽기 전용 실측 — 용량·W/M 잔류·갈래별 완전성·유니버스·달력 (`scripts/m0_probe.py`, 세션 READ ONLY) — 2026-09-19
- [x] 추가 실측 — charts 실행 시각 34건, 수급 NULL 항등식, DART list.json 표본 → `docs/m0/M0_REPORT.md` — 2026-09-19
- [x] SPEC v2.1 → v2.2 (D1 전용 프로젝트, 수급 derived_zero, D2 M4 연결) — 2026-09-19
- [x] PLAN v0.1 — 2026-09-19
- [ ] 리포·venv·`pyproject.toml`(ruff·mypy strict·pytest)·`.gitignore`·CI(`ci.yml`) — 첫 푸시에서 CI 녹색
  - 2026-09-19 로컬 완료: `git init`(커밋 전)·venv(3.11)·의존성 설치·`.env`/`.env.example`·`ci.yml`. **남은 것: 깃허브 리포 생성 → 첫 푸시 → CI 녹색**
- [x] kss 전용 Supabase: `store/schema.sql`(M1 객체 7개)·RLS·anon 정책 없음·`kss_batch`/`kss_reader` 롤·`scripts/apply_schema.py` — 2026-09-19 적용. 롤 비밀번호는 무작위 생성해 `.env`에만. 롤 로그인 실측: batch 쓰기 ✅·bus-mate 표 거부 ✅ / reader 조회 ✅·쓰기 거부 ✅·비게시 표 거부 ✅
- [x] `tests/test_schema.py` — 파일 수준 14개 + 실DB(`-m db`: RLS 전부 켜짐·anon/authenticated 정책·권한 0) 통과 — 2026-09-19. 저장 열 = 읽기 열 왕복은 writer가 생기는 M1에서
- [x] `rules/v0.toml` + `scoring/rules.py`(로드·정규 해시) — 테스트 12개: 항목 만점 17개·공통 90·축 35/35/7/13·뉴스 별도·신용 비활성·도달 가능 최대·해시 안정성 — 2026-09-19
- [x] `scoring/calendar.py` + `domain/resample.py` (TDD) — 테스트 18개(주중·월중·추석 금요일 휴장·연말 월말 휴장·자정·매일 재처리 시 기간당 1행·미래 입력 차단) + **실DB 대조(`-m db`): 2026-09-07 주 2,700+종목 불일치 0** — 2026-09-19
- [ ] M0 사용자 확인

## M1 — 유니버스·기술 35·최소 저장

- [ ] `sources/upstream.py` — D 400거래일 일괄 적재(커서·행 수 검증), 적재 시간 실측
- [ ] 회귀 픽스처 — 실DB 표본 25종목 × 400일 CSV
- [ ] `domain/universe.py` — 우선주·스팩(alerts 이식)·금융계·신규·정지·드리프트·`classification_unknown`
- [ ] `domain/indicators.py` — SMA·RSI(Wilder 14)·MACD(12/26/9)·기울기, 워밍업
- [ ] `domain/technical.py` — 5항목, 손계산 골든 + 경계값
- [ ] `domain/aggregate.py` — 상태·관측률·axis_estimate·rank_eligible·passes_screen
- [ ] `store/writer.py` + `graph.py`(LangGraph 얇은 층)·`run.py --profile technical` — 청크 저장·publish 트랜잭션·중간 실패 시 이전 게시본 유지
- [ ] 전 종목 실행 3분 이내 · 하루 저장량 실측 → 보존 기간 확정 · `kss_signal_cross`(partial_technical)

## M2 — 기본 35·수급 13·규칙 확정

- [ ] `sources/krx.py` + `kss_sector_stats` (업종 중앙값·시장 폴백)
- [ ] `sources/dart.py` 이식 (corp·fnlttMultiAcnt 하강 탐색) + 호출 표본
- [ ] `domain/financial.py` 이식 + `domain/fundamental.py` (4+3 성장률·ROE 평균자본·부채비율)
- [ ] `kss_financial_versions`·`kss_corp_map`
- [ ] `domain/flow.py` — derived_zero·연속·규모 보정
- [ ] `domain/shorting.py` — 시장별 경계 확정
- [ ] 실종목 20개 수작업 대조 · §4.5 보완값 확정 → `rules/v0.toml` 고정

## M3 — 공시·사전·뉴스 보조

- [ ] 날짜축 공시 수집 (Y·K·전 페이지·`last_reprt_at=N`) → `kss_disclosures`
- [ ] `domain/flags.py` 이식 → 사전 시드 v1 (`kss_lexicon`·`_versions`)
- [ ] `domain/disclosure.py` — 30일 창·정정/철회·fatal·`kss_risk_events` 해제 근거
- [ ] 두 사전 버전 공존·재현 테스트
- [ ] 뉴스 선택 조회 (`passes_screen` 종목) — 목록만, 점수 null
- [ ] 뉴스 제목 300건 라벨링 → 사용 여부 결정 (D7)

## M4 — 운영·게시·복구

- [ ] `score.yml` — dispatch + 23:37 예비 cron + 07:17 복구, concurrency
- [ ] charts `daily.yml` dispatch 단계 (상위 리포 별도 커밋, 승인 후)
- [ ] 출처별 게이트 → `published_degraded` / `waiting_upstream`
- [ ] 복구 큐(5거래일)·heartbeat
- [ ] 입력 스냅샷·Parquet 아카이브·복원 검증·보존 삭제
- [ ] 모의 검증 — 지연·부분 실패·중복 이벤트·중간 저장 실패
- [ ] 5거래일 무인 관찰 (누락률·게시 일관성)

## M5 — 웹·접근 보호

- [ ] DESIGN.md — IA·와이어프레임·Claude Design 캔버스 시안 → 합의
- [ ] `/`·`/stock/[ticker]`
- [ ] `/lexicon` (kss_editor·CSRF·optimistic locking)
- [ ] `/cross`·`/status`
- [ ] Vercel 보호 실측 (All Deployments → 불가 시 verify 방식)
- [ ] 배포 전 보안 점검 · 비로그인 전 경로 차단 · 번들 grep

## M6 — 점수 검증·출시 판정

- [ ] 계산 정확성·관측률/업종/규모 편향
- [ ] 항목 상관·제거 민감도·일일 순위 안정성
- [ ] 시간순 분리 평가구간 초과수익 분포 (가능 범위)
- [ ] 검증 보고서·등급 컷 확정 (불충족이면 experimental 유지)

## M7 — MCP·Skill

- [ ] scorer-MCP (`score`·`explain`·`top`·`rescore`)
- [ ] 게시 점수와 설명 일치 · 실험이 게시본에 영향 없음

---

## 트러블슈팅 기록

| 일자 | 현상 | 원인 | 조치 |
|---|---|---|---|
| 2026-09-19 | kss DB(bus-mate)에 기존 앱 표 7개 공존 | 사용자가 기존 프로젝트를 전용으로 지정 | 접두어 `kss_`로 충돌 없음, kss 표는 anon 회수·RLS. bus-mate 표는 kss_batch도 접근 불가 확인 |
| 2026-09-19 | 상위 DB 주소가 트랜잭션 풀러(6543) | prepared statement 불가 | `config.connect_upstream`에 `prepare_threshold=None` |
| 2026-09-19 | M0를 `ksv_reader`로 할 수 없음 | 그 롤은 `ksv_*`만 SELECT | 배치 자격증명 + `default_transaction_read_only=on` 세션으로 실측(서버가 쓰기 거부). 상위 전용 SELECT 롤은 D2 범위 |
