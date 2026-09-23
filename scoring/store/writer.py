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
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from scoring import archive
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


def json_value(value: Any) -> Jsonb:
    """노드 층이 jsonb 열에 값을 넣을 때 쓰는 어댑터 (psycopg 타입을 밖으로 내보내지 않는다)."""
    return _json(value)


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


# ── M3 공시·사전·뉴스 ───────────────────────────────────────────


def write_disclosures(conn: Conn, rows: Sequence[tuple[Any, ...]]) -> int:
    """공시 원본 — 접수번호가 곧 키다. 같은 공시를 다시 받아도 `first_seen_at`은 그대로 둔다."""
    with conn.cursor() as cur:
        for chunk in _chunks(rows):
            cur.executemany(
                "insert into kss_disclosures (rcept_no, corp_code, stock_code, corp_name, "
                "corp_cls, report_nm, rcept_dt, flr_nm, rm, norm_name, corrected, note) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "on conflict (rcept_no) do update set fetched_at = now()", chunk)
            conn.commit()
    return len(rows)


def write_risk_events(conn: Conn, rows: Sequence[tuple[Any, ...]]) -> int:
    """판정된 사건. 사전 버전이 다르면 다른 행이다 — 과거 판정을 덮어쓰지 않는다."""
    with conn.cursor() as cur:
        for chunk in _chunks(rows):
            cur.executemany(
                "insert into kss_risk_events (ticker, rcept_no, rule_id, lexicon_version, "
                "level, fatal, subsidiary, rcept_dt) values (%s, %s, %s, %s, %s, %s, %s, %s) "
                "on conflict do nothing", chunk)
            conn.commit()
    return len(rows)


def write_lexicon_version(conn: Conn, version: str, scope: str, entries: Sequence[Any]) -> int:
    """사전 스냅샷. 같은 내용이면 행이 늘지 않는다 (SPEC §5.5)."""
    with conn.cursor() as cur:
        cur.execute(
            "insert into kss_lexicon_versions (lexicon_version, scope, entry_count, entries) "
            "values (%s, %s, %s, %s) on conflict (lexicon_version) do nothing",
            (version, scope, len(entries), _json(list(entries))))
        conn.commit()
        return int(cur.rowcount or 0)


def write_news_observations(conn: Conn, run_id: UUID, rows: Sequence[tuple[Any, ...]]) -> int:
    """실행 단위 뉴스 관측 — 미조회·실패·기사 없음을 그대로 남긴다."""
    with conn.cursor() as cur:
        for chunk in _chunks([(run_id, *r) for r in rows]):
            cur.executemany(
                "insert into kss_news_observations (run_id, ticker, status, query, window_from, "
                "window_to, articles, points, lexicon_version, note) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "on conflict (run_id, ticker) do nothing", chunk)
            conn.commit()
    return len(rows)


# ── M4 운영 — 좀비 실행 정리 · 복구 대상 ────────────────────────


def sweep_stale_runs(conn: Conn, minutes: int = 90) -> list[UUID]:
    """heartbeat가 끊긴 실행을 `failed`로 닫는다 (SPEC §6.4).

    강제 종료된 실행(러너 타임아웃·취소)은 `computing` 상태로 남아 다음 실행이 같은 T를
    이미 처리 중이라고 오해하게 만든다. **게시된 실행은 건드리지 않는다** — 끝나지 않은 것만 닫는다.

    Args:
        conn: kss 연결.
        minutes: 이만큼 heartbeat가 없으면 죽은 것으로 본다.

    Returns:
        닫은 실행 ID.
    """
    with conn.cursor() as cur:
        cur.execute(
            "update kss_runs set status = 'failed', finished_at = now(), updated_at = now(), "
            "error = coalesce(error, %s) "
            "where status in ('created', 'checking', 'computing', 'validating') "
            "  and coalesce(heartbeat_at, created_at) < now() - make_interval(mins => %s) "
            "returning run_id",
            (f"heartbeat {minutes}분 끊김 — 강제 종료로 본다", minutes),
        )
        rows = [UUID(str(r[0])) for r in cur.fetchall()]
    conn.commit()
    return rows


def published_dates(conn: Conn, profile: str, since: date) -> set[date]:
    """이미 게시된 거래일 (복구 대상을 가릴 때 쓴다)."""
    with conn.cursor() as cur:
        cur.execute(
            "select data_date from kss_publications where profile = %s and data_date >= %s",
            (profile, since),
        )
        out = {r[0] for r in cur.fetchall()}
    conn.rollback()
    return out


# ── M4 아카이브·보존 삭제 ───────────────────────────────────────


def export_run(conn: Conn, run_id: UUID, out_dir: Path) -> archive.Manifest:
    """한 실행의 행을 `jsonl.gz`로 내보낸다 (SPEC §7.4)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with conn.cursor() as cur:
        cur.execute("select data_date, profile from kss_runs where run_id = %s", (run_id,))
        head = cur.fetchone()
        if head is None:
            raise ValueError(f"없는 실행: {run_id}")
        manifest = archive.Manifest(run_id=str(run_id), data_date=head[0].isoformat(),
                                    profile=str(head[1]))
        for name, sql in archive.EXPORTS.items():
            cur.execute(sql, (run_id,))
            columns = [d.name for d in cur.description or ()]
            manifest.files.append(
                archive.write_jsonl_gz(out_dir / f"{name}.jsonl.gz", columns, iter(cur.fetchall()))
            )
    conn.rollback()
    return manifest


def record_snapshot(conn: Conn, manifest: archive.Manifest, uri: str | None,
                    verified: bool) -> str:
    """아카이브 사실을 남긴다. 같은 내용이면 행이 늘지 않는다."""
    snapshot_id = manifest.snapshot_id()
    with conn.cursor() as cur:
        cur.execute(
            "insert into kss_input_snapshots (snapshot_id, run_id, data_date, profile, manifest, "
            "archive_uri, rows, bytes, verified_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, case when %s then now() end) "
            "on conflict (snapshot_id) do update set archive_uri = coalesce("
            "excluded.archive_uri, kss_input_snapshots.archive_uri), "
            "verified_at = coalesce(excluded.verified_at, kss_input_snapshots.verified_at)",
            (snapshot_id, UUID(manifest.run_id), date.fromisoformat(manifest.data_date),
             manifest.profile, _json(manifest.as_dict()), uri, manifest.rows, manifest.bytes,
             verified),
        )
    conn.commit()
    return snapshot_id


def archivable_runs(conn: Conn, cutoff: date) -> list[UUID]:
    """보존 경계 밖이라 **내보낼** 실행.

    아카이브 여부를 여기서 묻지 않는다 — 물으면 첫 아카이브가 영영 일어나지 않는다(2026-09-23).
    지워도 되는지는 `prune`이 다시 따진다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select r.run_id from kss_runs r "
            "where r.data_date < %s "
            "  and not exists (select 1 from kss_publications p where p.run_id = r.run_id) "
            "order by r.data_date",
            (cutoff,),
        )
        rows = [UUID(str(r[0])) for r in cur.fetchall()]
    conn.rollback()
    return rows


# 갈래 → (표, 실행을 가리키는 열). 유니버스만 `snapshot_id`다
PRUNE_TABLES = {
    "parts": ("kss_score_parts", "run_id"),
    "universe": ("kss_universe_snapshots", "snapshot_id"),
    "scores": ("kss_scores", "run_id"),
}


def prune(conn: Conn, table_key: str, run_ids: Sequence[UUID]) -> int:
    """상세를 지운다 — **SPEC §7.4의 세 가지 금지를 SQL이 다시 확인한 것만**.

    부르는 쪽이 이미 걸렀더라도 여기서 한 번 더 본다. 삭제는 되돌릴 수 없고,
    조건 한 줄이 빠진 채 지나가는 쪽이 훨씬 비싸다.

    - 복원 검증을 통과한 아카이브가 있어야 한다
    - 게시본이 가리키는 실행은 지우지 않는다

    **미해결 위험은 여기서 막지 않는다.** SPEC §7.4의 「미해결 위험은 제거하지 않는다」는
    위험 사건과 그 근거 공시를 지키라는 말이고, 그것들은 `kss_risk_events`·`kss_disclosures`에
    있으며 애초에 삭제 대상이 아니다(`PRUNE_TABLES` 참조). 이를 실행 단위 조건으로 적었더니
    미해결 위험이 있는 종목이 하나라도 든 실행이 전부 막혀 **아무것도 지워지지 않았다**
    (2026-09-23 실측: 827건이 열려 있어 모든 실행이 대상에서 빠졌다).
    """
    if not run_ids:
        return 0
    table, column = PRUNE_TABLES[table_key]
    with conn.cursor() as cur:
        cur.execute(
            f"delete from {table} d where d.{column} = any(%s) "
            f"  and exists (select 1 from kss_input_snapshots s "
            f"              where s.run_id = d.{column} and s.verified_at is not null) "
            f"  and not exists (select 1 from kss_publications p where p.run_id = d.{column})",
            (list(run_ids),),
        )
        deleted = int(cur.rowcount)
    conn.commit()
    return deleted
