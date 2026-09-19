"""출처별 품질 게이트 — 순수 함수 (SPEC §6.3).

워크플로 success나 MAX(d) 하나로 통과시키지 않는다. 시장별 T일 일봉의 기대·저장·유효 수를 보고,
시장 단위로 기준 미달이면 게시를 막는다(waiting_upstream). 개별 누락은 0으로 보정하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

D_COVERAGE_MIN = 0.99   # D3 초깃값 — 시장별 D 저장 커버리지


@dataclass(frozen=True)
class SourceCheck:
    """`kss_source_checks` 한 행."""

    source: str
    market: str
    expected_count: int | None
    stored_count: int | None
    valid_count: int | None
    null_count: int | None
    coverage: float | None
    status: str
    reason: str | None
    max_date: date | None = None


def gate_bars(
    counts: dict[str, dict[str, int]], t: date, meta: dict[str, Any], mismatch: tuple[date, ...]
) -> tuple[bool, list[SourceCheck]]:
    """일봉 D 게이트. (통과 여부, 기록할 검사 행)."""
    checks = []
    ok = True
    for market in ("KOSPI", "KOSDAQ"):
        c = counts.get(market)
        if not c or not c["expected"]:
            checks.append(SourceCheck("bars_d", market, None, None, None, None, None, "failed",
                                      "시장 종목 없음"))
            ok = False
            continue
        coverage = c["stored"] / c["expected"]
        passed = coverage >= D_COVERAGE_MIN
        ok &= passed
        checks.append(SourceCheck(
            "bars_d", market, c["expected"], c["stored"], c["valid"], c["amount_null"],
            round(coverage, 4), "ok" if passed else "failed",
            None if passed else f"T일 저장 커버리지 {coverage:.4f} < {D_COVERAGE_MIN}", t,
        ))

    update = meta.get("update") or {}
    universe = meta.get("universe") or {}
    notes = []
    if str(update.get("updated")) != t.isoformat():
        notes.append(f"ksc_meta.update.updated={update.get('updated')}")
    if str(universe.get("updated")) != t.isoformat():
        notes.append(f"universe.updated={universe.get('updated')}")
    if mismatch:
        notes.append(f"달력 불일치 {len(mismatch)}일(최근 {mismatch[-1]})")
    checks.append(SourceCheck(
        "upstream_meta", "ALL", None, None, None, None, None,
        "degraded" if notes else "ok", "; ".join(notes) or None, t,
    ))
    return ok, checks
