"""도메인 값 객체 — 불변 dataclass. I/O를 모른다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

Timeframe = Literal["W", "M"]
PartState = Literal["observed", "no_event", "adverse_defined", "missing", "not_applicable"]
MissingReason = Literal[
    "not_queried", "insufficient_history", "source_error", "stale", "invalid_value", "unavailable"
]


@dataclass(frozen=True, slots=True)
class DailyBar:
    """일봉 한 행 (상위 `ksc_bars` timeframe='D'). 거래대금 `a`는 NULL일 수 있다."""

    d: date
    o: int
    h: int
    l: int  # noqa: E741 — 상위 열 이름(o/h/l/c/v/a)을 그대로 쓴다
    c: int
    v: int
    a: int | None


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True)
class Part:
    """항목 하나의 점수와 근거 (SPEC §4.3·§7.2 `parts`).

    `points`는 state가 missing이면 None이다. `actual`에는 재계산 가능한 실제 특징값을 담는다.
    """

    item: str
    axis: str
    max: float
    state: PartState
    points: float | None
    missing_reason: MissingReason | None = None
    actual: dict[str, Any] = field(default_factory=dict)
    note: str | None = None


@dataclass(frozen=True)
class TickerMeta:
    """상위 `ksc_tickers` 한 행."""

    ticker: str
    name: str
    market: str
    sector: str
