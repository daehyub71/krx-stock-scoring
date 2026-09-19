"""M0 선행 실측 — 상위 DB 읽기 전용 점검 (SPEC §2.2).

세션을 DB 차원에서 READ ONLY로 강제한다. 쓰기 문장은 서버가 거부한다.
접속 문자열은 인자로 받은 .env 파일의 SUPABASE_DATABASE_URL을 쓰며 출력하지 않는다.

사용:
    python scripts/m0_probe.py ../krx-signal-verify/.env > m0_probe.txt
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import psycopg

# (제목, SQL) — 순서는 SPEC §2.2 우선순위: 용량 → W/M 잔류 → 갈래별 완전성
QUERIES: list[tuple[str, str]] = [
    ("server", "select version(), current_setting('TimeZone') as tz, now() as now_utc"),
    ("db_size", "select pg_size_pretty(pg_database_size(current_database())) as db_size,"
                " pg_database_size(current_database()) as bytes"),
    ("table_sizes_top40", """
        select c.relname, c.reltuples::bigint as est_rows,
               pg_size_pretty(pg_table_size(c.oid)) as table_sz,
               pg_size_pretty(pg_indexes_size(c.oid)) as index_sz,
               pg_total_relation_size(c.oid) as total_bytes
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where c.relkind = 'r' and n.nspname not in ('pg_catalog','information_schema')
        order by pg_total_relation_size(c.oid) desc limit 40"""),
    ("size_by_prefix", """
        select coalesce(substring(c.relname from '^(k[a-z]{2})_'), '(other)') as prefix,
               n.nspname, count(*) as tables,
               pg_size_pretty(sum(pg_total_relation_size(c.oid))) as total,
               sum(pg_total_relation_size(c.oid)) as bytes
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where c.relkind = 'r'
        group by 1, 2 order by bytes desc"""),
    ("ksc_bars_by_timeframe", """
        select timeframe, count(*) as rows, count(distinct ticker) as tickers,
               min(d), max(d), count(*) filter (where a is null) as a_null
        from ksc_bars group by timeframe order by timeframe"""),
    ("wm_dup_periods_summary", """
        with periods as (
          select ticker, timeframe,
                 case timeframe when 'W' then date_trunc('week', d::timestamp)::date
                                when 'M' then date_trunc('month', d::timestamp)::date end as period_start,
                 d
          from ksc_bars where timeframe in ('W','M')
        ), g as (
          select timeframe, ticker, period_start, count(*) as n
          from periods group by 1,2,3 having count(*) > 1
        )
        select timeframe, count(*) as dup_periods, count(distinct ticker) as tickers,
               sum(n - 1) as extra_rows, min(period_start), max(period_start)
        from g group by timeframe order by timeframe"""),
    ("wm_dup_periods_by_month", """
        with periods as (
          select ticker, timeframe,
                 case timeframe when 'W' then date_trunc('week', d::timestamp)::date
                                when 'M' then date_trunc('month', d::timestamp)::date end as period_start
          from ksc_bars where timeframe in ('W','M')
        ), g as (
          select timeframe, ticker, period_start, count(*) as n
          from periods group by 1,2,3 having count(*) > 1
        )
        select timeframe, date_trunc('month', period_start)::date as month,
               count(*) as dup_periods, sum(n - 1) as extra_rows
        from g group by 1,2 order by 1,2"""),
    ("wm_dup_sample", """
        with periods as (
          select ticker, timeframe, d, c, v,
                 case timeframe when 'W' then date_trunc('week', d::timestamp)::date
                                when 'M' then date_trunc('month', d::timestamp)::date end as period_start
          from ksc_bars where timeframe in ('W','M') and d >= date '2026-08-01'
        )
        select p.* from periods p
        join (select ticker, timeframe, period_start from periods
              group by 1,2,3 having count(*) > 1 limit 3) x using (ticker, timeframe, period_start)
        order by ticker, timeframe, d"""),
    ("wm_vs_daily_last_complete_week", """
        -- 직전 완성 주(월요일 시작)에 대해 저장 W 최신 행과 D 집계의 불일치 종목 수
        with wk as (select date_trunc('week', now() at time zone 'Asia/Seoul')::date - 7 as ws),
        dagg as (
          select b.ticker, max(b.h) as h, min(b.l) as l, sum(b.v) as v,
                 (array_agg(b.o order by b.d))[1] as o, (array_agg(b.c order by b.d desc))[1] as c,
                 max(b.d) as last_d
          from ksc_bars b, wk where b.timeframe = 'D' and b.d >= wk.ws and b.d < wk.ws + 7
          group by b.ticker
        ), wrow as (
          select distinct on (b.ticker) b.ticker, b.d, b.o, b.h, b.l, b.c, b.v
          from ksc_bars b, wk where b.timeframe = 'W' and b.d >= wk.ws and b.d < wk.ws + 7
          order by b.ticker, b.d desc
        )
        select (select ws from wk) as week_start, count(*) as compared,
               count(*) filter (where w.ticker is null) as w_missing,
               count(*) filter (where w.ticker is not null and
                 (w.o, w.h, w.l, w.c, w.v) is distinct from (dg.o, dg.h, dg.l, dg.c, dg.v)) as w_mismatch,
               count(*) filter (where w.d is distinct from dg.last_d) as w_date_mismatch
        from dagg dg left join wrow w using (ticker)"""),
    ("daily_rows_by_market_40d", """
        with ds as (select distinct d from ksc_bars where timeframe='D'
                    and d > (now() at time zone 'Asia/Seoul')::date - 70)
        select b.d, t.market, count(*) as rows, count(*) filter (where b.v = 0) as v0,
               count(*) filter (where b.a is null) as a_null
        from ksc_bars b join ksc_tickers t using (ticker)
        where b.timeframe='D' and b.d in (select d from ds)
        group by 1,2 order by 1 desc, 2 limit 90"""),
    ("calendar_daily_vs_index", """
        with dd as (select distinct d from ksc_bars where timeframe='D' and d >= date '2026-06-01'),
             ix as (select d, count(*) as mkts from ksc_index_bars where d >= date '2026-06-01' group by d)
        select coalesce(dd.d, ix.d) as d, dd.d is not null as in_daily, ix.mkts
        from dd full join ix on ix.d = dd.d
        where dd.d is null or ix.d is null or ix.mkts <> 2 order by 1"""),
    ("flows_by_day_40", """
        select f.d, t.market, count(*) as rows,
               count(*) filter (where f.inst_net is null) as inst_null,
               count(*) filter (where f.foreign_net is null) as fgn_null,
               count(*) filter (where f.foreign_etc_net is null) as fgn_etc_null
        from ksc_investor_flows f left join ksc_tickers t using (ticker)
        where f.d > (now() at time zone 'Asia/Seoul')::date - 70
        group by 1,2 order by 1 desc, 2 limit 90"""),
    ("flows_range", "select min(d), max(d), count(distinct d) as days, count(*) from ksc_investor_flows"),
    ("shorting_by_day_40", """
        select s.d, t.market, count(*) as rows,
               count(*) filter (where s.buy_vol = 0) as buy0,
               count(*) filter (where s.short_vol = 0) as short0,
               round(avg(s.ratio), 3) as avg_ratio,
               percentile_cont(0.5) within group (order by s.ratio) as p50,
               percentile_cont(0.9) within group (order by s.ratio) as p90
        from ksc_shorting s left join ksc_tickers t using (ticker)
        where s.d > (now() at time zone 'Asia/Seoul')::date - 70
        group by 1,2 order by 1 desc, 2 limit 90"""),
    ("shorting_range", "select min(d), max(d), count(distinct d) as days, count(*) from ksc_shorting"),
    ("shorting_ratio_check", """
        select count(*) as rows,
               count(*) filter (where buy_vol > 0 and abs(ratio - round(100.0*short_vol/buy_vol, 2)) > 0.011) as ratio_mismatch
        from ksc_shorting where d >= (select max(d) from ksc_shorting) - 30"""),
    ("tickers_by_market", """
        select market, count(*) as n,
               count(*) filter (where sector = '') as sector_empty,
               count(distinct sector) as sectors,
               count(*) filter (where mktcap is null) as mktcap_null,
               min(updated_at), max(updated_at), max(mktcap_d)
        from ksc_tickers group by 1 order by 1"""),
    ("tickers_code_patterns", """
        select market,
               count(*) filter (where ticker !~ '^[0-9]{6}$') as alnum_code,
               count(*) filter (where right(ticker,1) <> '0') as last_digit_not0,
               count(*) filter (where name ~ '스팩|SPAC') as spac_name,
               count(*) filter (where name ~ '(우|우B|우C|\\(전환\\))$') as pref_name,
               count(*) filter (where name ~ '리츠|REIT') as reit_name
        from ksc_tickers group by 1 order by 1"""),
    ("sector_counts", """
        select market, sector, count(*) as n from ksc_tickers
        group by 1,2 order by 1, 3 desc"""),
    ("tickers_without_recent_bars", """
        with mx as (select max(d) as md from ksc_bars where timeframe='D')
        select t.market, count(*) as no_bar_last10
        from ksc_tickers t
        where not exists (select 1 from ksc_bars b, mx where b.ticker = t.ticker and b.timeframe='D'
                          and b.d > mx.md - 14)
        group by 1"""),
    ("daily_history_depth", """
        select width_bucket(n, 0, 800, 8) as bucket, min(n), max(n), count(*) as tickers
        from (select ticker, count(*) as n from ksc_bars where timeframe='D' group by ticker) x
        group by 1 order by 1"""),
    ("ksc_meta", "select key, left(value::text, 600) as value, updated_at from ksc_meta order by key"),
    ("ksa_signals_recent", """
        select d, strategy, count(*) as n, count(*) filter (where suppressed) as suppressed,
               min(created_at), max(created_at)
        from ksa_signals where d >= (now() at time zone 'Asia/Seoul')::date - 20
        group by 1,2 order by 1 desc, 2"""),
    ("ksa_runs_recent", """
        select run_at, data_date, universe_n, signal_n, status from ksa_runs
        order by run_at desc limit 12"""),
    ("kss_existing", "select relname from pg_class where relname like 'kss\\_%' and relkind in ('r','v')"),
    ("roles", """
        select rolname, rolsuper, rolbypassrls, rolcanlogin from pg_roles
        where rolname !~ '^pg_' order by 1"""),
    ("anon_policies_on_ksc", """
        select tablename, policyname, roles::text, cmd from pg_policies
        where tablename like 'ksc\\_%' or tablename like 'ksa\\_%' order by 1,2"""),
]


def load_url(env_path: Path) -> str:
    """env 파일에서 SUPABASE_DATABASE_URL만 읽는다."""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("SUPABASE_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("SUPABASE_DATABASE_URL 없음")


def main() -> None:
    url = load_url(Path(sys.argv[1]))
    opts = "-c default_transaction_read_only=on -c statement_timeout=180000"
    with psycopg.connect(url, options=opts, prepare_threshold=None) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("show transaction_read_only")
            row = cur.fetchone()
            print("# transaction_read_only =", row[0] if row else None)
        for title, sql in QUERIES:
            t0 = time.monotonic()
            print(f"\n## {title}")
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cols = [c.name for c in cur.description or []]
                    print(" | ".join(cols))
                    for row in cur.fetchall():
                        print(" | ".join("" if v is None else str(v) for v in row))
            except Exception as exc:  # 실패도 기록으로 남긴다
                conn.rollback()
                print(f"ERROR {type(exc).__name__}: {str(exc).splitlines()[0]}")
            print(f"({time.monotonic() - t0:.1f}s)")
            conn.rollback()


if __name__ == "__main__":
    main()
