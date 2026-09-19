"""D → W/M 직접 집계 — 순수 함수 (SPEC §5.2).

상위 `ksc_bars`의 W/M 저장 행은 쓰지 않는다: 진행 중인 주·월이 날마다 새 행으로 남는다
(M0 실측, 초과 80,161행). 여기서는 기간 키 `(timeframe, period_start)`로 묶어
**기간당 한 행**만 낸다.
집계: 첫 시가 · 최고 고가 · 최저 저가 · 마지막 종가 · 거래량 합 ·
거래대금 합(하나라도 NULL이면 NULL).
입력은 `d ≤ T`로 자른다 — T 이후 봉이 섞여도 결과가 바뀌지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from scoring.calendar import Calendar, is_complete, period_start
from scoring.models import DailyBar, PeriodBar, Timeframe


class ResampleError(ValueError):
    """입력 일봉이 계약을 어긴다 (같은 날짜 중복 등)."""


def resample(
    bars: Iterable[DailyBar], tf: Timeframe, t: date, cal: Calendar
) -> tuple[PeriodBar, ...]:
    """한 종목의 일봉을 주/월봉으로 집계한다.

    Args:
        bars: 한 종목의 일봉 (순서 무관).
        tf: "W" 또는 "M".
        t: 평가 거래일 T (`date`). 이후 봉은 버린다.
        cal: 완성 여부 판정용 거래일 달력.

    Returns:
        period_start 오름차순 주/월봉.

    Raises:
        ResampleError: 같은 날짜 일봉이 두 번 있을 때.
    """
    rows = sorted((b for b in bars if b.d <= t), key=lambda b: b.d)
    days = [b.d for b in rows]
    if len(days) != len(set(days)):
        raise ResampleError("같은 날짜 일봉이 중복됐다")

    groups: dict[date, list[DailyBar]] = {}
    for b in rows:
        groups.setdefault(period_start(b.d, tf), []).append(b)

    out = []
    for start in sorted(groups):
        g = groups[start]
        amounts = [b.a for b in g]
        out.append(
            PeriodBar(
                timeframe=tf,
                period_start=start,
                o=g[0].o,
                h=max(b.h for b in g),
                l=min(b.l for b in g),
                c=g[-1].c,
                v=sum(b.v for b in g),
                a=None if None in amounts else sum(a for a in amounts if a is not None),
                last_trading_date=g[-1].d,
                n_days=len(g),
                is_complete=is_complete(cal, tf, start, t),
            )
        )
    return tuple(out)
