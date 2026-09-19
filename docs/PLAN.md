# PLAN.md — krx-stock-scoring

> **v0.3 (2026-09-19)** · 기준: `SPEC.md` **v2.3** · M0 실측: `docs/m0/M0_REPORT.md`
> SPEC과 어긋나면 SPEC이 기준이다. 이 문서는 **구현 순서·구조·테스트 전략**만 정한다.

---

## 1. 구현 원칙

1. **도메인은 순수 함수.** 점수·집계·자격 판정은 I/O를 모른다. DB·API는 `sources/`와 `store/`만 안다. 그래서 기본 테스트가 네트워크 없이 돈다(N23).
2. **규칙은 파일 하나가 기준.** `rules/v0.toml`의 배점·임계값을 코드가 읽는다. 코드에 상수 사본을 두지 않는다(F1).
3. **골든 테스트는 독립 계산값으로.** 손으로 계산한 기대값이나 실DB 표본을 스프레드시트로 대조한 값을 적는다. 규칙 파일을 읽어 같은 파일과 비교하는 테스트는 쓰지 않는다(SPEC §11.1).
4. **재사용은 이식(복사 + 테스트 동반).** verify·alerts를 import하지 않는다. 각 파일 머리에 출처 커밋을 적는다(SPEC V11·§15.2).
5. **단계형(SPEC §12.1).** M1~M3는 계산 정확성과 최소 저장까지만 한다. 운영 장치는 M4, 웹은 M5에 붙인다.
6. **LangGraph는 얇은 그래프 층.** `graph.py`·`state.py`만 LangGraph를 안다. 노드는 domain·store 함수를 부르는 얇은 포장이며, 상태에는 `run_id`·T·스냅샷 참조·게이트 결과·집계 요약만 싣는다(시세·근거 행은 싣지 않는다). 걷어내도 domain 테스트는 그대로 돈다(verify N4 방식, SPEC §10).

---

## 2. 아키텍처

![krx-stock-scoring 아키텍처](img/architecture.png)

> 원본: `docs/img/architecture.svg` · 다시 그리기: `python3 scripts/draw_architecture.py docs/img/architecture.svg` 실행 후 PNG로 렌더링. 구조가 바뀌면 그림도 함께 고친다.

### 2.1 디렉토리

```text
krx-stock-scoring/
  rules/v0.toml              배점·임계값·창·상태 규칙 (단일 기준)
  scoring/
    config.py                환경변수·연결 문자열 두 개(UPSTREAM_DATABASE_URL, KSS_DATABASE_URL)
    models.py                PartState, Part, ScoreRow, SourceCheck … (dataclass, 불변)
    rules.py                 TOML 로드 + 정규 해시(rules_hash)
    calendar.py              거래일 달력 — D 날짜 ∩ 지수 날짜, T 결정, 완성 주/월 판정
    domain/
      resample.py            D → W/M (기간 키·완성 여부·T 상한)
      indicators.py          SMA · RSI(Wilder) · MACD · 기울기
      technical.py           정배열·추세·거래량·매물대 근사·RSI/MACD → Part 5개
      fundamental.py         PER/PBR 상대비·영업이익률·성장률·ROE·부채비율 → Part 6개
      flow.py                derived_zero · 연속 순매수 · 5/20일 누적 규모 보정 → Part 2개
      shorting.py            20일 평균 비중·시장별 경계 → Part 1개
      disclosure.py          공시 사건 → 7점 (M3)
      universe.py            분류(우선주·스팩·금융·신규·정지)·제외 사유
      aggregate.py           관측률·axis_estimate·상태·rank_eligible·grade·pct_rank·passes_screen
      financial.py           (verify 이식) 계정 정규화·CFS/OFS
      flags.py               (verify 이식) 공시 제목 규칙 (M3)
      wording.py             (verify 이식) 금지어 (N1·N2)
    sources/
      upstream.py            ksc_*/ksa_* 일괄 SELECT (커서·청크, 행 수 검증)
      dart.py                corpCode · list.json · fnlttMultiAcnt (verify dart_fin 이식)
      krx.py                 pykrx PER/PBR 시장별 1회 (M2)
    store/
      schema.sql             kss 전용 DB 스키마 (멱등)
      writer.py              run 생성·청크 저장·publish 트랜잭션
    checks.py                출처별 품질 게이트 → kss_source_checks
    compute.py               전 종목 계산·요약·게시 전 검증 (I/O 없음)
    state.py                 그래프 상태 (TypedDict — 참조·요약만)
    graph.py                 LangGraph 노드·조건 분기 (유일하게 langgraph를 import)
    nodes.py                 노드 = domain·store 호출 포장
    run.py                   CLI: score --date T [--dry-run] [--tickers …] [--profile technical] → graph 실행
    archive.py               Parquet 내보내기·복원 검증 (M4)
  scripts/                   m0_probe.py · apply_schema.py · export_fixture.py · draw_architecture.py · export_graph.py · draw_graph.py
  tests/                     test_{module}_*.py, fixtures/ (실DB 표본 CSV)
  .github/workflows/         ci.yml (M0) · score.yml (M4)
```

### 2.2 계산 흐름 (한 번의 실행)

1. **T 결정**: 인자 없으면 `max(d)`(ksc_bars D) — 지수 달력에 같은 날이 있어야 한다. 자정을 넘어도 T는 바뀌지 않는다.
2. **run 생성**: `kss_runs`에 `created`로 먼저 기록한다(실패해도 남는다).
3. **품질 게이트**: 시장별 D 행 수·NULL·날짜 상한, 수급/공매도 T 행, `ksc_meta.update` 대조 → `kss_source_checks`. 시장 전체 장애면 `waiting_upstream`.
4. **입력 적재**: D는 **최근 400거래일**만 읽는다(SMA120 + 월봉 13개월 + 여유). 약 110만 행이며, 한 번에 받아 종목별로 나눈다.
5. **계산**: 종목별로 domain 함수를 불러 `Part` 17개(뉴스 1·신용 1은 상태만)를 만든다 → aggregate.
6. **검증**: 종목 수 합계 = 유니버스, 상태별 합, 점수 범위, 규칙 해시가 run과 일치하는지 확인.
7. **저장·게시**: 500행 청크로 저장한 뒤 한 트랜잭션에서 `kss_publications` 포인터를 바꾼다.

### 2.3 배치 그래프 (LangGraph)

![채점 배치 그래프](img/graph.png)

> 정본은 코드(`scoring/graph.py`)다. Mermaid 도식 `docs/GRAPH.md`는 `scripts/export_graph.py`가 컴파일된 그래프에서 생성하고, 코드와 어긋나면 `tests/test_graph.py`가 실패한다. 위 그림은 `scripts/draw_graph.py`가 **같은 그래프의 노드·간선**을 읽어 그린다(설명 없는 노드가 생기면 그리기가 실패한다).

**왜 얇은 층인가.** 배치는 LLM 없이 한 방향으로 흐르는 계산이다. 그래서 LangGraph에는 **순서와 분기**만 맡긴다. 계산 규칙은 `domain/`, 저장·게시는 `store/`에 있고 LangGraph 없이 테스트된다. `graph.py`만 `langgraph`를 import한다(verify N4 방식, SPEC §10 v2.3).

| 노드 | 하는 일 | 상위 읽기 | kss 쓰기 | `kss_runs.status` |
|---|---|:-:|:-:|---|
| `calendar` | 거래일 달력(일봉 날짜 ∩ 지수 날짜)과 T 결정. 실행 시각(자정 이후 등)과 무관 | ✅ | | |
| `create_run` | 실행 행을 **먼저** 기록 — 이후 어디서 실패해도 흔적이 남는다 | | ✅ | `created → checking` |
| `gate` | 시장별 T일 일봉 저장 커버리지 ≥ 99%, `ksc_meta` 갱신일·달력 불일치 확인 → `kss_source_checks` | ✅ | ✅ | |
| `wait` | (게이트 미달) 게시하지 않고 끝낸다. M4 복구 잡이 다시 집는다 | | ✅ | `waiting_upstream` |
| `load` | 최근 400거래일 일봉을 서버 측 커서로 적재(≈108만 행, 약 31초), 종목·드리프트 메타 | ✅ | | `computing` |
| `compute` | 분류 → 기술 5항목 → 집계. 순수 함수, I/O 없음(2,771종목 약 7초) | | | |
| `validate` | 종목 수 = 유니버스, 종목당 항목 5개, scored ⇒ 관측률 1, 비완전 관측의 랭킹 자격 금지 | | | `validating` |
| `persist` | 유니버스·점수·근거를 500행 청크로 저장한 뒤 행 수를 다시 세어 대조 | | ✅ | |
| `publish` | advisory lock → 게시 포인터 교체 → 이력, **한 트랜잭션** | | ✅ | `published` |
| `cross` | 같은 기준일(d = T) alerts 신호와 점수를 연결, `available_at_signal`·주봉 품질 표지 | ✅ | ✅ | |

**분기는 하나다.** `gate` 뒤 조건 간선(`route_after_gate`)이 통과면 `load`로, 미달이면 `wait`로 보낸다. 시장 전체 장애일 때 이전 게시본을 유지하려는 것이다(SPEC §6.3). 개별 종목 누락은 분기하지 않고 종목 상태(`insufficient_data`·위험 표지)로 드러난다.

**상태와 문맥을 나눈다.**
- `RunState`(그래프 상태): `profile · requested_t · trigger · dry_run · t · run_id · gate_ok · status · stats` — 요약만.
- `RunContext`(실행 문맥): DB 연결, 규칙, 스냅샷(일봉 108만 행), 계산 결과. 그래프를 만들 때 노드 클로저에 묶는다 → 상태·체크포인트가 대량 데이터를 복제하지 않는다(SPEC §10).

**실패와 드라이런.**
- 어느 노드에서든 예외가 나면 `run.py`가 실행을 `failed`로 기록하고 0이 아닌 코드로 끝난다. 게시 포인터는 `publish` 트랜잭션 안에서만 바뀌므로 **이전 게시본이 그대로 보인다** — `tests/test_writer_db.py`가 실DB에서 확인한다.
- `--dry-run`이면 `create_run · persist · publish · cross`가 kss에 쓰지 않는다. 계산과 검증은 똑같이 돈다.

**M4에서 더해질 것.** `repository_dispatch` 트리거·복구 큐·heartbeat·입력 스냅샷/아카이브. 노드를 추가하면 `scripts/export_graph.py`와 `scripts/draw_graph.py`(NOTES)를 함께 고친다.

다시 그리기:

```bash
venv/bin/python scripts/export_graph.py                     # docs/GRAPH.md (Mermaid)
venv/bin/python scripts/draw_graph.py docs/img/graph.svg    # 그림 원본
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars \
  --force-device-scale-factor=2 --window-size=1500,1270 \
  --screenshot="$PWD/docs/img/graph.png" "file://$PWD/docs/img/graph.svg"
```

---

## 3. 데이터 모델 — 마일스톤별 도입

SPEC §7.2의 객체를 **필요한 마일스톤에** 만든다. 열 이름은 M0 스키마 작업에서 확정하고, 저장하는 열과 읽는 열이 같은지 왕복 테스트로 확인한다.

| 객체 | 도입 | 비고 |
|---|---|---|
| `kss_runs` · `kss_scores` · `kss_score_parts` · `kss_publications` · `kss_publication_history` · `kss_source_checks` · `kss_universe_snapshots` | **M1** | `kss_score_parts`는 정규 테이블(run_id, ticker, item). **3거래일** 뒤 Parquet로 옮기고 삭제(M4) |
| `kss_sector_stats` · `kss_financial_versions` · `kss_corp_map` | M2 | |
| `kss_disclosures` · `kss_risk_events` · `kss_lexicon` · `kss_lexicon_versions` | M3 | |
| `kss_input_snapshots` · `kss_signal_cross` | M1(cross는 기술 전용 `partial_technical`) · M4(snapshot) | |
| `kss_news_observations` | M3 이후(선택) | |

용량 예산(SPEC v2.4 §7.4, M1 실측 후 확정): **점수 252 · 근거 3 · 유니버스 20거래일**, 경고 400 MB · 신규 상세 저장 중단 450 MB. M1 실측 하루 ~6.9 MB(점수 1.2 · 근거 5 · 유니버스 0.5, 기술 5항목). M2 `common` 첫 실행에서 행 크기를 다시 재고, 넘치면 점수 행 중복 열 축소·`technical` 프로필 중단.

---

## 4. 재사용 목록 (이식 대상)

| 원본 | 대상 | 무엇을 | 언제 |
|---|---|---|---|
| verify `dart_fin.py`(155) | `sources/dart.py` | 보고서 하강 탐색, 15개 청크, 013 처리 | M2 |
| verify `financial.py`(245) | `domain/financial.py` | 계정 정규화, CFS 우선·혼합 금지, 금융사 매출 부재 기록. **비율·점수는 새로 쓴다** | M2 |
| verify `corp.py`(88) | `sources/dart.py` | corpCode.xml 파싱, XML 오류 본문 판별 | M2 |
| verify `flags.py`(212) + 테스트 표본 | `domain/flags.py` | 공시 제목 정규화·규칙표·자회사/리츠 강등 → 사전 시드 | M3 |
| verify `wording.py`(143) | `domain/wording.py` | N1·N2 금지어 | M1(표시 문구 검사) |
| alerts `universe.py` | `domain/universe.py` | `is_spac`·`is_preferred` 보조 판별 | M1 |
| charts `resample.py` | 참고만 | 집계 규칙. **기간 키·완성·T 상한은 새 계약으로 다시 쓴다** | M1 |
| verify `scripts/deploy_web.sh` · 웹 DB 접근 패턴 | `web/` | preview + 보호 별칭 | M5 |

이식할 때 원본 커밋 SHA를 파일 머리와 `docs/TASKS.md` 이식 기록에 적는다.

---

## 5. 마일스톤

각 마일스톤의 완료 조건에는 **ruff · mypy(strict) · pytest 전부 통과**가 포함된다. 마일스톤을 닫으면 사용자 확인을 받는다.

### M0 — 선행 실측·계약·뼈대

| 범위 | 완료 조건 |
|---|---|
| ✅ 읽기 전용 실측(용량·시점·W/M·완전성·수급 NULL·DART 표본) | `docs/m0/M0_REPORT.md` |
| 리포(`daehyub71/krx-stock-scoring`)·venv·`pyproject`(ruff·mypy strict·pytest)·CI | 첫 푸시에서 CI 녹색 |
| **kss 전용 Supabase 프로젝트**(사용자 생성)·`store/schema.sql`의 M1 객체·RLS·anon 정책 없음·`kss_batch`/`kss_reader` 롤 | 스키마 적용, `test_schema.py`(anon 차단·정책 목록) 통과 |
| `rules/v0.toml` — SPEC §4.5 표 전체 + 상태·관측률 하한 | `test_rules.py`: 배점 합 90/8/2, 해시 안정성 |
| `scoring/calendar.py` + `domain/resample.py` (TDD) | 회귀: 주중·월중·금요일 휴장·월말 휴장·자정 이동, **같은 주 D를 여러 번 처리해도 기간당 1행**, M0에서 확인한 직전 완성 주 2,766종목의 저장 W 최신 행과 일치 |

### M1 — 유니버스·기술 35·최소 저장·기술 대조

- `domain/universe.py`: 우선주·스팩·금융계(262)·신규(700행 미만 317)·정지(v=0) 분류, `classification_unknown`. 드리프트 종목(`ksc_meta.drift`)은 기술 `missing(stale)`.
- `domain/indicators.py`·`technical.py`: §4.5 기술 5항목. 워밍업 부족은 `missing(insufficient_history)`. 매물대는 60일·20구간·(h+l+c)/3(보완값). 거래정지 의심일은 거래량비 계산에서 제외.
- `aggregate.py`: 상태 4종, 관측률, `passes_screen`. `technical` 프로필은 `experimental=true`.
- `store/writer.py`: run·청크 저장·publish 트랜잭션. `graph.py`(gate → snapshot → compute → validate → publish, 게이트 미달은 `waiting_upstream` 종료 분기) + `run.py score --profile technical`.
- `kss_signal_cross`(partial_technical): `signal.d = data_date`로 연결.
- **완료 조건**: 전 종목 상태 합계 = 유니버스 · 미래 입력 차단 · 중복 · 워밍업 · 정지 회귀 통과 · 전 종목 실행 **3분 이내**(적재 포함) · **하루 저장량 실측 → 보존 기간 확정**.

### M2 — 기본 35·수급 13·규칙 확정

- `sources/krx.py`: pykrx PER/PBR 시장별(KRX 로그인 필요 — charts와 같은 방식). `kss_sector_stats`: 업종 유효 양수 표본 중앙값, 5 미만이면 시장 폴백.
- `sources/dart.py` + `domain/fundamental.py`: 누적 기간 매출·영업이익 YoY(4+3), 영업이익률, ROE(연간·평균 자본), 부채비율. `kss_financial_versions`로 접수번호별 불변 저장. 청크 15개로 시작해 증량을 실측한다.
- `domain/flow.py`: `derived_zero`(SPEC §5.4), 인접 거래일이 모두 있어야 인정하는 연속 순매수, 거래대금으로 규모 보정한 5/20일 누적.
- `domain/shorting.py`: 20거래일 산술평균, **시장별** 경계 확정(M0 분포: KOSPI p50 1.3~3.1% · KOSDAQ 0.5~1.5%).
- 금융·보험·리츠는 `special_sector`로 분리하고 일반 랭킹에서 뺀다(D5는 보류).
- **완료 조건**: 실종목 **20개 수작업 대조**(비12월 결산·CFS/OFS·음수 분모·정정 전후 포함) · §4.5 보완값 전부 확정 → `rules/v0.toml` 고정 · 전 종목 `common` 프로필 실행.

### M3 — 공시·사전·뉴스 보조

- `sources/dart.py` 날짜축 `list.json`(Y·K, `last_reprt_at=N`, 전 페이지): **하루 4~5회**(M0 실측).
- `domain/flags.py` 이식 → `kss_lexicon` 시드 v1. `disclosure.py`: 30일 창, 정정/철회 관계, fatal은 0으로 덮어쓰기, `kss_risk_events`의 해제 근거.
- 사전 버전 스냅샷(불변 해시). 뉴스는 `passes_screen` 종목만 조회하고, 사전을 검증하기 전에는 점수를 null로 둔다(목록만 저장).
- **완료 조건**: 중복·철회·해제 사례 테스트 · 사전 1항목 변경 → 두 버전 공존·각각 재현 · 뉴스 제목 300건 라벨링으로 사용 여부 결정(D7).

### M4 — 운영·게시·복구

- `score.yml`: `repository_dispatch`(charts 완료 이벤트, 주) + 23:37 KST 평일 cron(예비) + 07:17 복구 잡. `concurrency` 그룹으로 T별 중복을 막는다.
- **상위 변경(별도 커밋, 사용자 승인)**: charts `daily.yml` 끝에 dispatch 단계를 추가한다(payload: run_id·sha·T). 토큰은 대상 리포 1개 한정 fine-grained PAT.
- 복구 큐(최근 5거래일), heartbeat, `published_degraded`, 입력 스냅샷, Parquet 아카이브(깃허브 Release 자산 1안) + 복원 검증 + 보존 삭제.
- **완료 조건**: 5거래일 무인 관찰(출처별 누락률·게시 일관성 포함) + 지연·부분 실패·중복 이벤트·중간 저장 실패 모의 테스트.

### M5 — 웹 (DESIGN 합의 선행)

- `/`·`/stock/[ticker]`·`/lexicon`·`/cross`·`/status`. Claude Design 캔버스 시안을 합의한 뒤 구현한다.
- `kss_reader`(게시 뷰 SELECT)·`kss_editor`(사전 편집 절차) 서버 사이드. Vercel All Deployments 보호를 실제로 설정해 보고, 불가하면 verify 방식을 쓴다.
- **완료 조건**: lint·test·build · 비로그인 상태로 모든 URL·API 차단 확인 · 번들 grep(비밀값 없음) · 배포 전 보안 점검.

### M6 — 점수 검증·출시 판정 / M7 — MCP·Skill

SPEC §11.2·§12를 따른다. **수급·공매도는 보존 구간이 약 2개월뿐**이라(M0) 소급 검증은 기술·기본 축 위주로 하고, 나머지는 운영하면서 관측한다.

---

## 6. 테스트 전략

| 층 | 방식 |
|---|---|
| domain | 손계산 골든 값 + 경계값(임계값 ±1틱, 동점, 0, NULL) — `test_technical_*`, `test_flow_derived_zero_*` … |
| 회귀 픽스처 | M0 실DB 표본(종목 20~30개 × 400일)을 CSV로 `tests/fixtures/`에 고정한다. 공개 시장 데이터라 커밋 가능 |
| sources | 응답 픽스처(DART JSON·XML 오류 본문·013), 네트워크 없음 |
| store | 스키마 왕복(저장 열 = 읽기 열), publish 원자성(중간 예외 → 이전 게시본 유지) — `-m db` 마커, 로컬 전용 |
| live | 실DB·실API 범위 제한 호출 — `-m live`, CI 기본 제외 |
| 문구 | 화면·근거 문자열 전부 `wording.first_violation` 통과 |

---

## 7. 의존성 (추가 전 사용자 확인 — 워크스페이스 규칙)

| 패키지 | 용도 | 시점 | 비고 |
|---|---|---|---|
| `psycopg[binary]>=3.2` | 두 DB 연결 | M0 | verify와 같다 |
| `langgraph>=1.2,<2` | 배치 그래프 층 | M1 | verify와 같은 범위. `graph.py`만 import |
| `certifi` | macOS TLS | M0 | verify와 같다 |
| `pykrx` | PER/PBR | M2 | charts와 같은 버전으로 고정 |
| `pyarrow` | Parquet 아카이브 | M4 | 그때 다시 확인 |
| dev: `pytest`·`ruff`·`mypy` | 검증 | M0 | |

규칙 파일은 **TOML(`tomllib`, 표준 라이브러리)** 로 둔다. SPEC §4.2의 `rules/<version>.yaml`을 `.toml`로 바꾸는 것이며, PyYAML 의존성을 피하려는 것이다(아래 결정 대기).

---

## 8. 리스크 (PLAN 수준)

| 리스크 | 대응 |
|---|---|
| 공유 DB 한도 초과로 상위가 읽기 전용으로 전환됨 → 상위 수집 중단 | scoring은 `waiting_upstream`으로 버틴다. 공유 DB 정리는 별도 조치(사용자 보고) |
| 400거래일 D 적재가 느림(Supabase 풀러) | M1에서 측정하고, 느리면 종목 청크 병렬 또는 필요한 열만 읽기 |
| pykrx KRX 로그인 차단 | charts의 진단 경로를 재사용한다. 실패하면 PER/PBR은 `source_error`로 두고 degraded 게시 |
| DART 일일 한도(20,000) | 재무는 분기 초에 몰린다 — 청크·캐시 실측(M2) |
| 전용 무료 프로젝트 7일 비활성 일시정지 | 매일 배치가 쓰므로 해당 없음. 장기 휴장 때만 주의 |

---

## 9. 사용자 준비물

| 시점 | 항목 |
|---|---|
| M0 | 깃허브 리포 생성(public/private 결정) · **kss 전용 Supabase 무료 프로젝트 생성** 후 연결 문자열을 `.env`에 · 의존성 승인 |
| M2 | KRX 로그인 계정(charts와 같은 값 재사용 여부) |
| M4 | fine-grained PAT(대상 `krx-stock-scoring`, Contents write) → charts Secrets · charts `daily.yml` 변경 승인 |
| M5 | DESIGN 시안 합의 · Vercel 프로젝트 |

---

## 10. 변경 이력

| 판 | 일자 | 내용 |
|---|---|---|
| v0.3 | 2026-09-19 | §2.3 배치 그래프(LangGraph) 그림·노드 설명 추가, 디렉토리에 compute.py·스크립트 반영 |
| v0.2 | 2026-09-19 | 아키텍처를 이미지로(§2), LangGraph 얇은 그래프 층 채택(원칙 6·§2.1·§7) |
| v0.1 | 2026-09-19 | 초안 — SPEC v2.2, M0 실측 반영. 단계형 구현, 전용 DB, derived_zero, M4 dispatch |
