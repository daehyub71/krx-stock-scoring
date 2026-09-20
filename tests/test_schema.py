"""kss 스키마 회귀 — 권한·RLS·상태 계약이 조용히 풀리지 않게 한다 (SPEC N11·§4.3·§6.4).

파일 수준 검사는 자격증명 없이 돈다. 실DB 검사는 `pytest -m db`.
공유 DB에서 "나중에 쓸지 모르니 열어 둔 anon 정책"이 두 번 구멍이 됐다 — 여기서는 아예 막는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SCHEMA = Path(__file__).resolve().parents[1] / "scoring" / "store" / "schema.sql"
# 주석은 지우고 문장만 검사한다 (설명 문구가 규칙 검사에 걸리지 않게)
SQL = re.sub(r"--[^\n]*", "", SCHEMA.read_text(encoding="utf-8"))
TABLES = re.findall(r"create table if not exists (\w+)", SQL)

M1_TABLES = {
    "kss_runs", "kss_source_checks", "kss_universe_snapshots", "kss_scores",
    "kss_score_parts", "kss_publications", "kss_publication_history", "kss_signal_cross",
    "kss_sector_stats",
}


def _check_values(name: str) -> set[str]:
    m = re.search(rf"constraint {name} check \((\w+) in \((.*?)\)\)", SQL, re.S)
    assert m, name
    return set(re.findall(r"'(\w+)'", m.group(2)))


def test_schema_has_m1_tables() -> None:
    assert set(TABLES) == M1_TABLES


def test_schema_all_tables_prefixed() -> None:
    assert all(t.startswith("kss_") for t in TABLES)


def test_schema_rls_enabled_on_every_table() -> None:
    enabled = set(re.findall(r"alter table (\w+)\s+enable row level security", SQL))
    assert enabled == set(TABLES)


def test_schema_no_anon_or_authenticated_policy() -> None:
    for policy in re.findall(r"create policy .*?;", SQL, re.S):
        assert not re.search(r"\bto\s+(anon|authenticated|public)\b", policy), policy


def test_schema_revokes_default_grants_from_every_table() -> None:
    m = re.search(r"revoke all on (.*?)\s+from anon, authenticated;", SQL, re.S)
    assert m
    revoked = {t.strip() for t in m.group(1).replace("\n", " ").split(",")}
    assert revoked == set(TABLES)


def test_schema_reader_is_select_only() -> None:
    grants = re.findall(r"grant ([\w, ]+) on [\w, \n]+? to kss_reader", SQL)
    assert grants and all(g.strip() == "select" for g in grants)
    for policy in re.findall(r"create policy \w+ on \w+ for (\w+) to kss_reader", SQL):
        assert policy == "select"


def test_schema_reader_sees_only_published_runs() -> None:
    for table in ("kss_runs", "kss_source_checks", "kss_scores", "kss_score_parts",
                  "kss_signal_cross", "kss_sector_stats"):
        pat = (
            rf"on {table} for select to kss_reader\s+"
            r"using \(exists \(select 1 from kss_publications"
        )
        assert re.search(pat, SQL), table


def test_schema_no_bypassrls_role() -> None:
    assert "bypassrls" not in SQL.lower()


def test_schema_run_statuses_match_spec() -> None:
    # SPEC §6.4
    assert _check_values("kss_runs_status") == {
        "created", "checking", "computing", "validating", "published", "published_degraded",
        "waiting_upstream", "no_trading", "failed", "cancelled",
    }


def test_schema_score_statuses_match_spec() -> None:
    assert _check_values("kss_scores_status") == {
        "scored", "provisional", "insufficient_data", "excluded",
    }


def test_schema_part_states_match_spec() -> None:
    assert _check_values("kss_parts_state") == {
        "observed", "no_event", "adverse_defined", "missing", "not_applicable",
    }


def test_schema_total_and_grade_only_when_scored() -> None:
    assert "check (total is null or status = 'scored')" in SQL
    assert "check (grade is null or status = 'scored')" in SQL


def test_schema_scores_keyed_by_run_not_date() -> None:
    # (d, ticker) 덮어쓰기 폐기 — 실행별 불변 (SPEC V15)
    m = re.search(r"create table if not exists kss_scores \((.*?)\n\);", SQL, re.S)
    assert m and "primary key (run_id, ticker)" in m.group(1)


def test_schema_no_secrets_in_file() -> None:
    assert not re.search(r"password\s+'", SQL, re.I)


@pytest.mark.db
def test_schema_db_rls_and_policies() -> None:
    from scoring.config import connect_kss

    with connect_kss() as conn, conn.cursor() as cur:
        cur.execute(
            "select relname, relrowsecurity from pg_class "
            "where relname like 'kss\\_%%' and relkind = 'r'"
        )
        rows = {str(r[0]): bool(r[1]) for r in cur.fetchall()}
        assert set(rows) >= M1_TABLES
        assert all(rows[t] for t in M1_TABLES)
        cur.execute(
            "select tablename, roles::text from pg_policies where tablename like 'kss\\_%%'"
        )
        for table, roles in cur.fetchall():
            assert "anon" not in str(roles) and "authenticated" not in str(roles), table
        cur.execute(
            "select grantee, table_name from information_schema.role_table_grants "
            "where table_name like 'kss\\_%%' and grantee in ('anon', 'authenticated')"
        )
        assert cur.fetchall() == []
