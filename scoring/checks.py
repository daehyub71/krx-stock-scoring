"""출처별 품질 게이트 — 순수 함수 (SPEC §6.3).

워크플로 success나 MAX(d) 하나로 통과시키지 않는다. 시장별 T일 일봉의 기대·저장·유효 수를 보고,
시장 단위로 기준 미달이면 게시를 막는다(waiting_upstream). 개별 누락은 0으로 보정하지 않는다.
"""

from __future__ import annotations

from collections.abc import Container, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

D_COVERAGE_MIN = 0.99     # D3 초깃값 — 시장별 D 저장 커버리지
AUX_COVERAGE_MIN = 0.95   # D3 초깃값 — 수급·공매도·공시 등 보조 갈래 (SPEC §6.3)
RECOVERY_WINDOW = 5       # 자동 복구 범위 (거래일). 그 이전은 손으로 지정한다 (SPEC §6.4)


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


def gate_aux(source: str, counts: Mapping[str, int], t: date) -> tuple[bool, SourceCheck]:
    """보조 갈래(수급·공매도·공시) 게이트.

    일봉과 다르게 **막지 않는다.** 모자라면 `degraded`로 남기고, 게시는 하되 모자란 사실을 공개한다
    (SPEC §6.3). 뉴스·수급 장애가 공통 랭킹을 세우지 않게 하려는 것이다.

    Args:
        source: 갈래 이름 (`flows` / `shorting` / `disclosures`).
        counts: `expected` · `stored` · `valid`.
        t: 평가 거래일.

    Returns:
        (기준을 넘었는가, 기록할 검사 행). 분모를 모르면 커버리지를 지어내지 않고 `unknown`이다.
    """
    expected = int(counts.get("expected") or 0)
    stored = int(counts.get("stored") or 0)
    valid = int(counts.get("valid") or 0)
    if not expected:
        return False, SourceCheck(source, "ALL", None, stored, valid, None, None, "unknown",
                                  "기대 대상 수를 알 수 없다", t)
    coverage = stored / expected
    passed = coverage >= AUX_COVERAGE_MIN
    return passed, SourceCheck(
        source, "ALL", expected, stored, valid, None, round(coverage, 4),
        "ok" if passed else "degraded",
        None if passed else f"커버리지 {coverage:.4f} < {AUX_COVERAGE_MIN}", t,
    )


def publish_decision(bars_ok: bool, aux_ok: bool) -> str:
    """게시 등급을 정한다 (SPEC §6.4).

    Returns:
        `publish` · `publish_degraded` · `wait`. 일봉 실패가 보조 갈래보다 우선한다 —
        시장 단위 장애면 보조가 멀쩡해도 게시하지 않는다.
    """
    if not bars_ok:
        return "wait"
    return "publish" if aux_ok else "publish_degraded"


def recovery_targets(
    sessions: Sequence[date], published: Container[date], window: int = RECOVERY_WINDOW
) -> list[date]:
    """복구할 거래일 — 최근 `window`거래일 중 게시되지 않은 날 (SPEC §6.4).

    이 큐는 **점수 미게시** 복구용이다. 상위 일봉 누락까지 scoring이 고치지는 않는다.
    """
    return [d for d in sessions[-window:] if d not in published]
