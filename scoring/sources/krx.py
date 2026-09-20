"""pykrx 시장 지표 — **배당수익률(DIV)·주당배당금(DPS)**을 시장별 1회로 전 종목 (SPEC §5.3 v2.7).

PER·PBR·EPS·BPS는 쓰지 않는다 — 직전 사업연도 기준이라 실적이 바뀐 종목에서 어긋난다(v2.5).

pykrx 1.2.x는 import할 때 `KRX_ID`/`KRX_PW`로 로그인하고 **로그인 ID를 표준 출력에 찍는다**.
공개 리포의 Actions 로그에 남지 않도록 import와 호출 동안 출력을 가로채 버린다(M2 확인).
"""

from __future__ import annotations

import contextlib
import io
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

MARKETS = ("KOSPI", "KOSDAQ")


@dataclass(frozen=True)
class Dividend:
    """KRX 공표 배당수익률(%)과 주당배당금(원). 최근 결산연도·중간배당 포함."""

    div_yield: float
    dps: float


class KrxError(RuntimeError):
    """pykrx 조회 실패."""


def _quiet() -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
    return stack


def _stock(env: dict[str, str]) -> Any:
    for key in ("KRX_ID", "KRX_PW"):
        if env.get(key):
            os.environ[key] = env[key]
    with _quiet():
        from pykrx import stock
    return stock


def fetch_dividends(t: date, env: dict[str, str], retries: int = 2) -> dict[str, Dividend]:
    """T일 전 종목 배당수익률·주당배당금. 시장 하나라도 빈 응답이면 `KrxError`."""
    stock = _stock(env)
    out: dict[str, Dividend] = {}
    for market in MARKETS:
        df = None
        for attempt in range(retries + 1):
            try:
                with _quiet():
                    df = stock.get_market_fundamental_by_ticker(t.strftime("%Y%m%d"), market=market)
                if df is not None and len(df):
                    break
            except Exception as exc:  # noqa: BLE001 — pykrx는 예외 종류를 보장하지 않는다
                if attempt == retries:
                    raise KrxError(f"{market} 지표 조회 실패: {type(exc).__name__}") from None
            time.sleep(1.0 + attempt)
        if df is None or not len(df):
            raise KrxError(f"{market} 지표 0행 — KRX 로그인·휴장일 확인 필요")
        for ticker, row in df.iterrows():
            out[str(ticker)] = Dividend(div_yield=float(row["DIV"]), dps=float(row["DPS"]))
        time.sleep(0.5)
    return out
