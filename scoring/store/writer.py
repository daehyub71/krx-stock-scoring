"""kss 저장·게시 — 실행별 불변 저장과 원자적 게시 (SPEC §6.4·§7.2, N22).

- 실행 행을 **먼저** 만든다(실패해도 남는다).
- 점수·근거는 500행 청크로 저장한다. 게시 전이라 웹에는 보이지 않는다(kss_reader 정책).
- 게시는 한 트랜잭션: 같은 (T, 프로필) 잠금 → 포인터 교체 → 이력 → 실행 상태.
  중간에 실패하면 이전 게시본이 그대로 남는다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from scoring.checks import SourceCheck
from scoring.compute import TickerResult
from scoring.rules import Rules

Conn = psycopg.Connection[tuple[Any, ...]]
CHUNK = 500


def _chunks(
    rows: Sequence[tuple[Any, ...]], size: int = CHUNK
) -> Iterable[Sequence[tuple[Any, ...]]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _json(value: Any) -> Jsonb:
    # float NaN·inf는 JSON이 아니다 — 저장 전에 막는다
    json.dumps(value, allow_nan=False, default=str)
    return Jsonb(value)


def create_run(
    conn: Conn, *, data_date: date, profile: str, rules: Rules, trigger: str, code_sha: str | None
) -> UUID:
    """실행 행을 만들고 즉시 커밋한다."""
    with conn.cursor() as cur:
        cur.execute(
            "insert into kss_runs (data_date, profile, status, trigger, code_sha, rules_version, "
            "rules_hash, information_cutoff_at) values (%s, %s, 'created', %s, %s, %s, %s, now()) "
            "returning run_id",
            (data_date, profile, trigger, code_sha, rules.version, rules.hash),
        )
        row = cur.fetchone()
    conn.commit()
    assert row is not None
    return UUID(str(row[0]))


def update_run(
    conn: Conn, run_id: UUID, status: str, *, stats: dict[str, Any] | None = None,
    error: str | None = None, finished: bool = False,
) -> None:
    """실행 상태를 바꾸고 커밋한다 (heartbeat 겸)."""
    with conn.cursor() as cur:
        cur.execute(
            "update kss_runs set status = %s, updated_at = now(), heartbeat_at = now(), "
            "stats = stats || %s::jsonb, "
            "error = coalesce(%s, error), "
            "finished_at = case when %s then now() else finished_at end where run_id = %s",
            (status, _json(stats or {}), error, finished, run_id),
        )
    conn.commit()


def write_source_checks(conn: Conn, run_id: UUID, checks: Sequence[SourceCheck]) -> None:
    """출처별 품질 기록."""
    with conn.cursor() as cur:
        cur.executemany(
            "insert into kss_source_checks (run_id, source, market, expected_count, stored_count, "
            "valid_count, null_count, coverage, status, reason, max_date) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [(run_id, c.source, c.market, c.expected_count, c.stored_count, c.valid_count,
              c.null_count, c.coverage, c.status, c.reason, c.max_date) for c in checks],
        )
    conn.commit()


def write_results(
    conn: Conn, run_id: UUID, data_date: date, profile: str,
    results: Sequence[TickerResult], source_id: str,
) -> dict[str, int]:
    """유니버스 스냅샷·점수·근거를 청크로 저장한다. (행 수 반환)"""
    universe = [
        (run_id, r.entry.meta.ticker, r.entry.meta.name, r.entry.meta.market, r.entry.meta.sector,
         list(r.entry.classification), r.entry.excluded_reason)
        for r in results
    ]
    scores = [
        (run_id, r.entry.meta.ticker, data_date, profile, r.entry.meta.name, r.entry.meta.market,
         r.entry.meta.sector, r.row.status, r.row.profile_max, r.row.raw_total, r.row.total,
         r.row.estimated_total,
         r.row.grade, r.row.coverage, _json(r.row.axis), r.row.availability_signature,
         r.row.rank_eligible, list(r.row.risk_flags), r.row.passes_screen)
        for r in results
    ]
    parts = [
        (run_id, r.entry.meta.ticker, p.item, p.axis, p.points, p.max, p.state, p.missing_reason,
         _json(p.actual), p.note, source_id)
        for r in results for p in r.parts
    ]
    with conn.cursor() as cur:
        for chunk in _chunks(universe):
            cur.executemany(
                "insert into kss_universe_snapshots (snapshot_id, ticker, name, market, sector, "
                "classification, excluded_reason) values (%s, %s, %s, %s, %s, %s, %s)", chunk)
            conn.commit()
        for chunk in _chunks(scores):
            cur.executemany(
                "insert into kss_scores (run_id, ticker, data_date, profile, name, market, sector, "
                "status, profile_max, raw_total, total, estimated_total, grade, coverage, axis, "
                "availability_signature, rank_eligible, risk_flags, passes_screen) "
                "values (" + ", ".join(["%s"] * 19) + ")",
                chunk)
            conn.commit()
        for chunk in _chunks(parts):
            cur.executemany(
                "insert into kss_score_parts (run_id, ticker, item, axis, points, max, state, "
                "missing_reason, actual, note, source_snapshot_id) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", chunk)
            conn.commit()
    return {"universe": len(universe), "scores": len(scores), "parts": len(parts)}


def write_sector_stats(
    conn: Conn, run_id: UUID, rows: Sequence[tuple[str, str, str, float, int]]
) -> int:
    """업종·시장 중앙값 (우리가 계산한 PER·PBR 기준)."""
    with conn.cursor() as cur:
        cur.executemany(
            "insert into kss_sector_stats (run_id, market, sector, metric, median, samples) "
            "values (%s, %s, %s, %s, %s, %s) on conflict do nothing",
            [(run_id, m, s, k, med, n) for m, s, k, med, n in rows],
        )
    conn.commit()
    return len(rows)


def write_financial_versions(conn: Conn, rows: Sequence[tuple[Any, ...]]) -> int:
    """재무 원본 불변 버전 — 같은 접수번호·내용이면 늘지 않는다 (SPEC §7.2)."""
    with conn.cursor() as cur:
        for chunk in _chunks(rows):
            cur.executemany(
                "insert into kss_financial_versions (corp_code, bsns_year, reprt_code, rcept_no, "
                "content_hash, basis, period_end, rcept_date, items) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict do nothing", chunk)
            conn.commit()
    return len(rows)


def write_corp_map(conn: Conn, version: str, mapping: Mapping[str, str]) -> int:
    """종목 ↔ 법인 매핑 버전. 이미 있는 버전이면 아무것도 쓰지 않는다."""
    with conn.cursor() as cur:
        cur.execute("select 1 from kss_corp_map where mapping_version = %s limit 1", (version,))
        if cur.fetchone():
            conn.rollback()
            return 0
        rows = [(version, stock, corp) for stock, corp in sorted(mapping.items())]
        for chunk in _chunks(rows):
            cur.executemany(
                "insert into kss_corp_map (mapping_version, stock_code, corp_code) "
                "values (%s, %s, %s) on conflict do nothing", chunk)
            conn.commit()
    return len(mapping)


def count_rows(conn: Conn, run_id: UUID) -> dict[str, int]:
    """저장된 행 수 (게시 전 대조)."""
    with conn.cursor() as cur:
        cur.execute(
            "select (select count(*) from kss_universe_snapshots where snapshot_id = %s), "
            "(select count(*) from kss_scores where run_id = %s), "
            "(select count(*) from kss_score_parts where run_id = %s)",
            (run_id, run_id, run_id),
        )
        u, s, p = cur.fetchone() or (0, 0, 0)
    conn.rollback()
    return {"universe": u, "scores": s, "parts": p}


def publish(
    conn: Conn, run_id: UUID, data_date: date, profile: str, status: str, reason: str
) -> datetime:
    """게시 포인터를 한 트랜잭션에서 교체한다. (게시 시각 반환)"""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"kss:{data_date}:{profile}",))
        cur.execute(
            "select run_id from kss_publications where data_date = %s and profile = %s for update",
            (data_date, profile),
        )
        prev = cur.fetchone()
        cur.execute(
            "insert into kss_publications (data_date, profile, run_id, status, published_at) "
            "values (%s, %s, %s, %s, now()) on conflict (data_date, profile) do update "
            "set run_id = excluded.run_id, status = excluded.status, "
            "published_at = excluded.published_at returning published_at",
            (data_date, profile, run_id, status),
        )
        row = cur.fetchone()
        cur.execute(
            "insert into kss_publication_history "
            "(data_date, profile, prev_run_id, new_run_id, reason) values (%s, %s, %s, %s, %s)",
            (data_date, profile, prev[0] if prev else None, run_id, reason),
        )
        cur.execute(
            "update kss_runs set status = %s, finished_at = now(), updated_at = now() "
            "where run_id = %s",
            (status, run_id),
        )
    assert row is not None
    published_at: datetime = row[0]
    return published_at


def write_signal_cross(conn: Conn, rows: Sequence[tuple[Any, ...]]) -> int:
    """alerts 대조 행 (이미 있으면 갱신)."""
    with conn.cursor() as cur:
        cur.executemany(
            "insert into kss_signal_cross (signal_data_date, ticker, strategy, score_run_id, "
            "comparison_mode, signal_created_at, score_published_at, available_at_signal, rank_no, "
            "suppressed, score_status, axis_points, passes_screen, "
            "upstream_weekly_quality_unverified, note) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict (signal_data_date, ticker, strategy, score_run_id, comparison_mode) "
            "do update set score_published_at = excluded.score_published_at, "
            "available_at_signal = excluded.available_at_signal",
            rows,
        )
    conn.commit()
    return len(rows)
