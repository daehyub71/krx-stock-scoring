"""수급 13 — 외국인·기관·공매도 (SPEC §5.4·§4.5).

- NULL은 같은 행 합계 항등식으로 0이 증명될 때만 0으로 본다(M0 실측 근거).
- 연속 순매수는 인접 거래일 자료가 모두 있을 때만 센다 — NULL을 건너뛰어 늘리지 않는다.
- 공매도는 일별 비중의 20거래일 산술평균이며 잔고가 아니다. 경계는 시장별.
기대값은 손으로 정했다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import pytest

from scoring.domain.flow import FlowDay, ShortDay, score_flow
from scoring.models import Part
from scoring.rules import load_rules

RULES = load_rules(Path(__file__).resolve().parents[1] / "rules" / "v0.toml")


def sessions(n: int, end: date = date(2026, 9, 18)) -> list[date]:
    out: list[date] = []
    cur = end
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur)
        cur -= timedelta(days=1)
    return out[::-1]


SESSIONS = tuple(sessions(25))


def flows(values: Sequence[int | None], *, inst: Sequence[int | None] | None = None,
          days: tuple[date, ...] = SESSIONS) -> list[FlowDay]:
    """외국인 순매수 목록으로 하루치 행을 만든다 — 합계가 0이 되도록 개인 몫을 채운다."""
    out = []
    insts = inst or [0] * len(values)
    for d, fgn, ins in zip(days[-len(values):], values, insts, strict=True):
        if fgn is None:
            out.append(FlowDay(d=d, inst=ins, foreign=None, foreign_etc=None,
                               indiv=None, corp_etc=None))
            continue
        out.append(FlowDay(d=d, inst=ins, foreign=fgn, foreign_etc=0,
                           indiv=-(fgn + (ins or 0)), corp_etc=0))
    return out


def run(days: Sequence[FlowDay], shorts: Sequence[ShortDay] | None = None,
        market: str = "KOSPI") -> dict[str, Part]:
    res = score_flow(days, shorts or [], SESSIONS, SESSIONS[-1], market, RULES)
    return {p.item: p for p in res}


def test_flow_foreign_streak_and_cumulative() -> None:
    # 20일 모두 순매수 → 연속 20일(≥5) 3점 + 20일·5일 누적 > 0 → 3점 = 6
    p = run(flows([100] * 20))["flow.foreign"]
    assert (p.state, p.points) == ("observed", 6)
    assert p.actual["streak"] == 20


def test_flow_foreign_streak_tiers() -> None:
    # 앞 17일 매도, 마지막 3일 매수 → 연속 3일(2점). 20일 누적 = −1400 < 0 → 누적 0점
    p = run(flows([-100] * 17 + [100] * 3))["flow.foreign"]
    assert (p.actual["streak"], p.points) == (3, 2)


def test_flow_derived_zero_counts_as_observed() -> None:
    # 외국인·기타외국인이 NULL이지만 나머지 합이 0 → 0원으로 확정 (M0 항등식)
    days = flows([100] * 20)
    days[-1] = FlowDay(d=days[-1].d, inst=0, foreign=None, foreign_etc=None, indiv=0, corp_etc=0)
    p = run(days)["flow.foreign"]
    assert p.state == "observed"
    assert p.actual["derived_zero_days"] == 1
    assert p.actual["streak"] == 0          # 마지막 날 0원은 순매수가 아니다


def test_flow_unresolved_null_breaks_streak() -> None:
    # 나머지 합이 0이 아니면 NULL을 0으로 보지 않는다 → 그날은 결측, 연속은 거기서 끊긴다
    days = flows([100] * 20)
    days[-3] = FlowDay(d=days[-3].d, inst=50, foreign=None, foreign_etc=None,
                       indiv=-10, corp_etc=0)
    p = run(days)["flow.foreign"]
    assert p.actual["streak"] == 2
    assert p.actual["missing_days"] == 1


def test_flow_missing_when_window_too_sparse() -> None:
    # 20일 창에 12일치만 있으면(60% < 80%) 결측
    p = run(flows([100] * 12))["flow.foreign"]
    assert (p.state, p.missing_reason) == ("missing", "insufficient_history")


def test_flow_missing_when_t_day_absent() -> None:
    days = flows([100] * 20)[:-1]
    p = run(days)["flow.foreign"]
    assert (p.state, p.missing_reason) == ("missing", "unavailable")


def test_flow_institution_capped_at_five() -> None:
    # 기관도 같은 방식이지만 상한 5점 (연속 3 + 누적 3 = 6 → 5)
    p = run(flows([0] * 20, inst=[100] * 20))["flow.inst"]
    assert (p.max, p.points) == (5, 5)


def test_flow_turnover_ratio_recorded_when_available() -> None:
    p = run(flows([100] * 20))["flow.foreign"]
    assert "cum20" in p.actual and "cum5" in p.actual


# ─────────────────────────── 공매도 ───────────────────────────


def shorts(ratios: Sequence[float], days: tuple[date, ...] = SESSIONS) -> list[ShortDay]:
    return [ShortDay(d=d, ratio=r, short_vol=1, buy_vol=100)
            for d, r in zip(days[-len(ratios):], ratios, strict=True)]


def test_flow_shorting_market_boundaries() -> None:
    # KOSPI: <0.8 → 2 · <3.0 → 1 · 그 외 0 (2026-09-20 실측 분포의 하위 25%·50%)
    assert run(flows([100] * 20), shorts([0.5] * 20), "KOSPI")["flow.shorting"].points == 2
    assert run(flows([100] * 20), shorts([2.0] * 20), "KOSPI")["flow.shorting"].points == 1
    assert run(flows([100] * 20), shorts([5.0] * 20), "KOSPI")["flow.shorting"].points == 0
    # KOSDAQ은 더 낮은 경계 — 같은 0.5%도 만점
    assert run(flows([100] * 20), shorts([0.3] * 20), "KOSDAQ")["flow.shorting"].points == 2
    assert run(flows([100] * 20), shorts([0.5] * 20), "KOSDAQ")["flow.shorting"].points == 1


def test_flow_shorting_average_hand_computed() -> None:
    p = run(flows([100] * 20), shorts([0.0] * 10 + [4.0] * 10), "KOSPI")["flow.shorting"]
    assert p.actual["avg_ratio"] == pytest.approx(2.0)
    assert p.points == 1


def test_flow_shorting_missing_when_sparse() -> None:
    p = run(flows([100] * 20), shorts([1.0] * 10), "KOSPI")["flow.shorting"]
    assert (p.state, p.missing_reason) == ("missing", "insufficient_history")


def test_flow_parts_order_and_max() -> None:
    parts = score_flow(flows([100] * 20), shorts([1.0] * 20), SESSIONS, SESSIONS[-1], "KOSPI",
                       RULES)
    assert [(p.item, p.max) for p in parts] == [
        ("flow.foreign", 6), ("flow.inst", 5), ("flow.shorting", 2)]
    assert all(p.axis == "flow" for p in parts)
