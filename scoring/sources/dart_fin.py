"""다중회사 주요계정 — `fnlttMultiAcnt.json` (SPEC §5.3).

krx-signal-verify@de7aee1(`verify/dart_fin.py`)의 청크·하강 탐색을 이식하고 두 가지를 바꿨다.

1. **접수일 < T인 보고서만 쓴다.** verify는 「지금 제출된 최신」을 고르지만, scoring은 과거 T도
   계산하므로 T 이후 제출분이 섞이면 미래 정보다 (SPEC §5.1 — 날짜만 있는 공시는 다음 거래일 적용).
   접수일은 `rcept_no` 앞 8자리다.
2. **정기보고서(최신)와 사업보고서(최신)를 따로 확보한다.**
   이익률·성장률·부채비율은 최신 정기보고서, ROE·적자 확인은 최신 사업보고서를 쓴다.

기간 말 + `filing_lag_days` 전에는 그 보고서를 부르지 않는다
— 기간이 끝나기 전 제출은 없다(호출 절약).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

ENDPOINT = "fnlttMultiAcnt.json"
CHUNK = 15   # verify 실측 검증값 (15개 요청 → 15개 회사). 조용한 절단을 피해 늘리지 않는다
Q3, HALF, Q1, ANNUAL = "11014", "11012", "11013", "11011"
LABEL = {Q3: "3분기보고서", HALF: "반기보고서", Q1: "1분기보고서", ANNUAL: "사업보고서"}
PERIOD_END = {Q1: (3, 31), HALF: (6, 30), Q3: (9, 30), ANNUAL: (12, 31)}

Fetch = Callable[[str, dict[str, str]], dict[str, Any]]


@dataclass(frozen=True)
class Report:
    """한 회사·한 보고서의 원본 항목 (연결·개별이 섞여 있다)."""

    corp_code: str
    bsns_year: str
    reprt_code: str
    rcept_no: str
    items: tuple[dict[str, Any], ...]

    @property
    def rcept_date(self) -> date:
        """접수일 — 접수번호 앞 8자리."""
        n = self.rcept_no
        return date(int(n[:4]), int(n[4:6]), int(n[6:8]))

    @property
    def label(self) -> str:
        """근거에 적을 말. 「2026년 반기보고서」."""
        return f"{self.bsns_year}년 {LABEL.get(self.reprt_code, self.reprt_code)}"


def candidates(t: date, filing_lag_days: int, years: int = 3) -> list[tuple[str, str]]:
    """T 시점에 부를 만한 (사업연도, 보고서) — 기간 말이 늦은 것부터."""
    out = []
    for y in range(t.year, t.year - years, -1):
        for code in (ANNUAL, Q3, HALF, Q1):
            m, d = PERIOD_END[code]
            if date(y, m, d) + timedelta(days=filing_lag_days) <= t:
                out.append((date(y, m, d), str(y), code))
    out.sort(reverse=True)
    return [(y, code) for _, y, code in out]


def _chunks(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _round(get: Fetch, corps: Sequence[str], year: str, code: str, t: date) -> dict[str, Report]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for chunk in _chunks(list(corps), CHUNK):
        params = {"corp_code": ",".join(chunk), "bsns_year": year, "reprt_code": code}
        payload = get(ENDPOINT, params)
        for it in payload.get("list") or ():
            if corp := str(it.get("corp_code", "")):
                grouped.setdefault(corp, []).append(it)
    out = {}
    for corp, items in grouped.items():
        rcept = str(items[0].get("rcept_no", ""))
        report = Report(corp, year, code, rcept, tuple(items))
        if len(rcept) >= 8 and report.rcept_date < t:   # T 당일·이후 접수분은 쓰지 않는다
            out[corp] = report
    return out


@dataclass(frozen=True)
class FetchResult:
    """회사별 최신 정기보고서·최신 사업보고서와 호출 통계."""

    periodic: dict[str, Report]
    annual: dict[str, Report]
    calls: int
    rounds: list[tuple[str, str, int]]   # (연도, 보고서, 찾은 회사 수)


def fetch_reports(
    corp_codes: Sequence[str], t: date, get: Fetch, filing_lag_days: int = 14
) -> FetchResult:
    """회사들의 T 시점 최신 정기보고서와 최신 사업보고서.

    못 찾은 회사는 빠진다 — 빈 것을 만들어 넣으면 「재무가 0이었다」로 읽힌다.
    """
    calls = 0

    def counted(path: str, params: dict[str, str]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return get(path, params)

    periodic: dict[str, Report] = {}
    annual: dict[str, Report] = {}
    want_p, want_a = set(corp_codes), set(corp_codes)
    rounds = []
    for year, code in candidates(t, filing_lag_days):
        need = want_p | (want_a if code == ANNUAL else set())
        if not need:
            if not want_p and not want_a:
                break
            continue
        found = _round(counted, sorted(need), year, code, t)
        rounds.append((year, code, len(found)))
        for corp, rep in found.items():
            if corp in want_p:
                periodic[corp] = rep
                want_p.discard(corp)
            if code == ANNUAL and corp in want_a:
                annual[corp] = rep
                want_a.discard(corp)
    return FetchResult(periodic=periodic, annual=annual, calls=calls, rounds=rounds)
