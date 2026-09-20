-- krx-stock-scoring — kss 전용 Supabase 스키마 (M1 객체, PLAN §3). 멱등 — 재실행해도 안전하다.
--
-- 원칙 (SPEC §7.2 · §10 N11·N22)
--   · 실행마다 불변: 점수는 (run_id, ticker)로 쌓고 덮어쓰지 않는다. 재평가는 새 run_id.
--   · 게시는 포인터: kss_publications (data_date, profile) → run_id 를 한 트랜잭션에서 바꾼다.
--   · anon·authenticated 정책 없음. 모든 표에 RLS. 접근은 전용 롤만:
--       kss_batch  — 배치 쓰기 (BYPASSRLS 없이 정책으로)
--       kss_reader — 웹 서버 사이드 읽기. **게시된 run만** 보인다
--   · 롤 비밀번호는 scripts/apply_schema.py가 환경변수에서 넣는다 (이 파일에 두지 않는다).

-- ───────────────────────── 롤 ─────────────────────────
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'kss_batch') then
    create role kss_batch login noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'kss_reader') then
    create role kss_reader login noinherit;
  end if;
end
$$;

grant usage on schema public to kss_batch, kss_reader;

-- ───────────────────────── 실행 ─────────────────────────
create table if not exists kss_runs (
  run_id                 uuid primary key default gen_random_uuid(),
  data_date              date not null,                 -- 평가 거래일 T
  profile                text not null,                 -- technical / common …
  status                 text not null default 'created',
  trigger                text not null default 'manual', -- dispatch / cron / recovery / manual
  source_run_id          text,                          -- 상위 수집 실행 ID
  code_sha               text,
  rules_version          text not null,
  rules_hash             text not null,
  lexicon_hash           text,
  dependency_hash        text,
  information_cutoff_at  timestamptz,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  heartbeat_at           timestamptz,
  finished_at            timestamptz,
  stats                  jsonb not null default '{}'::jsonb,
  error                  text,
  constraint kss_runs_status check (status in (
    'created', 'checking', 'computing', 'validating',
    'published', 'published_degraded',
    'waiting_upstream', 'no_trading', 'failed', 'cancelled'))
);
create index if not exists kss_runs_date on kss_runs (data_date desc, profile);

-- 출처·시장별 품질 기록 — 부분 실패를 success에 숨기지 않는다 (SPEC §6.3)
create table if not exists kss_source_checks (
  run_id           uuid not null references kss_runs(run_id) on delete cascade,
  source           text not null,                  -- bars_d / flows / shorting / universe …
  market           text not null,                  -- KOSPI / KOSDAQ / ALL
  expected_count   integer,
  stored_count     integer,
  valid_count      integer,
  null_count       integer,
  duplicate_count  integer,
  min_date         date,
  max_date         date,
  coverage         numeric(6, 4),
  observed_at      timestamptz not null default now(),
  status           text not null,
  reason           text,
  primary key (run_id, source, market),
  constraint kss_source_checks_status check (status in ('ok', 'degraded', 'failed', 'skipped'))
);

-- 당일 유니버스·분류 스냅샷 (snapshot_id = run_id, M1)
create table if not exists kss_universe_snapshots (
  snapshot_id     uuid not null references kss_runs(run_id) on delete cascade,
  ticker          text not null,
  name            text not null,
  market          text not null,
  sector          text not null default '',
  classification  text[] not null default '{}',   -- preferred / spac / special_sector / new_listing / suspended / classification_unknown …
  excluded_reason text,
  source          text not null default 'ksc_tickers',
  primary key (snapshot_id, ticker),
  constraint kss_universe_ticker check (ticker ~ '^[0-9A-Z]{6}$')
);

-- ───────────────────────── 점수 ─────────────────────────
create table if not exists kss_scores (
  run_id                 uuid not null references kss_runs(run_id) on delete cascade,
  ticker                 text not null,
  data_date              date not null,
  profile                text not null,
  name                   text not null,            -- 웹이 상위를 조인하지 않도록 게시 시점 값 (SPEC §7.2)
  market                 text not null,
  sector                 text not null default '',
  status                 text not null,
  profile_max            numeric(6, 2),            -- 프로필 만점 (기술 35 · 기술+기본 71 · 공통 100)
  raw_total              numeric(6, 2),            -- 관측 항목 점수의 합 (v2.8: 모든 상태)
  total                  numeric(6, 2),            -- 완전 관측일 때만 (100 환산)
  estimated_total        numeric(6, 2),            -- 부분 관측 추정치 (provisional)
  grade                  text,
  coverage               numeric(5, 4),
  axis                   jsonb not null default '{}'::jsonb,  -- 축별 points / available_max / estimate
  availability_signature text,
  rank_eligible          boolean not null default false,
  risk_flags             text[] not null default '{}',
  passes_screen          boolean,                  -- 입력이 모두 유효할 때만 true/false
  primary key (run_id, ticker),
  constraint kss_scores_ticker check (ticker ~ '^[0-9A-Z]{6}$'),
  constraint kss_scores_status check (status in ('scored', 'provisional', 'insufficient_data', 'excluded')),
  -- 확정 total·grade는 완전 관측(scored)에만 (SPEC §4.3)
  constraint kss_scores_total_only_scored check (total is null or status = 'scored'),
  constraint kss_scores_grade_only_scored check (grade is null or status = 'scored'),
  constraint kss_scores_grade_values check (grade is null or grade in ('A', 'B', 'C', 'D')),
  -- v2.7 — 총점 100점 척도(환산 없음). 뉴스(−9~+9) 때문에 음수 총점이 가능하다
  constraint kss_scores_ranges check (
    (total is null or total between -100 and 100) and
    (estimated_total is null or estimated_total between -100 and 100) and
    (coverage is null or coverage between 0 and 1))
);
create index if not exists kss_scores_ticker on kss_scores (ticker, data_date desc);

-- 항목 근거 — 약 10거래일 보관 후 Parquet로 옮긴다 (M4)
create table if not exists kss_score_parts (
  run_id              uuid not null,
  ticker              text not null,
  item                text not null,               -- rules 항목 ID (tech.alignment …)
  axis                text not null,
  points              numeric(6, 2),
  max                 numeric(6, 2) not null,
  state               text not null,
  missing_reason      text,
  actual              jsonb not null default '{}'::jsonb,  -- 재계산 가능한 실제 특징값
  unit                text,
  period              text,
  note                text,
  source_snapshot_id  text,
  primary key (run_id, ticker, item),
  foreign key (run_id, ticker) references kss_scores(run_id, ticker) on delete cascade,
  constraint kss_parts_state check (state in ('observed', 'no_event', 'adverse_defined', 'missing', 'not_applicable')),
  constraint kss_parts_missing_null check ((state = 'missing') = (points is null) or state = 'not_applicable'),
  constraint kss_parts_reason_only_missing check (missing_reason is null or state = 'missing'),
  -- 뉴스 항목만 음수를 낸다 (−max ~ max)
  constraint kss_parts_points_range check (points is null or points between -max and max)
);

-- ───────────────────────── 게시 ─────────────────────────
create table if not exists kss_publications (
  data_date     date not null,
  profile       text not null,
  run_id        uuid not null references kss_runs(run_id),
  status        text not null,
  published_at  timestamptz not null default now(),
  primary key (data_date, profile),
  constraint kss_publications_status check (status in ('published', 'published_degraded'))
);

create table if not exists kss_publication_history (
  id           bigint generated always as identity primary key,
  data_date    date not null,
  profile      text not null,
  prev_run_id  uuid,
  new_run_id   uuid not null,
  changed_at   timestamptz not null default now(),
  reason       text not null
);

-- ───────────────────────── 기존 DB 이행 (v2.7~v2.8) ─────────────────────────
alter table kss_scores add column if not exists profile_max numeric(6, 2);
-- `create table if not exists`는 이미 있는 표의 제약을 바꾸지 않는다 → 명시적으로 다시 건다.
alter table kss_scores drop constraint if exists kss_scores_ranges;
alter table kss_scores add constraint kss_scores_ranges check (
  (total is null or total between -100 and 100) and
  (estimated_total is null or estimated_total between -100 and 100) and
  (coverage is null or coverage between 0 and 1));
alter table kss_score_parts drop constraint if exists kss_parts_points_range;
alter table kss_score_parts add constraint kss_parts_points_range check (
  points is null or points between -max and max);

-- 업종·시장 중앙값 (M2) — 우리가 계산한 PER·PBR의 유효 양수 표본 중앙값
create table if not exists kss_sector_stats (
  run_id    uuid not null references kss_runs(run_id) on delete cascade,
  market    text not null,
  sector    text not null,
  metric    text not null,                 -- per / pbr
  median    numeric(12, 4) not null,
  samples   integer not null,
  primary key (run_id, market, sector, metric),
  constraint kss_sector_stats_metric check (metric in ('per', 'pbr'))
);

-- 재무 원본 불변 버전 (SPEC §7.2) — 접수번호·내용 해시로 정정 전후를 모두 보존한다.
-- 같은 보고서를 다시 받아도 내용이 같으면 행이 늘지 않는다(on conflict do nothing).
create table if not exists kss_financial_versions (
  corp_code     text not null,
  bsns_year     text not null,
  reprt_code    text not null,
  rcept_no      text not null,
  content_hash  text not null,
  basis         text,                      -- CFS / OFS
  period_end    date,
  rcept_date    date,
  items         jsonb not null,            -- 점수에 쓰는 계정만 (매출·영업이익·순이익·자본·부채)
  observed_at   timestamptz not null default now(),
  primary key (corp_code, bsns_year, reprt_code, rcept_no, content_hash)
);

-- 종목 ↔ DART 법인 매핑 버전 (SPEC §7.2) — corpCode.xml 내용 해시가 버전이다
create table if not exists kss_corp_map (
  mapping_version text not null,
  stock_code      text not null,
  corp_code       text not null,
  observed_at     timestamptz not null default now(),
  primary key (mapping_version, stock_code)
);

-- ───────────────────────── alerts 대조 ─────────────────────────
-- signal.d = score.data_date 로 연결 (SPEC §8). M1은 기술 전용(partial_technical).
-- available_at_signal: 점수 게시 시각 ≤ 신호 생성 시각 — 그때 실제로 볼 수 있었던 점수인가
create table if not exists kss_signal_cross (
  signal_data_date       date not null,
  ticker                 text not null,
  strategy               text not null,
  score_run_id           uuid not null references kss_runs(run_id) on delete cascade,
  comparison_mode        text not null,               -- partial_technical / same_data_date
  signal_created_at      timestamptz,
  score_published_at     timestamptz,
  available_at_signal    boolean,
  rank_no                integer,
  suppressed             boolean,
  score_status           text,
  axis_points            numeric(6, 2),
  passes_screen          boolean,
  upstream_weekly_quality_unverified boolean not null default false,  -- 상위 W/M 저장 행 의존 전략
  note                   text,
  primary key (signal_data_date, ticker, strategy, score_run_id, comparison_mode),
  constraint kss_signal_cross_mode check (comparison_mode in ('partial_technical', 'same_data_date'))
);

-- ───────────────────────── 권한 ─────────────────────────
-- Supabase 기본 권한이 public 스키마 새 표를 anon·authenticated에 열어 둔다 → 표마다 회수한다.
revoke all on kss_runs, kss_source_checks, kss_universe_snapshots, kss_scores,
              kss_score_parts, kss_publications, kss_publication_history, kss_signal_cross,
              kss_sector_stats, kss_financial_versions, kss_corp_map
  from anon, authenticated;

alter table kss_runs                enable row level security;
alter table kss_source_checks       enable row level security;
alter table kss_universe_snapshots  enable row level security;
alter table kss_scores              enable row level security;
alter table kss_score_parts         enable row level security;
alter table kss_publications        enable row level security;
alter table kss_publication_history enable row level security;
alter table kss_signal_cross        enable row level security;
alter table kss_sector_stats        enable row level security;
alter table kss_financial_versions  enable row level security;
alter table kss_corp_map            enable row level security;

-- kss_batch: 필요한 쓰기만. 삭제는 근거 보존 정리(parts)에만
grant select, insert, update on kss_runs, kss_source_checks, kss_universe_snapshots,
                               kss_scores, kss_score_parts, kss_publications,
                               kss_publication_history, kss_signal_cross,
                               kss_sector_stats, kss_financial_versions,
                               kss_corp_map to kss_batch;
grant delete on kss_score_parts, kss_publications, kss_signal_cross to kss_batch;

drop policy if exists kss_runs_batch on kss_runs;
drop policy if exists kss_source_checks_batch on kss_source_checks;
drop policy if exists kss_universe_batch on kss_universe_snapshots;
drop policy if exists kss_scores_batch on kss_scores;
drop policy if exists kss_parts_batch on kss_score_parts;
drop policy if exists kss_publications_batch on kss_publications;
drop policy if exists kss_pub_history_batch on kss_publication_history;
drop policy if exists kss_cross_batch on kss_signal_cross;
drop policy if exists kss_sector_stats_batch on kss_sector_stats;
drop policy if exists kss_fin_versions_batch on kss_financial_versions;
drop policy if exists kss_corp_map_batch on kss_corp_map;
create policy kss_runs_batch          on kss_runs                for all to kss_batch using (true) with check (true);
create policy kss_source_checks_batch on kss_source_checks       for all to kss_batch using (true) with check (true);
create policy kss_universe_batch      on kss_universe_snapshots  for all to kss_batch using (true) with check (true);
create policy kss_scores_batch        on kss_scores              for all to kss_batch using (true) with check (true);
create policy kss_parts_batch         on kss_score_parts         for all to kss_batch using (true) with check (true);
create policy kss_publications_batch  on kss_publications        for all to kss_batch using (true) with check (true);
create policy kss_pub_history_batch   on kss_publication_history for all to kss_batch using (true) with check (true);
create policy kss_cross_batch         on kss_signal_cross        for all to kss_batch using (true) with check (true);
create policy kss_sector_stats_batch  on kss_sector_stats        for all to kss_batch using (true) with check (true);
create policy kss_fin_versions_batch  on kss_financial_versions  for all to kss_batch using (true) with check (true);
create policy kss_corp_map_batch      on kss_corp_map            for all to kss_batch using (true) with check (true);

-- kss_reader: SELECT만, 그리고 게시된 run의 행만 (SPEC N11)
grant select on kss_runs, kss_source_checks, kss_scores, kss_score_parts,
                kss_publications, kss_signal_cross, kss_sector_stats to kss_reader;

drop policy if exists kss_publications_reader on kss_publications;
drop policy if exists kss_runs_reader on kss_runs;
drop policy if exists kss_source_checks_reader on kss_source_checks;
drop policy if exists kss_scores_reader on kss_scores;
drop policy if exists kss_parts_reader on kss_score_parts;
drop policy if exists kss_cross_reader on kss_signal_cross;
drop policy if exists kss_sector_stats_reader on kss_sector_stats;
create policy kss_publications_reader on kss_publications for select to kss_reader using (true);
create policy kss_runs_reader on kss_runs for select to kss_reader
  using (exists (select 1 from kss_publications p where p.run_id = kss_runs.run_id));
create policy kss_source_checks_reader on kss_source_checks for select to kss_reader
  using (exists (select 1 from kss_publications p where p.run_id = kss_source_checks.run_id));
create policy kss_scores_reader on kss_scores for select to kss_reader
  using (exists (select 1 from kss_publications p where p.run_id = kss_scores.run_id));
create policy kss_parts_reader on kss_score_parts for select to kss_reader
  using (exists (select 1 from kss_publications p where p.run_id = kss_score_parts.run_id));
create policy kss_cross_reader on kss_signal_cross for select to kss_reader
  using (exists (select 1 from kss_publications p where p.run_id = kss_signal_cross.score_run_id));
create policy kss_sector_stats_reader on kss_sector_stats for select to kss_reader
  using (exists (select 1 from kss_publications p where p.run_id = kss_sector_stats.run_id));
