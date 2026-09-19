"""D → W/M 직접 집계 (SPEC §5.2) · 거래일 달력 (SPEC §5.1).

상위 ksc_bars의 W/M 저장 행은 같은 주/월에 여러 행이 남는다(M0: 초과 80,161행).
scoring은 D에서 직접 집계하며, 같은 기간을 여러 번 처리해도 기간당 1행이어야 한다.
기대값은 손으로 계산했다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scoring.calendar import (
    Calendar,
    CalendarError,
    build_calendar,
    is_complete,
    period_start,
    resolve_t,
)
from scoring.domain.resample import ResampleError, resample
from scoring.models import DailyBar


def d(s: str) -> date:
    return date.fromisoformat(s)


def weekdays(start: str, end: str, skip: frozenset[date] = frozenset()) -> list[date]:
    out, cur, stop = [], d(start), d(end)
    while cur <= stop:
        if cur.weekday() < 5 and cur not in skip:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def bar(day: date, o: int, h: int, low: int, c: int, v: int, a: int | None = 1000) -> DailyBar:
    return DailyBar(d=day, o=o, h=h, l=low, c=c, v=v, a=a)


# 2026-09-14(월)~09-18(금) — 손으로 정한 값
WEEK = [
    bar(d("2026-09-14"), 100, 110, 95, 105, 10),
    bar(d("2026-09-15"), 105, 120, 104, 118, 20),
    bar(d("2026-09-16"), 118, 119, 90, 92, 30),
    bar(d("2026-09-17"), 92, 101, 91, 100, 40),
    bar(d("2026-09-18"), 100, 108, 99, 107, 50),
]
CAL = Calendar(sessions=tuple(weekdays("2026-08-03", "2026-09-18")))


# ─────────────────────────── 달력 ───────────────────────────


def test_calendar_period_start_week_is_monday() -> None:
    assert period_start(d("2026-09-18"), "W") == d("2026-09-14")
    assert period_start(d("2026-09-14"), "W") == d("2026-09-14")
    assert period_start(d("2026-09-20"), "W") == d("2026-09-14")  # 일요일


def test_calendar_period_start_month() -> None:
    assert period_start(d("2026-09-18"), "M") == d("2026-09-01")


def test_calendar_build_reports_mismatch() -> None:
    bars = [d("2026-09-16"), d("2026-09-17"), d("2026-09-18")]
    index = [d("2026-09-17"), d("2026-09-18"), d("2026-09-19")]
    cal, mismatch = build_calendar(bars, index)
    assert cal.sessions == (d("2026-09-17"), d("2026-09-18"))
    assert mismatch == (d("2026-09-16"), d("2026-09-19"))


def test_calendar_resolve_t_defaults_to_last_session() -> None:
    # 실행 시각(자정 이후 등)과 무관하게 마지막 거래일이 T다
    assert resolve_t(CAL) == d("2026-09-18")


def test_calendar_resolve_t_rejects_non_session() -> None:
    with pytest.raises(CalendarError):
        resolve_t(CAL, d("2026-09-19"))  # 토요일
    with pytest.raises(CalendarError):
        resolve_t(Calendar(sessions=()))


def test_calendar_week_incomplete_midweek() -> None:
    assert is_complete(CAL, "W", d("2026-09-14"), d("2026-09-16")) is False


def test_calendar_week_complete_on_friday() -> None:
    assert is_complete(CAL, "W", d("2026-09-14"), d("2026-09-18")) is True


def test_calendar_friday_holiday_week_complete_with_known_holidays() -> None:
    # 2026 추석: 9/24(목)·9/25(금) 휴장 → 그 주는 9/23(수)에 완성
    hol = frozenset({d("2026-09-24"), d("2026-09-25")})
    cal = Calendar(sessions=tuple(weekdays("2026-09-14", "2026-09-23")), holidays=hol)
    assert is_complete(cal, "W", d("2026-09-21"), d("2026-09-23")) is True


def test_calendar_friday_holiday_unknown_is_conservative() -> None:
    # 휴장 정보가 없으면 미완성으로 본다 — 다음 거래일이 나오면 완성
    cal = Calendar(sessions=tuple(weekdays("2026-09-14", "2026-09-23")))
    assert is_complete(cal, "W", d("2026-09-21"), d("2026-09-23")) is False
    hol = frozenset({d("2026-09-24"), d("2026-09-25")})
    later = Calendar(sessions=tuple(weekdays("2026-09-14", "2026-09-28", skip=hol)))
    assert is_complete(later, "W", d("2026-09-21"), d("2026-09-28")) is True


def test_calendar_month_end_holiday() -> None:
    # 2025-12-31 연말 휴장 → 12월은 12/30에 완성
    hol = frozenset({d("2025-12-25"), d("2025-12-31")})
    cal = Calendar(sessions=tuple(weekdays("2025-12-01", "2025-12-30", skip=hol)), holidays=hol)
    assert is_complete(cal, "M", d("2025-12-01"), d("2025-12-30")) is True
    assert is_complete(cal, "M", d("2025-12-01"), d("2025-12-29")) is False


def test_calendar_month_midmonth_incomplete_previous_complete() -> None:
    assert is_complete(CAL, "M", d("2026-09-01"), d("2026-09-18")) is False
    assert is_complete(CAL, "M", d("2026-08-01"), d("2026-09-18")) is True


# ─────────────────────────── 집계 ───────────────────────────


def test_resample_week_ohlcv() -> None:
    (w,) = resample(WEEK, "W", d("2026-09-18"), CAL)
    assert (w.period_start, w.o, w.h, w.l, w.c, w.v, w.a) == (
        d("2026-09-14"), 100, 120, 90, 107, 150, 5000,
    )
    assert w.last_trading_date == d("2026-09-18")
    assert w.n_days == 5
    assert w.is_complete is True


def test_resample_daily_progression_keeps_one_row_per_week() -> None:
    # 월·화·수… 매일 다시 처리해도 같은 주는 한 행 — 상위 W 잔류 결함의 회귀
    for k in range(1, 6):
        t = WEEK[k - 1].d
        rows = resample(WEEK[:k], "W", t, CAL)
        assert len(rows) == 1
        assert rows[0].v == sum(b.v for b in WEEK[:k])
        assert rows[0].c == WEEK[k - 1].c
        assert rows[0].is_complete is (k == 5)


def test_resample_blocks_future_input() -> None:
    # T 이후 봉이 입력에 섞여도 T 스냅샷 결과는 같다
    t = d("2026-09-16")
    assert resample(WEEK, "W", t, CAL) == resample(WEEK[:3], "W", t, CAL)


def test_resample_amount_none_if_any_missing() -> None:
    bars = [*WEEK[:4], bar(d("2026-09-18"), 100, 108, 99, 107, 50, a=None)]
    (w,) = resample(bars, "W", d("2026-09-18"), CAL)
    assert w.a is None


def test_resample_month_spans_weeks() -> None:
    aug = [bar(x, 10, 12, 9, 11, 1) for x in weekdays("2026-08-03", "2026-08-31")]
    rows = resample(aug + WEEK, "M", d("2026-09-18"), CAL)
    assert [r.period_start for r in rows] == [d("2026-08-01"), d("2026-09-01")]
    assert rows[0].n_days == 21 and rows[0].is_complete is True
    assert rows[1].o == 100 and rows[1].is_complete is False


def test_resample_rejects_duplicate_day() -> None:
    with pytest.raises(ResampleError):
        resample([WEEK[0], WEEK[0]], "W", d("2026-09-18"), CAL)


def test_resample_input_order_irrelevant() -> None:
    t = d("2026-09-18")
    assert resample(list(reversed(WEEK)), "W", t, CAL) == resample(WEEK, "W", t, CAL)
