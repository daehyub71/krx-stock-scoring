"""보고서 탐색 — 접수일 < T 필터·기간 전 호출 생략·사업보고서 분리·청크 (SPEC §5.1·§5.3)."""

from __future__ import annotations

from datetime import date
from typing import Any

from scoring.sources.dart_fin import ANNUAL, HALF, Q1, Q3, candidates, fetch_reports

T = date(2026, 9, 18)


def fake(db: dict[tuple[str, str], dict[str, str]]) -> tuple[Any, list[dict[str, str]]]:
    """db[(연도, 보고서)] = {corp: rcept_no}. 호출 인자를 기록한다."""
    calls: list[dict[str, str]] = []

    def get(path: str, params: dict[str, str]) -> dict[str, Any]:
        calls.append(params)
        got = db.get((params["bsns_year"], params["reprt_code"]), {})
        corps = params["corp_code"].split(",")
        rows = [{"corp_code": c, "rcept_no": got[c], "fs_div": "CFS", "account_nm": "매출액"}
                for c in corps if c in got]
        return {"status": "000" if rows else "013", "list": rows}
    return get, calls


def test_dart_fin_candidates_skip_unfinished_periods() -> None:
    c = candidates(T, filing_lag_days=14)
    assert ("2026", Q3) not in c                    # 9/30 기간 말 전
    assert c[:3] == [("2026", HALF), ("2026", Q1), ("2025", ANNUAL)]


def test_dart_fin_rcept_on_or_after_t_is_ignored() -> None:
    # 반기보고서가 T 당일(9/18) 접수 → 쓰지 않고 1분기로 내려간다
    db = {("2026", HALF): {"A": "20260918000001"}, ("2026", Q1): {"A": "20260515000001"},
          ("2025", ANNUAL): {"A": "20260310000001"}}
    get, _ = fake(db)
    r = fetch_reports(["A"], T, get)
    assert r.periodic["A"].reprt_code == Q1
    assert r.annual["A"].bsns_year == "2025"


def test_dart_fin_annual_fetched_even_when_periodic_found() -> None:
    db = {("2026", HALF): {"A": "20260814000001", "B": "20260814000002"},
          ("2025", ANNUAL): {"A": "20260310000001"}, ("2024", ANNUAL): {"B": "20250310000001"}}
    get, _ = fake(db)
    r = fetch_reports(["A", "B"], T, get)
    assert {k: v.reprt_code for k, v in r.periodic.items()} == {"A": HALF, "B": HALF}
    assert r.annual["A"].bsns_year == "2025" and r.annual["B"].bsns_year == "2024"


def test_dart_fin_missing_company_is_absent_not_empty() -> None:
    get, _ = fake({("2026", HALF): {"A": "20260814000001"}})
    r = fetch_reports(["A", "Z"], T, get)
    assert "Z" not in r.periodic and "Z" not in r.annual


def test_dart_fin_chunks_of_15_and_only_pending() -> None:
    corps = [f"C{i:02d}" for i in range(40)]
    db = {("2026", HALF): {c: "20260814000001" for c in corps[:39]},
          ("2025", ANNUAL): {c: "20260310000001" for c in corps}}
    get, calls = fake(db)
    r = fetch_reports(corps, T, get)
    half_calls = [c for c in calls if c["reprt_code"] == HALF]
    assert [len(c["corp_code"].split(",")) for c in half_calls] == [15, 15, 10]
    q1_calls = [c for c in calls if c["reprt_code"] == Q1]
    assert [c["corp_code"] for c in q1_calls] == ["C39"]      # 못 찾은 회사만 다음 단계로
    assert len(r.periodic) == 40 and len(r.annual) == 40
    assert r.calls == len(calls)
