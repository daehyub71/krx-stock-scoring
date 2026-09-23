"""M4 운영 — 아카이브·보존·모의 장애 (SPEC §6.4 · §7.4).

여기 있는 것은 전부 **실제로 일어날 수 있는 사고**를 흉내 낸 것이다.
지연·부분 실패·중복 이벤트·중간 저장 실패 넷은 M4 완료 조건이다 (PLAN M4).
"""

from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scoring import archive
from scoring.checks import gate_aux, publish_decision, recovery_targets
from scoring.store import writer

T = date(2026, 9, 18)
SESSIONS = [date(2026, 1, 2)] + [date(2026, 9, d) for d in (14, 15, 16, 17, 18)]


# ── 아카이브 ────────────────────────────────────────────────────


def test_roundtrip_preserves_every_row(tmp_path: Path) -> None:
    rows = [(1, "005930", date(2026, 9, 18), {"per": 11.47}), (2, "000660", T, None)]
    entry = archive.write_jsonl_gz(
        tmp_path / "scores.jsonl.gz", ["id", "ticker", "d", "actual"], iter(rows))
    back = archive.read_jsonl_gz(tmp_path / "scores.jsonl.gz")
    assert entry.rows == 2 and len(back) == 2
    assert back[0]["ticker"] == "005930"
    assert back[0]["d"] == "2026-09-18"        # date는 ISO 문자열로
    assert back[0]["actual"] == {"per": 11.47}  # jsonb는 그대로
    assert back[1]["actual"] is None


def test_same_content_gives_the_same_hash(tmp_path: Path) -> None:
    """재실행이 같은 내용을 새 파일로 만들지 않게 — gzip 시각을 고정했다."""
    rows = [(1, "005930")]
    a = archive.write_jsonl_gz(tmp_path / "a.jsonl.gz", ["id", "t"], iter(rows))
    b = archive.write_jsonl_gz(tmp_path / "b.jsonl.gz", ["id", "t"], iter(rows))
    assert a.sha256 == b.sha256
    assert (tmp_path / "a.jsonl.gz").read_bytes() == (tmp_path / "b.jsonl.gz").read_bytes()


def manifest_of(tmp_path: Path, rows: list[tuple[Any, ...]]) -> archive.Manifest:
    m = archive.Manifest(run_id="0" * 32, data_date=T.isoformat(), profile="common")
    m.files.append(archive.write_jsonl_gz(tmp_path / "scores.jsonl.gz", ["id", "t"], iter(rows)))
    return m


def test_verify_passes_on_an_intact_archive(tmp_path: Path) -> None:
    ok, reason = archive.verify(manifest_of(tmp_path, [(1, "a"), (2, "b")]), tmp_path)
    assert ok and reason == ""


def test_verify_catches_a_truncated_file(tmp_path: Path) -> None:
    """파일 크기만 보면 놓친다 — 되읽어 행 수를 센다."""
    m = manifest_of(tmp_path, [(1, "a"), (2, "b")])
    with gzip.open(tmp_path / "scores.jsonl.gz", "wt", encoding="utf-8") as fp:
        fp.write(json.dumps({"id": 1, "t": "a"}) + "\n")   # 한 줄만 남기고 덮어쓴다
    ok, reason = archive.verify(m, tmp_path)
    assert not ok and "행 수" in reason


def test_verify_catches_altered_content(tmp_path: Path) -> None:
    m = manifest_of(tmp_path, [(1, "a")])
    with gzip.open(tmp_path / "scores.jsonl.gz", "wt", encoding="utf-8") as fp:
        fp.write(json.dumps({"id": 1, "t": "zzz"}) + "\n")  # 행 수는 같고 값만 바뀐다
    ok, reason = archive.verify(m, tmp_path)
    assert not ok and "해시" in reason


def test_verify_catches_a_missing_file(tmp_path: Path) -> None:
    m = manifest_of(tmp_path, [(1, "a")])
    (tmp_path / "scores.jsonl.gz").unlink()
    ok, reason = archive.verify(m, tmp_path)
    assert not ok and "없음" in reason


def test_snapshot_id_changes_with_content(tmp_path: Path) -> None:
    a = manifest_of(tmp_path, [(1, "a")]).snapshot_id()
    b = manifest_of(tmp_path, [(1, "b")]).snapshot_id()
    assert a != b and len(a) == 64


# ── 보존 경계 ───────────────────────────────────────────────────


def test_retention_keeps_the_confirmed_windows() -> None:
    """점수 252 · 근거 3 · 유니버스 20거래일 (SPEC v2.4 확정)."""
    cut = archive.retention_cutoffs(SESSIONS, keep_scores=252, keep_parts=3, keep_universe=20)
    assert cut["parts"] == date(2026, 9, 16)   # 최근 3거래일(16·17·18)만 남긴다
    assert cut["scores"] is None               # 달력이 252거래일보다 짧다
    assert cut["universe"] is None


def test_retention_does_not_invent_a_cutoff_when_history_is_short() -> None:
    """지울 것이 없으면 None이다 — 날짜를 지어내면 남아야 할 것을 지운다."""
    assert archive.retention_cutoffs([T], keep_parts=3)["parts"] is None


def test_prune_guards_are_spelled_out_in_sql() -> None:
    """검증된 아카이브가 있고 게시본이 아닌 것만 지운다 (SPEC §7.4).

    조건 한 줄이 빠지면 되돌릴 수 없는 삭제가 된다.
    """
    text = " ".join(str(c) for c in writer.prune.__code__.co_consts)
    assert "kss_input_snapshots" in text and "verified_at is not null" in text
    assert "not exists" in text and "kss_publications" in text


def test_risk_evidence_tables_are_never_pruned() -> None:
    """위험 사건과 근거 공시는 삭제 대상 자체가 아니다 (SPEC §7.4).

    이걸 실행 단위 조건으로 적었다가 미해결 위험이 있는 종목이 하나라도 든 실행이 전부 막혀
    아무것도 지워지지 않았다 (2026-09-23). 지키는 방법은 조건이 아니라 **대상에서 빼는 것**이다.
    """
    tables = {t for t, _ in writer.PRUNE_TABLES.values()}
    assert tables == {"kss_score_parts", "kss_universe_snapshots", "kss_scores"}
    for protected in ("kss_risk_events", "kss_disclosures", "kss_lexicon",
                      "kss_lexicon_versions", "kss_publications", "kss_runs"):
        assert protected not in tables


def test_archivable_does_not_require_a_prior_archive() -> None:
    """아카이브 여부를 선택 조건에 넣으면 첫 아카이브가 영영 일어나지 않는다 (2026-09-23)."""
    text = " ".join(str(c) for c in writer.archivable_runs.__code__.co_consts)
    assert "kss_input_snapshots" not in text
    assert "kss_publications" in text


def test_prune_with_no_targets_touches_nothing() -> None:
    class Boom:
        def cursor(self) -> Any:
            raise AssertionError("빈 목록에 쿼리를 보내면 안 된다")

    assert writer.prune(Boom(), "parts", []) == 0  # type: ignore[arg-type]


# ── 모의 장애 ───────────────────────────────────────────────────


def test_delayed_upstream_waits_instead_of_publishing() -> None:
    """지연 — 상위 수집이 덜 끝났다. 게시하지 않고 복구 큐에 남긴다."""
    assert publish_decision(bars_ok=False, aux_ok=True) == "wait"


def test_partial_failure_publishes_degraded() -> None:
    """부분 실패 — 수급만 모자라다. 랭킹은 게시하되 모자란 것을 공개한다."""
    aux_ok, check = gate_aux("flows", {"expected": 2774, "stored": 1200, "valid": 1200}, T)
    assert aux_ok is False and check.status == "degraded"
    assert publish_decision(bars_ok=True, aux_ok=aux_ok) == "publish_degraded"


def test_duplicate_event_does_not_publish_twice() -> None:
    """중복 이벤트 — 같은 T·프로필에 두 번 와도 게시는 한 번이다.

    잠금과 멱등성 키가 그 장치다 (SPEC §6.4). 여기서는 SQL에 그것이 있는지 본다.
    """
    text = " ".join(str(c) for c in writer.publish.__code__.co_consts)
    assert "pg_advisory_xact_lock" in text
    assert "on conflict (data_date, profile) do update" in text


def test_interrupted_run_keeps_the_previous_publication() -> None:
    """중간 저장 실패 — 게시 포인터는 한 트랜잭션에서만 바뀐다.

    저장 중 죽은 실행은 heartbeat 만료로 닫히고, 그 실행의 행은 게시본이 가리키지 않는다.
    """
    sweep = " ".join(str(c) for c in writer.sweep_stale_runs.__code__.co_consts)
    assert "heartbeat_at" in sweep
    assert "status in ('created', 'checking', 'computing', 'validating')" in sweep
    # 죽은 실행의 T는 다음 복구 잡이 다시 집어 간다
    assert recovery_targets(SESSIONS[-3:], published={SESSIONS[-1]}, window=3) == SESSIONS[-3:-1]


@pytest.mark.parametrize(
    ("bars_ok", "aux_ok", "expected"),
    [(True, True, "publish"), (True, False, "publish_degraded"),
     (False, True, "wait"), (False, False, "wait")],
)
def test_publish_decision_table(bars_ok: bool, aux_ok: bool, expected: str) -> None:
    assert publish_decision(bars_ok, aux_ok) == expected


def test_prune_order_deletes_the_shortest_retention_first() -> None:
    """점수를 먼저 지우면 근거가 FK로 딸려 가 삭제 수가 0으로 보고된다 (2026-09-23 실측)."""
    assert archive.PRUNE_ORDER == ("parts", "universe", "scores")
    assert set(archive.PRUNE_ORDER) == set(writer.PRUNE_TABLES)
