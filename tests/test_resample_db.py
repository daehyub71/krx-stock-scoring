"""실DB 대조 — D 직접 집계가 상위 W의 기간별 최신 행과 일치하는가 (PLAN M0 완료 조건).

M0 실측: 2026-09-07 주는 상위 W 최신 행과 D 집계가 2,766종목 모두 일치했다.
scoring의 resample이 같은 결과를 내야 한다. `pytest -m db`로만 돈다 (읽기 전용 세션).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import pytest

from scoring.calendar import build_calendar
from scoring.config import connect_upstream
from scoring.domain.resample import resample
from scoring.models import DailyBar

WEEK_START = date(2026, 9, 7)
T = date(2026, 9, 18)


@pytest.mark.db
def test_resample_db_matches_upstream_latest_weekly_row() -> None:
    with connect_upstream() as conn, conn.cursor() as cur:
        cur.execute(
            "select ticker, d, o, h, l, c, v, a from ksc_bars "
            "where timeframe = 'D' and d between %s and %s",
            (date(2026, 8, 1), T),
        )
        daily: dict[str, list[DailyBar]] = defaultdict(list)
        for tk, d, o, h, low, c, v, a in cur.fetchall():
            daily[str(tk)].append(DailyBar(d=d, o=o, h=h, l=low, c=c, v=v, a=a))  # type: ignore[arg-type]
        cur.execute(
            "select d from ksc_index_bars where d between %s and %s", (date(2026, 8, 1), T)
        )
        index_dates = [r[0] for r in cur.fetchall()]
        cur.execute(
            "select distinct on (ticker) ticker, d, o, h, l, c, v from ksc_bars "
            "where timeframe = 'W' and d between %s and %s order by ticker, d desc",
            (WEEK_START, date(2026, 9, 13)),
        )
        stored = {str(r[0]): r[1:] for r in cur.fetchall()}

    all_dates = {b.d for bars in daily.values() for b in bars}
    cal, mismatch = build_calendar(all_dates, index_dates)  # type: ignore[arg-type]
    assert mismatch == ()

    compared = mismatched = 0
    for tk, bars in daily.items():
        week = [w for w in resample(bars, "W", T, cal) if w.period_start == WEEK_START]
        if not week or tk not in stored:
            continue
        w = week[0]
        compared += 1
        assert w.is_complete
        if (w.last_trading_date, w.o, w.h, w.l, w.c, w.v) != tuple(stored[tk]):
            mismatched += 1
    assert compared >= 2700
    assert mismatched == 0
