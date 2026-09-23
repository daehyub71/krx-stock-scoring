"""공시 7 — 30일 창의 사건으로 기본 4점을 올리고 내린다 (SPEC §4.5 · §5.5). 순수 함수.

계약
- 창은 **T를 포함한 최근 30달력일**이다(거래일이 아니다). T 이후 공시는 그날 알 수 없었으므로 뺀다.
- 기본 4점에서 호재 +1.5 · 악재 −2. 악재는 🔴·🟡을 가리지 않는다 — 등급은 근거에만 남긴다.
- **치명(fatal)이 하나라도 유효하면 0점**이고 상태는 `adverse_defined`다. 호재로 상쇄되지 않는다.
- 규칙에 걸린 공시가 하나도 없으면 `no_event` + 기본 4점이다. **조회하지 않았으면 결측**이며,
  둘을 섞지 않는다 (SPEC §4.3).
- **정정본은 원본과 한 사건이다.** `[기재정정]`이 붙은 공시는 같은 (규칙, 정규화 제목)의 원본이
  창 안에 있으면 세지 않는다. 원본이 창 밖으로 밀려났으면 정정본이 그 사건을 대표한다.
- **원본 두 건은 두 사건이다.** 같은 제목이라도 정정이 아니면 서로 다른 계약으로 본다 —
  「단일판매·공급계약체결」을 한 달에 두 번 낸 회사의 두 계약을 하나로 합치지 않는다 (SPEC §5.5).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from scoring.domain.flags import match, normalize
from scoring.models import Part
from scoring.rules import Rules

AXIS = "disclosure"
ITEM = "disc.dart"


@dataclass(frozen=True, slots=True)
class DisclosureRow:
    """`kss_disclosures` 한 행 — 판정에 필요한 만큼만."""

    rcept_no: str
    rcept_dt: date
    report_nm: str


def score_disclosure(
    rows: Sequence[DisclosureRow],
    t: date,
    company_name: str,
    rules: Rules,
    *,
    queried: bool = True,
) -> Part:
    """종목 하나의 공시 점수를 낸다.

    Args:
        rows: 종목의 공시 (창보다 길어도 된다).
        t: 평가 거래일.
        company_name: 종목명 — 리츠 예외에 쓴다.
        rules: 검증된 규칙.
        queried: DART를 실제로 조회했는가. False면 결측이다.

    Returns:
        공시 항목 하나의 Part.
    """
    item = rules.item(ITEM)
    p = item.params
    window_days = int(p["window_days"])
    start = t - timedelta(days=window_days - 1)

    if not queried:
        return Part(
            ITEM, AXIS, item.max, "missing", None,
            missing_reason="not_queried", note="DART 공시를 조회하지 않았다",
        )

    hits: list[tuple[DisclosureRow, Any, tuple[str, str], bool]] = []
    for r in rows:
        if not (start <= r.rcept_dt <= t):
            continue
        m = match(r.report_nm, company_name)
        if m is None:
            continue
        n = normalize(r.report_nm)
        hits.append((r, m, (m.rule, n.name), n.corrected))

    # 원본이 창 안에 있는 사건은 정정본을 세지 않는다. 원본이 밀려났으면 정정본이 대표한다
    originals = {key for _, _, key, corrected in hits if not corrected}
    events: list[dict[str, Any]] = []
    represented: set[tuple[str, str]] = set()
    for r, m, key, corrected in sorted(hits, key=lambda h: (h[0].rcept_dt, h[0].rcept_no)):
        if corrected and (key in originals or key in represented):
            continue
        represented.add(key)
        events.append({
            "rcept_no": r.rcept_no, "rcept_dt": r.rcept_dt.isoformat(),
            "rule": m.rule, "level": m.level, "fatal": m.fatal,
            "subsidiary": m.subsidiary, "corrected": corrected, "report_nm": r.report_nm,
        })

    positives = [e for e in events if e["level"] == "positive"]
    negatives = [e for e in events if e["level"] in ("red", "amber")]
    fatal_rules = [e["rule"] for e in events if e["fatal"]]

    actual: dict[str, Any] = {
        "window": [start.isoformat(), t.isoformat()],
        "event_count": len(events),
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "fatal_rules": fatal_rules,
        "events": events,
    }

    if fatal_rules:
        actual["base"] = float(p["base"])
        return Part(
            ITEM, AXIS, item.max, "adverse_defined", float(p["fatal_points"]),
            actual=actual, note=f"치명 사건 {', '.join(fatal_rules)} — 공시 점수를 0으로 덮었다",
        )

    raw = float(p["base"]) + float(p["positive"]) * len(positives)
    raw += float(p["negative"]) * len(negatives)
    points = min(max(raw, float(p["floor"])), float(item.max))
    actual["raw"] = round(raw, 2)
    if not events:
        return Part(ITEM, AXIS, item.max, "no_event", points, actual=actual,
                    note="창 안에 규칙에 걸린 공시가 없다")
    return Part(ITEM, AXIS, item.max, "observed", points, actual=actual)
