"""게시 원자성 — 실DB (`pytest -m db`). 시험 프로필 `test`로만 쓰고 끝나면 지운다.

SPEC §11.1 「저장 절반 실패 시 웹은 이전 전체 게시본 유지. 새 게시본은 한 번에 교체」.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import psycopg
import pytest

from scoring.config import connect_kss, connect_kss_batch
from scoring.rules import load_rules
from scoring.store import writer

RULES = load_rules(Path(__file__).resolve().parents[1] / "rules" / "v0.toml")
T = date(2000, 1, 3)   # 실제 게시와 겹치지 않는 날짜
PROFILE = "test"


def _cleanup() -> None:
    with connect_kss() as owner, owner.cursor() as cur:
        cur.execute("delete from kss_publication_history where profile = %s", (PROFILE,))
        cur.execute("delete from kss_publications where profile = %s", (PROFILE,))
        cur.execute("delete from kss_runs where profile = %s", (PROFILE,))
        owner.commit()


@pytest.mark.db
def test_writer_db_failed_publish_keeps_previous_pointer() -> None:
    _cleanup()
    try:
        with connect_kss_batch() as kss:
            first = writer.create_run(kss, data_date=T, profile=PROFILE, rules=RULES,
                                      trigger="test", code_sha=None)
            writer.publish(kss, first, T, PROFILE, "published", "test first")
            second = writer.create_run(kss, data_date=T, profile=PROFILE, rules=RULES,
                                       trigger="test", code_sha=None)
            # 잘못된 상태값 → 포인터 교체 도중 제약 위반 → 트랜잭션 전체 롤백
            with pytest.raises(psycopg.errors.CheckViolation):
                writer.publish(kss, second, T, PROFILE, "bogus", "test fail")
            kss.rollback()
            with kss.cursor() as cur:
                cur.execute("select run_id from kss_publications where data_date = %s "
                            "and profile = %s", (T, PROFILE))
                assert cur.fetchone() == (first,)
                cur.execute("select count(*) from kss_publication_history where profile = %s",
                            (PROFILE,))
                assert cur.fetchone() == (1,)
                cur.execute("select status from kss_runs where run_id = %s", (second,))
                assert cur.fetchone() == ("created",)
            # 정상 교체는 한 번에 — 이력에 이전 run이 남는다
            writer.publish(kss, second, T, PROFILE, "published", "test second")
            with kss.cursor() as cur:
                cur.execute("select prev_run_id, new_run_id from kss_publication_history "
                            "where profile = %s order by id desc limit 1", (PROFILE,))
                assert cur.fetchone() == (first, second)
            kss.rollback()
    finally:
        _cleanup()
