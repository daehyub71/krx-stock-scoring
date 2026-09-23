"""공시 제목 판정 — 정규화 · 규칙표 · 등급. **도메인 층 · 순수 함수.**

`krx-signal-verify`의 `flags.py`(212줄)를 **테스트 표본과 함께 이식**했다(PLAN §4).
실표본 3,000건으로 세우고 DART 원문 손검증을 통과한 규칙표라 다시 만들지 않는다.

**scoring이 더한 것 (SPEC v2.9, 2026-09-23 사용자 결정)**

- **호재 5종** — verify는 위험만 보므로 호재 규칙이 없었다. 공시 7점은 기본 4점에서
  호재 +1.5 · 악재 −2로 움직이므로(§4.5) 올려 줄 사건을 정해야 했다.
- **치명 4종** — 상장폐지 · 회생절차 · 횡령/배임 · 감사의견 비적정.
  유효하면 공시 점수를 0으로 덮는다.
  **강등을 거친 뒤에도 🔴인 것만 치명이다** — 모회사가 자회사 회생으로 0점이 되지 않는다.

**규칙표가 곧 SPEC이다.** 바꾸면 SPEC(§5.5)을 먼저 고치고 `tests/test_flags.py`의 `SAMPLES`에
양성·음성 표본을 함께 넣는다 — **표본 없는 규칙은 테스트가 막는다.**

실표본(`tests/fixtures/report_names.txt`, 2026-08-29)에서 배운 것:
- 결정 공시는 `주요사항보고서(유상증자결정)`처럼 **래퍼 안**에 온다 → 부분 일치
- `[기재정정]`·`[첨부정정]`·`[발행조건확정]`·`[첨부추가]`·`[정정제출요구]` 접두가 붙는다
- 제목 뒤에 **공백 여러 칸 + (설명)** 이 붙는다 → note로 분리
- `유상증자결정(종속회사의주요경영사항)`은 **자회사** 공시 — 모회사 희석이 아니다 → 🔴를 🟡로 내린다
- `…결과보고서`·`…해지`·`…행사`·`…해제`는 결정이 아니다 → 제외어
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Level = Literal["red", "amber", "positive"]

_PREFIX = re.compile(r"^\[([^\]]*)\]\s*")
_NOTE_SPLIT = re.compile(r"\s{2,}")
_WS = re.compile(r"\s+")

# 정정을 뜻하는 접두. 그 밖의 접두(발행조건확정·첨부추가)는 떼되 corrected로 보지 않는다.
CORRECTION_MARK = "정정"

# 자회사·종속회사 공시 표시 — 정규화 후(공백 제거) 형태
SUBSIDIARY_MARKERS = ("자회사의주요경영사항", "종속회사의주요경영사항")

REIT_MARK = "리츠"


@dataclass(frozen=True, slots=True)
class Normalized:
    """정규화된 제목."""

    name: str  # 접두·공백 제거, 가운뎃점 통일. 괄호는 남긴다
    corrected: bool  # `[정정]`·`[기재정정]`·`[첨부정정]`·`[정정제출요구]`가 있었는가
    note: str  # 공백 여러 칸 뒤의 괄호 설명 (없으면 "")


@dataclass(frozen=True, slots=True)
class Rule:
    """규칙 하나. `keywords` 중 하나라도 있고 `exclude`가 하나도 없으면 걸린다."""

    id: str
    level: Level
    keywords: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    on_note: bool = False  # True면 note까지 본다 (감사의견은 note에만 온다)
    fatal: bool = False  # 치명 — 강등 뒤에도 🔴면 공시 점수를 0으로 덮는다


@dataclass(frozen=True, slots=True)
class Match:
    """제목 하나의 판정."""

    rule: str
    level: Level
    subsidiary: bool
    fatal: bool


# ── 규칙표 — **편집 원본은 `rules/lexicon/disclosure.toml`이다** ──
#
# 파일에 둔 이유: 규칙을 고치려고 코드를 열지 않아도 되게 하기 위해서다. 순서가 곧 우선순위다.
# 안전장치는 그대로다 — `tests/test_flags.py`의 SAMPLES가 규칙 하나마다 양성·음성 표본을 요구하므로,
# 표본 없이 규칙만 더하면 테스트가 막는다.

RULES_FILE = Path(__file__).resolve().parents[2] / "rules" / "lexicon" / "disclosure.toml"


class RuleFileError(RuntimeError):
    """규칙 파일이 계약을 어겼다 — 조용히 넘기지 않고 배치를 세운다."""


def load_rules_file(path: Path = RULES_FILE) -> tuple[Rule, ...]:
    """규칙 파일을 읽는다. 선언 순서가 판정 순서다.

    Raises:
        RuleFileError: 파일이 없거나, `id`·`level`·`keywords`가 비었거나, `id`가 겹친다.
    """
    if not path.exists():
        raise RuleFileError(f"규칙 파일이 없다: {path.name}")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: list[Rule] = []
    seen: set[str] = set()
    for i, raw in enumerate(data.get("rule") or []):
        rule_id, level = str(raw.get("id", "")).strip(), str(raw.get("level", "")).strip()
        keywords = tuple(str(k) for k in raw.get("keywords", ()))
        if not rule_id or level not in ("red", "amber", "positive") or not keywords:
            raise RuleFileError(f"{path.name} {i + 1}번째 규칙: id·level·keywords가 있어야 한다")
        if rule_id in seen:
            raise RuleFileError(f"{path.name}: 규칙 `{rule_id}`가 두 번 있다")
        seen.add(rule_id)
        out.append(Rule(
            id=rule_id, level=level,  # type: ignore[arg-type]
            keywords=keywords,
            exclude=tuple(str(x) for x in raw.get("exclude", ())),
            on_note=bool(raw.get("on_note", False)),
            fatal=bool(raw.get("fatal", False)),
        ))
    if not out:
        raise RuleFileError(f"{path.name}: 규칙이 하나도 없다")
    return tuple(out)


RULES: tuple[Rule, ...] = load_rules_file()

RULES_BY_ID = {r.id: r for r in RULES}


def normalize(report_nm: str) -> Normalized:
    """제목을 판정 가능한 형태로 만든다.

    Args:
        report_nm: DART `report_nm` 원문.

    Returns:
        접두를 떼고 공백을 전부 지운 `name`, 정정 여부, 뒤에 붙은 설명 `note`.
    """
    s = report_nm.strip()
    corrected = False
    while (m := _PREFIX.match(s)) is not None:
        corrected = corrected or CORRECTION_MARK in m.group(1)
        s = s[m.end() :]
    parts = _NOTE_SPLIT.split(s, 1)
    head = parts[0]
    note = parts[1].strip() if len(parts) > 1 else ""
    if note.startswith("(") and note.endswith(")"):
        note = note[1:-1].strip()
    name = _WS.sub("", head).replace("ㆍ", "·")
    return Normalized(name=name, corrected=corrected, note=note)


def is_reit(company_name: str) -> bool:
    """리츠인가 — 종목명에 `리츠`. 리츠의 유상증자는 자금 조달 구조라 🔴가 아니다."""
    return REIT_MARK in company_name


def match(report_nm: str, company_name: str = "") -> Match | None:
    """제목 하나를 규칙표에 대본다. 안 걸리면 None (참고·무해).

    Args:
        report_nm: DART `report_nm` 원문.
        company_name: 종목명 — 리츠 예외에 쓴다.

    Returns:
        걸린 규칙과 등급. 자회사·종속회사 공시는 🔴를 🟡로, 리츠의 유상증자도 🟡로 내린다.
        **강등되면 치명도 함께 풀린다** — 모회사가 자회사 사정으로 0점이 되지 않는다.
    """
    n = normalize(report_nm)
    note_key = _WS.sub("", n.note).replace("ㆍ", "·")
    subsidiary = any(marker in n.name for marker in SUBSIDIARY_MARKERS)
    for rule in RULES:
        hay = f"{n.name}|{note_key}" if rule.on_note else n.name
        if any(k in hay for k in rule.keywords) and not any(x in hay for x in rule.exclude):
            level: Level = rule.level
            if subsidiary and level == "red":
                level = "amber"
            if rule.id == "rights_issue" and is_reit(company_name):
                level = "amber"
            return Match(
                rule=rule.id, level=level, subsidiary=subsidiary,
                fatal=rule.fatal and level == "red",
            )
    return None
