"""도메인 값 객체 — 불변 dataclass. I/O를 모른다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

Timeframe = Literal["W", "M"]


@dataclass(frozen=True)
class DailyBar:
    """일봉 한 행 (상위 `ksc_bars` timeframe='D'). 거래대금 `a`는 NULL일 수 있다."""

    d: date
    o: int
    h: int
    l: int  # noqa: E741 — 상위 열 이름(o/h/l/c/v/a)을 그대로 쓴다
    c: int
    v: int
    a: int | None


@dataclass(frozen=True)
class PeriodBar:
    """D에서 직접 집계한 주/월봉 (SPEC §5.2). 논리 키는 (timeframe, period_start)."""

    timeframe: Timeframe
    period_start: date
    o: int
    h: int
    l: int  # noqa: E741
    c: int
    v: int
    a: int | None
    last_trading_date: date
    n_days: int
    is_complete: bool
