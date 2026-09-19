"""거래일 달력 — T 결정·주/월 기간 키·완성 여부 (SPEC §5.1·§5.2).

거래일은 **일봉 날짜 ∩ 지수 날짜**다. 어느 한쪽에만 있는 날은 `mismatch`로 돌려 품질 게이트가 본다
(M0: 2026-06 이후 불일치 0). 평일·빈 응답만으로 휴장을 판정하지 않는다.

완성 판정: 그 기간 **끝 이후의 거래일이 T 이내에 있거나**, T가 그 기간의 마지막 예정 거래일
(평일 − 알려진 휴장일) 이상이면 완성이다. 휴장 정보가 없으면 보수적으로 미완성으로 본다 —
다음 거래일이 생기는 순간 완성이 된다.
"""

from __future__ import annotations

import calendar as _cal
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from scoring.models import Timeframe


class CalendarError(ValueError):
    """T를 정할 수 없다 (거래일이 아니거나 달력이 비었다)."""


@dataclass(frozen=True)
class Calendar:
    """정렬된 거래일과 알려진 휴장일."""

    sessions: tuple[date, ...]
    holidays: frozenset[date] = field(default_factory=frozenset)


def build_calendar(
    bar_dates: Iterable[date],
    index_dates: Iterable[date],
    holidays: frozenset[date] = frozenset(),
) -> tuple[Calendar, tuple[date, ...]]:
    """일봉·지수 날짜로 달력을 만든다.

    Returns:
        (교집합 달력, 한쪽에만 있는 날짜 — 정렬).
    """
    bars, index = set(bar_dates), set(index_dates)
    sessions = tuple(sorted(bars & index))
    mismatch = tuple(sorted(bars ^ index))
    return Calendar(sessions=sessions, holidays=holidays), mismatch


def resolve_t(cal: Calendar, requested: date | None = None) -> date:
    """평가 거래일 T. 요청이 없으면 마지막 거래일 — 실행 시각(자정 이후 등)과 무관하다."""
    if not cal.sessions:
        raise CalendarError("거래일 달력이 비었다")
    if requested is None:
        return cal.sessions[-1]
    if requested not in set(cal.sessions):
        raise CalendarError(f"{requested}는 거래일이 아니다")
    return requested


def period_start(day: date, tf: Timeframe) -> date:
    """주는 월요일 시작 달력 주, 월은 달력 월의 1일."""
    if tf == "W":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def period_end(start: date, tf: Timeframe) -> date:
    """기간의 마지막 달력일 (일요일 / 말일)."""
    if tf == "W":
        return start + timedelta(days=6)
    return start.replace(day=_cal.monthrange(start.year, start.month)[1])


def _last_scheduled_session(start: date, tf: Timeframe, holidays: frozenset[date]) -> date:
    day = period_end(start, tf)
    while day >= start:
        if day.weekday() < 5 and day not in holidays:
            return day
        day -= timedelta(days=1)
    return start


def is_complete(cal: Calendar, tf: Timeframe, start: date, t: date) -> bool:
    """T 시점에 그 주/월이 완성됐는가."""
    end = period_end(start, tf)
    if any(end < s <= t for s in cal.sessions):
        return True
    return t >= _last_scheduled_session(start, tf, cal.holidays)
