"""사전 — 편집 원본과 불변 버전 (SPEC §5.5 · §7.2). 순수 함수.

두 갈래를 한 스키마에 담는다.

| scope | 무엇 | 시드 |
|---|---|---|
| `disclosure` | 공시 제목 규칙 — 키워드·제외어·치명 | `flags.RULES` (이식분 + v2.9) |
| `news` | 뉴스 제목 낱말 — 호재/악재 | **`rules/lexicon/news.toml`** (편집 원본) |

**버전은 내용 해시다.** 한 배치가 시작되면 사전이 고정되고, 이후 편집은 다음 실행부터 적용된다.
같은 내용을 다시 저장해도 버전이 늘지 않는다 — 점수를 소급해 덮어쓰지 않기 위한 장치다.

공시 규칙을 사전으로 내보내고(`from_rules`) 다시 규칙표로 되돌릴 수 있다(`to_rules`).
그래야 **과거 버전의 사전으로 그때 점수를 재현**할 수 있다 — 코드 상수만 있으면 못 한다.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from scoring.domain.flags import RULES, Level, Rule

Polarity = Literal["positive", "negative", "fatal"]
Scope = Literal["disclosure", "news"]


@dataclass(frozen=True, slots=True)
class Term:
    """사전 한 항목 (`kss_lexicon` 한 행)."""

    term: str                       # 사람이 읽는 원문 (공시는 규칙 id)
    norm: str                       # 판정 키워드 — 공백 제거
    polarity: Polarity
    scope: Scope
    weight: float = 1.0
    exclude: tuple[str, ...] = ()
    priority: int = 100             # 작을수록 먼저 본다
    on_note: bool = False
    enabled: bool = True
    note: str = ""

    def as_entry(self) -> dict[str, Any]:
        """버전 해시·저장에 쓰는 정규 형태."""
        return {
            "term": self.term, "norm": self.norm, "polarity": self.polarity,
            "scope": self.scope, "weight": self.weight, "exclude": list(self.exclude),
            "priority": self.priority, "on_note": self.on_note, "note": self.note,
        }


@dataclass(frozen=True)
class Lexicon:
    """한 갈래의 활성 사전 스냅샷."""

    scope: Scope
    terms: tuple[Term, ...]
    version: str = field(default="")

    def __post_init__(self) -> None:
        if not self.version:
            object.__setattr__(self, "version", entries_hash(self.entries()))

    def entries(self) -> list[dict[str, Any]]:
        """활성 항목만, 정렬 고정."""
        active = [t.as_entry() for t in self.terms if t.enabled]
        return sorted(active, key=lambda e: (e["priority"], e["norm"], e["polarity"]))


def entries_hash(entries: Sequence[dict[str, Any]]) -> str:
    """사전 버전 — 키 순서·공백에 흔들리지 않는 sha256."""
    blob = json.dumps(list(entries), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── 공시 ────────────────────────────────────────────────────────

_POLARITY_OF: dict[Level, Polarity] = {
    "red": "negative", "amber": "negative", "positive": "positive",
}


def from_rules(rules: Sequence[Rule] = RULES) -> Lexicon:
    """규칙표를 사전 항목으로 내보낸다 — 규칙 하나가 항목 하나다."""
    terms = tuple(
        Term(
            term=r.id,
            norm="|".join(r.keywords),
            polarity="fatal" if r.fatal else _POLARITY_OF[r.level],
            scope="disclosure",
            exclude=r.exclude,
            priority=i,                       # 규칙표의 순서가 곧 우선순위다
            on_note=r.on_note,
            note=r.level,                     # 강등 전 등급 (red / amber / positive)
        )
        for i, r in enumerate(rules)
    )
    return Lexicon(scope="disclosure", terms=terms)


def to_rules(lex: Lexicon) -> tuple[Rule, ...]:
    """사전을 규칙표로 되돌린다 — 과거 버전으로 그때 점수를 재현할 때 쓴다."""
    out: list[Rule] = []
    for t in sorted((t for t in lex.terms if t.enabled), key=lambda t: t.priority):
        level: Level = "amber"
        if t.note in ("red", "amber", "positive"):
            level = t.note  # type: ignore[assignment]
        elif t.polarity == "positive":
            level = "positive"
        out.append(Rule(
            id=t.term, level=level, keywords=tuple(t.norm.split("|")),
            exclude=t.exclude, on_note=t.on_note, fatal=t.polarity == "fatal",
        ))
    return tuple(out)


# ── 뉴스 ────────────────────────────────────────────────────────
#
# 시드 v1. **사전 검증(D7) 전까지 experimental이다** — 제목 300건 라벨링으로 정밀도를 재고
# 그 결과로 확정 등급 게시 여부를 정한다 (SPEC §5.5).
#
# 고른 기준: 제목에 그대로 나오고, 회사의 상태를 가리키며, 시황 기사에 흔하지 않은 말.
# 「상승」·「하락」처럼 시황 기사 제목에 늘 나오는 말은 넣지 않았다 — 관련성 필터를 통과해도
# 종목의 사건이 아니라 그날 시장을 말하는 경우가 많다.

LEXICON_DIR = Path(__file__).resolve().parents[2] / "rules" / "lexicon"
NEWS_FILE = LEXICON_DIR / "news.toml"
DISCLOSURE_FILE = LEXICON_DIR / "disclosure.toml"


class LexiconError(RuntimeError):
    """사전 파일이 계약을 어겼다 — 배치를 세운다. 조용히 넘기지 않는다."""


def load_terms(path: Path, scope: Scope) -> tuple[Term, ...]:
    """편집 원본 TOML을 항목으로 읽는다.

    Args:
        path: 사전 파일.
        scope: `disclosure` / `news`.

    Returns:
        선언 순서대로의 항목. 같은 `norm`이 두 번 나오면 오류다 —
        어느 쪽이 이기는지 모르게 두지 않는다.

    Raises:
        LexiconError: 파일이 없거나, 필수 열이 비었거나, `norm`이 겹친다.
    """
    if not path.exists():
        raise LexiconError(f"사전 파일이 없다: {path.name}")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: list[Term] = []
    seen: set[str] = set()
    for i, raw in enumerate(data.get("term") or []):
        norm = str(raw.get("norm", "")).strip()
        polarity = str(raw.get("polarity", "")).strip()
        if not norm or polarity not in ("positive", "negative", "fatal"):
            raise LexiconError(f"{path.name} {i + 1}번째 항목: norm·polarity가 있어야 한다")
        if norm in seen:
            raise LexiconError(f"{path.name}: `{norm}`이 두 번 있다")
        seen.add(norm)
        out.append(Term(
            term=str(raw.get("term", norm)),
            norm=norm,
            polarity=polarity,  # type: ignore[arg-type]
            scope=scope,
            weight=float(raw.get("weight", 1.0)),
            exclude=tuple(str(x) for x in raw.get("exclude", ())),
            priority=int(raw.get("priority", 100)),
            on_note=bool(raw.get("on_note", False)),
            enabled=bool(raw.get("enabled", True)),
            note=str(raw.get("note", "")),
        ))
    if not out:
        raise LexiconError(f"{path.name}: 항목이 하나도 없다")
    return tuple(out)


def news_lexicon(terms: Sequence[Term] | None = None, path: Path = NEWS_FILE) -> Lexicon:
    """뉴스 사전 스냅샷 — 기본은 `rules/lexicon/news.toml`을 읽는다."""
    chosen = tuple(terms) if terms is not None else load_terms(path, "news")
    return Lexicon(scope="news", terms=chosen)
