"""재무 추출 — 누적 기간·연결 우선·평균 자본 (SPEC §5.3).

응답 모양은 2026-09-19 실측(삼성전자 2026 반기·2025 사업보고서)을 본떴다. 금액은 손으로 정했다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from scoring.domain.financial import read_statement
from scoring.sources.dart_fin import ANNUAL, HALF, Q1, Report


def item(fs: str, sj: str, name: str, now: str | None, prev: str | None, *,
         now_add: str | None = None, prev_add: str | None = None,
         now_dt: str = "2026.01.01 ~ 2026.06.30", prev_dt: str = "2025.01.01 ~ 2025.06.30",
         prev2: str | None = None) -> dict[str, Any]:
    return {"fs_div": fs, "sj_div": sj, "account_nm": name, "thstrm_amount": now,
            "thstrm_add_amount": now_add, "frmtrm_amount": prev, "frmtrm_add_amount": prev_add,
            "thstrm_dt": now_dt if sj == "IS" else "2026.06.30 현재",
            "frmtrm_dt": prev_dt if sj == "IS" else "2025.12.31 현재",
            "bfefrmtrm_amount": prev2, "ord": "1", "rcept_no": "20260814003699"}


def half_report(fs: str = "CFS") -> Report:
    # 2분기 3개월 매출 170, 반기 누적 300 / 전년 2분기 70, 전년 반기 누적 150
    return Report(corp_code="00126380", bsns_year="2026", reprt_code=HALF,
                  rcept_no="20260814003699", items=(
        item(fs, "IS", "매출액", "170", "70", now_add="300", prev_add="150"),
        item(fs, "IS", "영업이익(손실)", "90", "5", now_add="150", prev_add="10"),
        item(fs, "IS", "당기순이익(손실)", "70", "4", now_add="120", prev_add="8"),
        item(fs, "BS", "부채총계", "180", "130"),
        item(fs, "BS", "자본총계", "600", "450"),
    ))


def test_financial_interim_uses_cumulative_amounts() -> None:
    s = read_statement(half_report())
    assert (s.revenue.now, s.revenue.prev) == (300, 150)    # 3개월치 170/70이 아니다
    assert (s.operating.now, s.operating.prev) == (150, 10)
    assert s.revenue.periods_match is True
    assert s.basis == "CFS"
    assert s.period_end == date(2026, 6, 30)
    assert s.rcept_date == date(2026, 8, 14)
    assert (s.liabilities, s.equity) == (180, 600)


def test_financial_q1_without_add_amount_falls_back_to_quarter() -> None:
    r = Report(corp_code="1", bsns_year="2026", reprt_code=Q1, rcept_no="20260515000001", items=(
        item("CFS", "IS", "매출액", "100", "80", now_dt="2026.01.01 ~ 2026.03.31",
             prev_dt="2025.01.01 ~ 2025.03.31"),
        item("CFS", "BS", "자본총계", "10", "9"),
    ))
    s = read_statement(r)
    assert (s.revenue.now, s.revenue.prev) == (100, 80)
    assert s.revenue.periods_match is True


def test_financial_period_mismatch_detected() -> None:
    # 전기 기간이 3개월, 당기가 6개월 → 비교 불가
    r = Report(corp_code="1", bsns_year="2026", reprt_code=HALF, rcept_no="20260814000001", items=(
        item("CFS", "IS", "매출액", "170", "70", now_add="300", prev_add=None,
             prev_dt="2025.04.01 ~ 2025.06.30"),
    ))
    assert read_statement(r).revenue.periods_match is False


def test_financial_prefers_consolidated_and_does_not_mix() -> None:
    items = half_report("OFS").items + (item("CFS", "IS", "매출액", "999", "1", now_add="999",
                                              prev_add="1"),)
    s = read_statement(Report(corp_code="1", bsns_year="2026", reprt_code=HALF,
                              rcept_no="20260814000001", items=items))
    assert s.basis == "CFS"
    assert s.revenue.now == 999
    assert s.equity is None          # 연결에 자본총계가 없으면 개별 값을 가져오지 않는다


def test_financial_separate_when_no_consolidated() -> None:
    s = read_statement(half_report("OFS"))
    assert s.basis == "OFS" and s.revenue.now == 300


def test_financial_annual_average_equity() -> None:
    r = Report(corp_code="1", bsns_year="2025", reprt_code=ANNUAL, rcept_no="20260310000001",
               items=(
        item("CFS", "IS", "당기순이익", "44", "30", now_dt="2025.01.01 ~ 2025.12.31",
             prev_dt="2024.01.01 ~ 2024.12.31"),
        item("CFS", "BS", "자본총계", "440", "400"),
    ))
    s = read_statement(r)
    assert s.net.now == 44
    assert s.equity == 440 and s.equity_prev == 400
    assert s.average_equity == pytest.approx(420)
    assert s.period_end == date(2025, 12, 31)


def test_financial_blank_amount_is_none_not_zero() -> None:
    r = Report(corp_code="1", bsns_year="2026", reprt_code=HALF, rcept_no="20260814000001", items=(
        item("CFS", "IS", "매출액", "", "-", now_add=None, prev_add=None),
        item("CFS", "IS", "영업이익", "0", "0", now_add="0", prev_add="0"),
    ))
    s = read_statement(r)
    assert s.revenue.now is None
    assert s.operating.now == 0
