"""상위 공유 DB 읽기 — `ksc_*`·`ksa_*` 일괄 조회 (SPEC §5.1·§6.3, N17).

연결은 `config.connect_upstream`(세션 READ ONLY)만 쓴다. 종목별 수천 번 호출 대신 필요한 열·기간을
한 번에 읽고, 대량 일봉은 서버 측 커서로 흘려 받아 메모리를 아낀다.
상위 W/M 저장 행은 읽지 않는다 (SPEC §5.2).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import psycopg

from scoring.calendar import Calendar, build_calendar, resolve_t
from scoring.models import DailyBar, TickerMeta

Conn = psycopg.Connection[tuple[Any, ...]]
CALENDAR_LOOKBACK_DAYS = 800   # 400거래일 창을 넉넉히 덮는 달력일
FETCH_SIZE = 50_000


@dataclass(frozen=True)
class CalendarInfo:
    """T와 거래일 달력. 일봉 날짜 ∩ 지수 날짜(두 시장 모두 있는 날)."""

    t: date
    cal: Calendar
    mismatch: tuple[date, ...]


@dataclass(frozen=True)
class Snapshot:
    """한 실행의 고정 입력 (T 기준)."""

    t: date
    cal: Calendar
    window_start: date
    tickers: tuple[TickerMeta, ...]
    bars: dict[str, tuple[DailyBar, ...]]
    drifted: frozenset[str]
    upstream_meta: dict[str, Any]
    source_id: str
    rows: int
    load_seconds: float


@dataclass(frozen=True)
class Signal:
    """alerts 신호 한 건 (`ksa_signals`)."""

    d: date
    strategy: str
    ticker: str
    rank_no: int | None
    suppressed: bool
    created_at: datetime


def load_calendar(
    conn: Conn, requested: date | None = None, today: date | None = None
) -> CalendarInfo:
    """거래일 달력과 T를 정한다."""
    start = (today or date.today()) - timedelta(days=CALENDAR_LOOKBACK_DAYS)
    with conn.cursor() as cur:
        cur.execute(
            "select d from ksc_bars where timeframe = 'D' and d >= %s group by d", (start,)
        )
        bar_dates = [r[0] for r in cur.fetchall()]
        cur.execute(
            "select d from ksc_index_bars where d >= %s group by d having count(*) = 2", (start,)
        )
        index_dates = [r[0] for r in cur.fetchall()]
    cal, mismatch = build_calendar(bar_dates, index_dates)
    return CalendarInfo(t=resolve_t(cal, requested), cal=cal, mismatch=mismatch)


def gate_counts(conn: Conn, t: date) -> dict[str, dict[str, int]]:
    """시장별 T일 일봉 기대·저장·유효(v>0) 수."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select tk.market,
                   count(*) as expected,
                   count(b.ticker) as stored,
                   count(*) filter (where b.v > 0) as valid,
                   count(*) filter (where b.a is null and b.ticker is not null) as amount_null
            from ksc_tickers tk
            left join ksc_bars b on b.ticker = tk.ticker and b.timeframe = 'D' and b.d = %s
            group by tk.market
            """,
            (t,),
        )
        return {
            str(m): {"expected": e, "stored": s, "valid": v, "amount_null": n}
            for m, e, s, v, n in cur.fetchall()
        }


def load_meta(conn: Conn) -> dict[str, Any]:
    """`ksc_meta`의 update · universe · drift 값."""
    with conn.cursor() as cur:
        cur.execute(
            "select key, value from ksc_meta where key in ('update', 'universe', 'drift')"
        )
        return {str(k): v for k, v in cur.fetchall()}


def load_snapshot(
    conn: Conn,
    info: CalendarInfo,
    window_sessions: int,
    tickers_filter: frozenset[str] | None = None,
) -> Snapshot:
    """T 기준 창의 종목·일봉·메타를 읽는다."""
    started = time.monotonic()
    window = tuple(s for s in info.cal.sessions if s <= info.t)[-window_sessions:]
    start = window[0]
    with conn.cursor() as cur:
        cur.execute("select ticker, name, market, sector from ksc_tickers order by ticker")
        tickers = tuple(
            TickerMeta(ticker=str(t), name=str(n), market=str(m), sector=str(s or ""))
            for t, n, m, s in cur.fetchall()
            if tickers_filter is None or t in tickers_filter
        )
    meta = load_meta(conn)
    drift = meta.get("drift") or {}
    drifted = frozenset(str(x) for x in (drift.get("drifted") or []))

    wanted = [t.ticker for t in tickers]
    bars: dict[str, list[DailyBar]] = {}
    rows = 0
    with conn.transaction(), conn.cursor(name="kss_bars") as cur:
        cur.itersize = FETCH_SIZE
        cur.execute(
            "select ticker, d, o, h, l, c, v, a from ksc_bars "
            "where timeframe = 'D' and d between %s and %s and ticker = any(%s) "
            "order by ticker, d",
            (start, info.t, wanted),
        )
        for tk, d, o, h, low, c, v, a in cur:
            bars.setdefault(tk, []).append(DailyBar(d=d, o=o, h=h, l=low, c=c, v=v, a=a))
            rows += 1
    return Snapshot(
        t=info.t,
        cal=info.cal,
        window_start=start,
        tickers=tickers,
        bars={k: tuple(v) for k, v in bars.items()},
        drifted=drifted,
        upstream_meta=meta,
        source_id=f"ksc_bars:D:{start.isoformat()}..{info.t.isoformat()}",
        rows=rows,
        load_seconds=round(time.monotonic() - started, 2),
    )


def load_signals(conn: Conn, d: date) -> list[Signal]:
    """기준일 d의 alerts 신호 (signal.d = score.data_date — SPEC §8)."""
    with conn.cursor() as cur:
        cur.execute(
            "select d, strategy, ticker, rank_no, suppressed, created_at "
            "from ksa_signals where d = %s order by strategy, ticker",
            (d,),
        )
        return [
            Signal(d=r[0], strategy=r[1], ticker=r[2], rank_no=r[3], suppressed=bool(r[4]),
                   created_at=r[5])
            for r in cur.fetchall()
        ]
