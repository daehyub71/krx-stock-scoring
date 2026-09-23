"""아카이브와 보존 삭제 — M4 (SPEC §7.4).

확정 보존은 **점수 252거래일 · 근거 3거래일 · 유니버스 20거래일**이다. 그보다 오래된 상세는
지우되, **지우기 전에 옮겨 담고 되읽어 확인한다**. 순서를 뒤집으면 복구할 수 없다.

    내보내기 → 해시 대조 → 되읽기(복원 검증) → `kss_input_snapshots` 기록 → 그때서야 삭제

형식은 **`jsonl.gz`** 다. SPEC §7.4가 `jsonl / parquet`을 허용하는데, jsonl은 표준 라이브러리로
읽고 쓰므로 새 의존성이 없다(최소 의존성 원칙). DuckDB도 그대로 읽는다 —
`select * from read_json_auto('scores.jsonl.gz')`.

**지우지 않는 것** (SPEC §7.4)
- 현재 게시본이 가리키는 실행
- 미해결 위험 사건(`kss_risk_events.cleared_at is null`)이 딸린 실행
- 아직 아카이브되지 않았거나 복원 검증을 통과하지 못한 실행
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

# 보존 (거래일) — v2.4에서 M1 실측 후 확정
KEEP_SCORES = 252
KEEP_PARTS = 3
KEEP_UNIVERSE = 20

# 지우는 순서. **점수를 먼저 지우면 근거가 FK(on delete cascade)로 딸려 가** 근거 삭제 수가
# 0으로 보고된다 (2026-09-23 실측). 짧게 보존하는 것부터 지운다.
PRUNE_ORDER = ("parts", "universe", "scores")

# 표 → 내보낼 SQL. **유니버스만 실행 열 이름이 `snapshot_id`다** — `run_id`로 적으면
# 아카이브가 통째로 실패한다 (2026-09-23 실측).
EXPORTS: dict[str, str] = {
    "scores": "select * from kss_scores where run_id = %s",
    "parts": "select * from kss_score_parts where run_id = %s",
    "universe": "select * from kss_universe_snapshots where snapshot_id = %s",
    "news": "select * from kss_news_observations where run_id = %s",
    "source_checks": "select * from kss_source_checks where run_id = %s",
}


@dataclass
class FileEntry:
    """아카이브 파일 하나."""

    name: str
    rows: int
    bytes: int
    sha256: str


@dataclass
class Manifest:
    """한 실행의 아카이브 목록."""

    run_id: str
    data_date: str
    profile: str
    files: list[FileEntry] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return sum(f.rows for f in self.files)

    @property
    def bytes(self) -> int:
        return sum(f.bytes for f in self.files)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "data_date": self.data_date, "profile": self.profile,
            "files": [{"name": f.name, "rows": f.rows, "bytes": f.bytes, "sha256": f.sha256}
                      for f in sorted(self.files, key=lambda f: f.name)],
        }

    def snapshot_id(self) -> str:
        """manifest 내용 해시 — 같은 내용이면 같은 id다."""
        blob = json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    """psycopg가 주는 값을 JSON으로 옮긴다 (date·UUID·Decimal)."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict | list | str | int | float | bool) or value is None:
        return value
    return str(value)


def write_jsonl_gz(path: Path, columns: Sequence[str], rows: Iterator[Any]) -> FileEntry:
    """행을 `jsonl.gz`로 쓰고 항목을 만든다.

    **바이트까지 재현 가능하게 쓴다** — `mtime=0`에 더해 파일 이름을 헤더에 넣지 않는다.
    gzip은 기본적으로 원본 파일명을 헤더에 적어서, 같은 내용이라도 이름이 다르면 바이트가 달라진다.
    """
    digest = hashlib.sha256()
    count = 0
    with path.open("wb") as raw, gzip.GzipFile(
        fileobj=raw, mode="wb", mtime=0, filename=""
    ) as fp:
        for row in rows:
            line = json.dumps(
                {c: _jsonable(v) for c, v in zip(columns, row, strict=False)},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            fp.write(line)
            digest.update(line)
            count += 1
    return FileEntry(name=path.name, rows=count, bytes=path.stat().st_size,
                     sha256=digest.hexdigest())


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    """되읽기 — 복원 검증에 쓴다."""
    with gzip.open(path, "rt", encoding="utf-8") as fp:
        return [json.loads(line) for line in fp if line.strip()]


def verify(manifest: Manifest, out_dir: Path) -> tuple[bool, str]:
    """아카이브가 실제로 복원 가능한지 확인한다.

    파일 크기만 보지 않는다 — **되읽어서 행 수와 내용 해시를 다시 만든다.**

    Returns:
        (통과했는가, 사유). 통과면 사유는 빈 문자열이다.
    """
    for entry in manifest.files:
        path = out_dir / entry.name
        if not path.exists():
            return False, f"{entry.name} 없음"
        rows = read_jsonl_gz(path)
        if len(rows) != entry.rows:
            return False, f"{entry.name} 행 수 {len(rows)} ≠ 기록 {entry.rows}"
        digest = hashlib.sha256()
        for row in rows:
            digest.update(
                json.dumps(row, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8") + b"\n")
        if digest.hexdigest() != entry.sha256:
            return False, f"{entry.name} 해시 불일치"
    return True, ""


def retention_cutoffs(
    sessions: Sequence[date],
    keep_scores: int = KEEP_SCORES,
    keep_parts: int = KEEP_PARTS,
    keep_universe: int = KEEP_UNIVERSE,
) -> dict[str, date | None]:
    """갈래별 보존 경계 — **이 날보다 이전**을 지운다 (SPEC §7.4).

    Args:
        sessions: 거래일 달력 (오름차순).
        keep_scores·keep_parts·keep_universe: 남길 거래일 수.

    Returns:
        갈래 → 경계일. 달력이 보존 기간보다 짧으면 None이다 — 지울 것이 없다는 뜻이며,
        **날짜를 지어내지 않는다.**
    """
    def cutoff(keep: int) -> date | None:
        return sessions[-keep] if len(sessions) > keep else None

    return {
        "scores": cutoff(keep_scores),
        "parts": cutoff(keep_parts),
        "universe": cutoff(keep_universe),
    }
