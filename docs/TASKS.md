# TASKS.md — krx-stock-scoring

> 기준: `SPEC.md` **v2.7** · `PLAN.md` **v0.6** (2026-09-20)
> 체크할 때마다 아래 대시보드를 함께 갱신한다. 마일스톤을 닫을 때는 **ruff · mypy strict · pytest 전부 통과**가 전제다.

---

## 진도율 대시보드

| 마일스톤               | 진도           | %       | 태스크      | 상태           |
| ------------------ | ------------ | ------- | -------- | ------------ |
| M0 선행 실측·계약·뼈대 | `██████████` | 100% | 10/10 | ✅ 2026-09-19 |
| **M1 유니버스·기술·최소 저장** | `██████████` | 100% | 8/8 | 🔄 사용자 확인 대기 |
| **M2 기본 36·수급 13·규칙 확정** | `██████████` | 100% | 13/13 | 🔄 사용자 확인 대기 |
| M3 공시·사전·뉴스 보조     | `░░░░░░░░░░` | 0%      | 0/6      | 🔜           |
| M4 운영·게시·복구        | `░░░░░░░░░░` | 0%      | 0/7      | 🔜           |
| M5 웹·접근 보호         | `░░░░░░░░░░` | 0%      | 0/6      | 🔜           |
| M6 점수 검증·출시 판정     | `░░░░░░░░░░` | 0%      | 0/4      | 🔜           |
| M7 MCP·Skill       | `░░░░░░░░░░` | 0%      | 0/2      | 🔜           |
| **전체** | `██████░░░░` | **56%** | **31/55** | 🔄 M2 확인 |

범례: 🔜 대기 · 🔄 진행중 · ✅완료일

**사용자 준비물 (블로커)**

| 시점 | 항목 | 상태 |
|------|------|------|
| ~~M0~~ | ~~깃허브 리포 생성~~ — `daehyub71/krx-stock-scoring` (**public**) | ✅ 2026-09-19 |
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
- [x] 리포·venv·`pyproject.toml`(ruff·mypy strict·pytest)·`.gitignore`·CI(`ci.yml`) — 2026-09-19 공개 리포 첫 푸시(8a0fc95), **CI 녹색 20초**. 푸시 전 보안 점검: 비밀값 대조 0건(작업 트리·커밋 이력), 공유 DB 원본 조회 출력은 `.gitignore`로 제외, 액션 SHA 고정, 권한 `contents: read`
- [x] kss 전용 Supabase: `store/schema.sql`(M1 객체 7개)·RLS·anon 정책 없음·`kss_batch`/`kss_reader` 롤·`scripts/apply_schema.py` — 2026-09-19 적용. 롤 비밀번호는 무작위 생성해 `.env`에만. 롤 로그인 실측: batch 쓰기 ✅·bus-mate 표 거부 ✅ / reader 조회 ✅·쓰기 거부 ✅·비게시 표 거부 ✅
- [x] `tests/test_schema.py` — 파일 수준 14개 + 실DB(`-m db`: RLS 전부 켜짐·anon/authenticated 정책·권한 0) 통과 — 2026-09-19. 저장 열 = 읽기 열 왕복은 writer가 생기는 M1에서
- [x] `rules/v0.toml` + `scoring/rules.py`(로드·정규 해시) — 테스트 12개: 항목 만점 17개·공통 90·축 35/35/7/13·뉴스 별도·신용 비활성·도달 가능 최대·해시 안정성 — 2026-09-19
- [x] `scoring/calendar.py` + `domain/resample.py` (TDD) — 테스트 18개(주중·월중·추석 금요일 휴장·연말 월말 휴장·자정·매일 재처리 시 기간당 1행·미래 입력 차단) + **실DB 대조(`-m db`): 2026-09-07 주 2,700+종목 불일치 0** — 2026-09-19
- [x] M0 사용자 확인 — 2026-09-19 「M0를 완료로 닫고 M1으로」

## M1 — 유니버스·기술 35·최소 저장

- [x] `sources/upstream.py` — 달력(일봉 ∩ 지수)·게이트 수·400거래일 일봉 서버 측 커서 적재(108만 행 13~31초)·신호 — 2026-09-19
- [x] 회귀 픽스처 — 유형별 25종목 × 400거래일(8,797행) `scripts/export_fixture.py` + 테스트 10개. **분류 단계의 미래 봉 누출 버그를 잡아 수정** — 2026-09-19
- [x] `domain/universe.py` — 우선주·스팩(alerts 2e0637e 이식)·금융계·리츠·신규·정지·드리프트·T일 봉 없음·`classification_unknown` — 2026-09-19
- [x] `domain/indicators.py` — SMA·EMA(SMA 시드)·RSI(Wilder)·MACD, 손계산 골든 8개 — 2026-09-19
- [x] `domain/technical.py` — 5항목, 손계산·경계(거래량비 정확히 1.5)·결측·정지·창=달력 테스트 23개 — 2026-09-19
- [x] `domain/aggregate.py` — 상태·관측률·축 추정·자격, RSI만 남은 종목 사례(SPEC §11.1) 포함 테스트 23개 — 2026-09-19
- [x] `store/writer.py` + `graph.py`(LangGraph 얇은 층)·`run.py --profile technical` — 청크 저장·publish 트랜잭션. 실DB: 게시 도중 실패 시 이전 포인터 유지 확인. 그래프 그림 PLAN §2.3 — 2026-09-19
- [x] 전 종목 실행 **42초**(T=9/18 게시, 2,771종목: scored 2,315 · provisional 80 · excluded 186 · insufficient 190, 랭킹 자격 2,149) · 하루 저장량 실측 ~6.9 MB(근거 5 · 점수 1.2 · 유니버스 0.5) → **보존 확정: 점수 252 · 근거 3 · 유니버스 20거래일**(사용자 결정, SPEC v2.4) · `kss_signal_cross` T=9/17 64건 연결 — 2026-09-19

## M2 — 기본 36·수급 13·규칙 확정

- [x] `sources/dart.py`·`corp.py` 이식(verify de7aee1, 테스트 12개) + `dart_fin.py` — 접수일 < T 필터·기간 전 호출 생략 — 2026-09-19
- [x] `domain/financial.py` — 누적치(`*_add_amount`)·기간 검증·연결 우선·평균 자본 (테스트 7개) — 2026-09-19
- [x] `domain/fundamental.py` 1차 — 6항목·업종 중앙값·불리 상태·stale (테스트 13개) + 전 종목 실측 분석(`docs/m2/`) — 2026-09-19
- [x] **v2.5·v2.6 반영**: 여섯 항목 직접 계산 — EPS = TTM ÷ (보통주+우선주 주식수), BPS = 자본총계 ÷ 같은 주식수, ROE = TTM ÷ 평균 자본. TTM 2,499 · 연환산 폴백 44 · 사업보고서 17 · 불가 25. 삼성전자 검산: TTM 150.7조 · EPS 22,669 · PER 11.47 · PBR 2.98 · ROE 29.7% — 2026-09-20
- [x] **v2.5 반영**: 뉴스 9점 공통 편입·환산 제거·음수 허용 — `rules`·`aggregate` 반영, 테스트 36개 — 2026-09-20
- [x] **v2.7 반영**: 배당수익률 4점(기본 36·총점 100) — KRX `DIV` 사용, `rules`·`fundamental`·스키마 범위(음수 허용) 반영·적용, 테스트 157개 — 2026-09-20
- [x] 흑자전환 3점 확정 (2026-09-20 사용자) — `rules` pending 해제
- [x] `partial` 프로필(기술 35 + 기본 36) 전 종목 실행·게시 — 2026-09-20. 141초(적재 25 · 재무 71 · 계산 7 · 저장 24), 근거 33,252행. scored 2,085 · 잠정 248 · 자료 부족 252 · 제외 186. 원점수 평균 28.5 / 최대 62
- [x] `kss_sector_stats` 저장 (업종·시장 중앙값·표본) — 스키마·writer·persist 연결 — 2026-09-20
- [ ] `kss_sector_stats`(우리 계산 PER·PBR 중앙값)·`kss_financial_versions`·`kss_corp_map` 저장
- [x] `domain/flow.py` — derived_zero·연속(결측일에서 끊김)·20/5일 누적·거래대금 규모 보정(참고값), 테스트 12개 — 2026-09-20
- [x] 공매도 — 20일 평균 비중, **시장별 경계 확정**(KOSPI <0.8/<3.0 · KOSDAQ <0.35/<2.0, 실측 분포 하위 25%·50%) — 2026-09-20
- [x] **실종목 20개 수작업 대조 — 109건 전부 일치**(`scripts/m2_crosscheck.py`: DART 재호출·SQL 재계산으로 독립 검증) — 2026-09-20
- [x] **규칙 고정** — 보완값 전부 확정(추세 기울기·상승일 우위·매물대 창/구간·기관 배분·공매도 경계·배당 단계·흑자전환·재무 노후도). 미확정은 뉴스뿐(D7)
- [x] `partial` 프로필(기술 35 + 기본 36 + 수급 13 = 84점) 전 종목 실행·게시 — 148초, scored 2,084 · 잠정 132 · 자료 부족 369 · 제외 186, 근거 41,565행 — 2026-09-20

## M3 — 공시·사전·뉴스 보조

- [ ] 날짜축 공시 수집 (Y·K·전 페이지·`last_reprt_at=N`) → `kss_disclosures`
- [ ] `domain/flags.py` 이식 → 사전 시드 v1 (`kss_lexicon`·`_versions`)
- [ ] `domain/disclosure.py` — 30일 창·정정/철회·fatal·`kss_risk_events` 해제 근거
- [ ] 두 사전 버전 공존·재현 테스트
- [ ] **뉴스 9점 공통(v2.5)** — `sources/naver.py` 전 종목 조회(약 2,585회/일), 최근 7일 관련 기사 3건 × ±3, 기사 없음 = `no_event`(0점)
- [ ] 뉴스 제목 300건 라벨링 → 사전 검증·확정 등급 게시 여부 결정 (D7)

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
| 2026-09-19 | CI 경고: 고정한 checkout v4·setup-python v5가 Node 20 기반(지원 종료) | 액션 메이저 버전이 낡음 | ✅ M1에서 checkout v7.0.1·setup-python v7.0.0(node24) SHA로 교체 |
| 2026-09-20 | 드리프트 종목이 26 → 2,470개로 급증해 기술 점수가 거의 다 결측 | 상위 `ksc_meta.drift.drifted`를 「낡은 종목」으로 잘못 해석. 상위는 그 목록을 **찾는 즉시 재백필**한다(`failed: 0` = 전부 고쳐짐) | `failed = 0`이면 보류하지 않도록 수정(SPEC §5.2 해석 주석 추가). 실패가 있으면 상위가 개수만 남겨 대상을 특정할 수 없어 목록 전체를 보수적으로 보류 — 실패 목록 저장은 상위에 요청할 사항 |
| 2026-09-20 | KRX PER·PBR이 실적과 크게 어긋남 | KRX 공표값이 직전 사업연도(2025 말) 기준 — 삼성전자 39.52 vs 반기 기준 6.39 | SPEC v2.5로 여섯 항목 직접 계산 전환 |
| 2026-09-19 | pykrx가 로그인 ID를 표준 출력에 찍음 | pykrx 1.2.x import 시 로그인 | `sources/krx.py`가 출력 가로채기. 공개 Actions 로그 대비 |
| 2026-09-19 | 픽스처 테스트가 미래 봉 누출 발견 | `compute_technical`이 분류 때 「마지막 봉 = T일 봉」 가정 → T 이후 봉이 섞이면 no_bar_on_t 오분류 | 분류 전에 `d ≤ T`로 자름. 실DB 적재는 이미 `d ≤ T`라 운영 영향 없었음 |
| 2026-09-19 | kss DB(bus-mate)에 기존 앱 표 7개 공존 | 사용자가 기존 프로젝트를 전용으로 지정 | 접두어 `kss_`로 충돌 없음, kss 표는 anon 회수·RLS. bus-mate 표는 kss_batch도 접근 불가 확인 |
| 2026-09-19 | 상위 DB 주소가 트랜잭션 풀러(6543) | prepared statement 불가 | `config.connect_upstream`에 `prepare_threshold=None` |
| 2026-09-19 | M0를 `ksv_reader`로 할 수 없음 | 그 롤은 `ksv_*`만 SELECT | 배치 자격증명 + `default_transaction_read_only=on` 세션으로 실측(서버가 쓰기 거부). 상위 전용 SELECT 롤은 D2 범위 |
