"""재무 추출 — 주요계정 원본 → 점수에 쓸 사실 (SPEC §5.3). 순수 함수.

계정 정규화(괄호 주석·공백 제거)와 「연결 우선·혼합 금지」는 krx-signal-verify@de7aee1
(`verify/financial.py`)에서 이식했다. 비율·기간 검증은 새로 썼다.

- **누적치**: 분기·반기 보고서의 손익은 `thstrm_amount`가 3개월치다(2026-09-19 실측).
  누적은 `thstrm_add_amount`, 전년 누적은 `frmtrm_add_amount`. 사업보고서는 `thstrm_amount`.
- **기간 검증**: 당기·전기 누적 기간 길이가 맞고 전기가 1년 앞일 때만 증가율을 낸다.
  당기는 누적인데 전기가 분기치로 대체됐으면 비교하지 않는다.
- **연결 우선**: 연결(CFS)에 값이 하나라도 있으면 그 기준만 쓴다.
  없는 계정을 개별에서 가져오지 않는다.
- 빈칸·`-`는 None, `0`은 0이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from scoring.sources.dart_fin import ANNUAL, Report

_PAREN = re.compile(r"\([^)]*\)")
_WS = re.compile(r"\s+")
_DATE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")

REVENUE, OPERATING, NET = "매출액", "영업이익", "당기순이익"
LIABILITIES, EQUITY = "부채총계", "자본총계"
TOLERANCE_DAYS = 7


def normalize(name: str) -> str:
    """`영업이익(손실)` → `영업이익`."""
    return _WS.sub("", _PAREN.sub("", name))


def amount(raw: Any) -> int | None:
    """쉼표 낀 금액 → int. 빈칸·`-`·읽을 수 없는 값은 None. 0은 살린다."""
    try:
        return int(str(raw).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _span(text: Any) -> tuple[date, date] | None:
    found = _DATE.findall(str(text or ""))
    if not found:
        return None
    ds = [date(int(y), int(m), int(d)) for y, m, d in found]
    return (ds[0], ds[-1])


@dataclass(frozen=True)
class Flow:
    """손익 계정의 당기·전기 누적치와 비교 가능 여부."""

    now: int | None
    prev: int | None
    periods_match: bool

    @property
    def growth_pct(self) -> float | None:
        """전기 대비 증가율(%). 전기 ≤ 0이거나 기간이 안 맞으면 None."""
        if self.now is None or self.prev is None or self.prev <= 0 or not self.periods_match:
            return None
        return (self.now - self.prev) / self.prev * 100


@dataclass(frozen=True)
class Statement:
    """한 보고서에서 읽은 사실."""

    label: str
    reprt_code: str
    rcept_no: str
    rcept_date: date
    basis: str
    period_end: date | None
    revenue: Flow
    operating: Flow
    net: Flow
    liabilities: int | None
    equity: int | None
    equity_prev: int | None

    @property
    def average_equity(self) -> float | None:
        """기초·기말 평균 자본 (사업보고서 ROE용)."""
        if self.equity is None or self.equity_prev is None:
            return None
        return (self.equity + self.equity_prev) / 2


def _pick(items: tuple[dict[str, Any], ...], basis: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for it in sorted(items, key=lambda x: int(str(x.get("ord") or 0) or 0)):
        if str(it.get("fs_div", "")) == basis:
            out.setdefault(normalize(str(it.get("account_nm", ""))), it)
    return out


def _flow(row: dict[str, Any] | None, interim: bool) -> Flow:
    if row is None:
        return Flow(None, None, False)
    now_add, prev_add = amount(row.get("thstrm_add_amount")), amount(row.get("frmtrm_add_amount"))
    now = now_add if interim and now_add is not None else amount(row.get("thstrm_amount"))
    prev = prev_add if interim and prev_add is not None else amount(row.get("frmtrm_amount"))
    mixed = interim and (now_add is None) != (prev_add is None)   # 누적 대 분기치 비교 금지
    a, b = _span(row.get("thstrm_dt")), _span(row.get("frmtrm_dt"))
    ok = False
    if a and b and not mixed:
        len_a, len_b = (a[1] - a[0]).days, (b[1] - b[0]).days
        shift = (a[0] - b[0]).days
        ok = abs(len_a - len_b) <= TOLERANCE_DAYS and abs(shift - 365) <= TOLERANCE_DAYS + 1
    return Flow(now, prev, ok)


def read_statement(report: Report) -> Statement:
    """보고서 하나 → 사실. 연결이 있으면 연결만, 없으면 개별만."""
    rows, basis = _pick(report.items, "CFS"), "CFS"
    if not rows:
        rows, basis = _pick(report.items, "OFS"), "OFS"
    interim = report.reprt_code != ANNUAL
    end = None
    for name in (REVENUE, OPERATING, NET, EQUITY, LIABILITIES):
        span = _span(rows[name].get("thstrm_dt")) if name in rows else None
        if span:
            end = span[1]
            break
    eq = rows.get(EQUITY)
    return Statement(
        label=report.label,
        reprt_code=report.reprt_code,
        rcept_no=report.rcept_no,
        rcept_date=report.rcept_date,
        basis=basis,
        period_end=end,
        revenue=_flow(rows.get(REVENUE), interim),
        operating=_flow(rows.get(OPERATING), interim),
        net=_flow(rows.get(NET), interim),
        liabilities=amount(rows[LIABILITIES].get("thstrm_amount")) if LIABILITIES in rows else None,
        equity=amount(eq.get("thstrm_amount")) if eq else None,
        equity_prev=amount(eq.get("frmtrm_amount")) if eq else None,
    )
