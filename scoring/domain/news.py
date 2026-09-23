"""뉴스 9 — 관련 기사 3건 × 호재 +3 / 악재 −3 / 중립 0 (SPEC §4.5 · §5.5). 순수 함수.

계약
- 창은 **최근 7일**이고, 그중 **관련성 필터를 통과한 최신 3건**만 센다.
  기사가 3건 미만이면 있는 만큼만 더한다.
- 상태 셋을 섞지 않는다 — 조회 안 함 `not_queried` · 요청 실패 `source_error` ·
  **조회했는데 관련 기사가 없음 `no_event`(0점)**.
- 관련성은 **제목**으로 본다. 최신순 조회는 본문에 종목명이 한 번 나온 시황 기사도 그대로 준다
  (2026-09-23 실측: 「에코프로비엠」 조회 결과 3건 중 3건이 시황·지수 기사였다). 제목에 회사명이
  없으면 그 회사의 사건으로 보지 않는다.
- **이름이 짧은 회사는 문맥을 함께 본다.** 두 글자 이름은 우연히 겹치기 쉬우므로 주식 문맥어가
  같이 있어야 통과한다. 다만 두 글자라는 이유로 전부 버리지는 않는다 (SPEC §5.5).
- 판정은 사전 버전에 매인다. 사전이 다르면 같은 기사라도 다른 점수가 나오며, 그것이 정상이다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from scoring.domain.lexicon import Lexicon
from scoring.models import Part
from scoring.rules import Rules

AXIS = "news"
ITEM = "news.naver"

SHORT_NAME_LEN = 2

# 짧은 이름이 통과하려면 함께 있어야 하는 말 — 주식 기사임을 가리킨다
CONTEXT_TERMS = (
    "주가", "주식", "증권", "코스피", "코스닥", "상장", "실적", "영업이익", "매출",
    "공시", "배당", "주주", "인수", "매출액", "목표가", "투자", "시총", "지분",
)

_WS = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Article:
    """기사 하나 — 수집기가 HTML 태그를 지우고 넘긴다."""

    title: str
    link: str
    published_at: datetime


def _key(text: str) -> str:
    return _WS.sub("", text)


def is_related(title: str, company_name: str) -> bool:
    """제목이 그 회사를 말하는가.

    Args:
        title: 태그를 지운 기사 제목.
        company_name: 종목명.

    Returns:
        회사명이 제목에 있으면 True. 두 글자 이하 이름은 주식 문맥어가 함께 있어야 한다.
    """
    name, hay = _key(company_name), _key(title)
    if not name or name not in hay:
        return False
    if len(name) <= SHORT_NAME_LEN:
        return any(_key(term) in hay for term in CONTEXT_TERMS)
    return True


def judge(title: str, lex: Lexicon) -> tuple[str, str]:
    """기사 제목 하나를 사전에 대본다.

    Returns:
        `(판정, 걸린 낱말)` — 판정은 positive / negative / neutral. 안 걸리면 중립이다.
        우선순위가 낮은 숫자부터 보므로 「상장폐지」가 「수주」보다 먼저 걸린다.
    """
    hay = _key(title)
    for term in sorted((t for t in lex.terms if t.enabled), key=lambda t: (t.priority, t.norm)):
        if term.norm in hay and not any(_key(x) in hay for x in term.exclude):
            return ("positive" if term.polarity == "positive" else "negative", term.term)
    return ("neutral", "")


def score_news(
    articles: Sequence[Article],
    t_end: datetime,
    company_name: str,
    lex: Lexicon,
    rules: Rules,
    *,
    status: str = "observed",
) -> Part:
    """종목 하나의 뉴스 점수를 낸다.

    Args:
        articles: 수집한 기사 (창보다 길어도 된다).
        t_end: 창의 끝 — 정보 마감시각.
        company_name: 종목명 — 관련성 필터에 쓴다.
        lex: 고정된 뉴스 사전.
        rules: 검증된 규칙.
        status: `observed` / `not_queried` / `source_error`.

    Returns:
        뉴스 항목 하나의 Part. 관련 기사가 없으면 `no_event`(0점)다.
    """
    item = rules.item(ITEM)
    p = item.params
    window_days = int(p["window_days"])
    per_article = float(p["per_article"])
    start = t_end - timedelta(days=window_days)

    if status == "not_queried":
        return Part(ITEM, AXIS, item.max, "missing", None,
                    missing_reason="not_queried", note="뉴스를 조회하지 않았다")
    if status == "source_error":
        return Part(ITEM, AXIS, item.max, "missing", None,
                    missing_reason="source_error", note="네이버 검색 요청이 실패했다")

    in_window = [a for a in articles if start <= a.published_at <= t_end]
    related = [a for a in in_window if is_related(a.title, company_name)]
    picked = sorted(related, key=lambda a: a.published_at, reverse=True)[: int(p["max_articles"])]

    judged: list[dict[str, Any]] = []
    points = 0.0
    for a in picked:
        verdict, term = judge(a.title, lex)
        delta = {"positive": per_article, "negative": -per_article}.get(verdict, 0.0)
        points += delta
        judged.append({
            "title": a.title, "link": a.link,
            "published_at": a.published_at.isoformat(),
            "verdict": verdict, "term": term, "points": delta,
        })

    actual: dict[str, Any] = {
        "window": [start.date().isoformat(), t_end.date().isoformat()],
        "fetched": len(articles), "in_window": len(in_window),
        "related": len(related), "used": len(picked),
        "lexicon_version": lex.version,
        "articles": judged,
    }
    if not picked:
        return Part(ITEM, AXIS, item.max, "no_event", 0.0, actual=actual,
                    note="창 안에 관련 기사가 없다")
    return Part(ITEM, AXIS, item.max, "observed", points, actual=actual)
