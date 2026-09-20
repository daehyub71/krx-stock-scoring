"""수급 13 — 외국인 6 · 기관 5 · 공매도 비중 2 (SPEC §4.5·§5.4). 순수 함수.

계약
- **NULL은 증명될 때만 0이다.** 투자자 5부류(기관·외국인·기타외국인·개인·기타법인)의
  순매수 합은 0이므로,
  같은 행에서 값이 있는 것들의 합이 0이면 NULL을 0으로 확정하고 `derived_zero`로 센다.
  합이 0이 아니면 그날은 결측이다 (SPEC §5.4, M0 항등식 실측).
- **연속 순매수는 인접 거래일 자료가 모두 있을 때만 센다.** 결측일을 건너뛰어 늘리지 않는다.
- 창은 달력 기준 최근 N거래일이며, 유효일이 하한 미만이면 항목이 결측이다.
- 공매도는 일별 **거래 비중**의 산술평균이며 잔고가 아니다. 경계는 시장별(실측 분포 기반).
- 누적 순매수의 규모 보정값(거래대금 대비)은 근거에만 남긴다 — 점수는 부호로 매긴다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from scoring.domain.tiers import tier_points
from scoring.models import Part
from scoring.rules import Rules

AXIS = "flow"
ITEMS = ("flow.foreign", "flow.inst", "flow.shorting")


@dataclass(frozen=True, slots=True)
class FlowDay:
    """`ksc_investor_flows` 한 행 — 순매수 거래대금(원). None은 그 표에 종목이 없었다는 뜻이다."""

    d: date
    inst: int | None
    foreign: int | None
    foreign_etc: int | None
    indiv: int | None
    corp_etc: int | None


@dataclass(frozen=True, slots=True)
class ShortDay:
    """`ksc_shorting` 한 행 — 공매도 거래 비중(%)."""

    d: date
    short_vol: int
    buy_vol: int
    ratio: float


@dataclass(frozen=True)
class Resolved:
    """하루치 수급 해석 결과."""

    foreign: int | None     # 외국인 + 기타외국인
    inst: int | None
    derived_zero: bool      # NULL을 항등식으로 0이라 확정했는가


def resolve_day(day: FlowDay) -> Resolved:
    """투자자 합계 항등식으로 NULL을 해석한다."""
    values = (day.inst, day.foreign, day.foreign_etc, day.indiv, day.corp_etc)
    missing = [v is None for v in values]
    if not any(missing):
        return Resolved(foreign=(day.foreign or 0) + (day.foreign_etc or 0),
                        inst=day.inst, derived_zero=False)
    if sum(v for v in values if v is not None) == 0:      # 나머지 합이 0 → NULL은 0원
        return Resolved(foreign=(day.foreign or 0) + (day.foreign_etc or 0),
                        inst=day.inst or 0, derived_zero=True)
    return Resolved(foreign=None, inst=None, derived_zero=False)


def _series(days: Sequence[FlowDay], window: Sequence[date]) -> tuple[
        dict[date, Resolved], int]:
    by_day = {d.d: resolve_day(d) for d in days}
    derived = sum(1 for d in window if (r := by_day.get(d)) is not None and r.derived_zero)
    return by_day, derived


def _investor_part(
    item_id: str,
    values: Mapping[date, int | None],
    window: Sequence[date],
    short_window: Sequence[date],
    t: date,
    derived_zero_days: int,
    rules: Rules,
    turnover: Mapping[date, int | None] | None = None,
) -> Part:
    item = rules.item(item_id)
    p = item.params
    cfg = rules.section("flow")
    present = [d for d in window if values.get(d) is not None]
    missing_days = len(window) - len(present)

    def miss(reason: str, note: str) -> Part:
        return Part(item_id, AXIS, item.max, "missing", None, missing_reason=reason,  # type: ignore[arg-type]
                    note=note, actual={"valid_days": len(present), "missing_days": missing_days})

    if values.get(t) is None:
        return miss("unavailable", "T일 수급 자료 없음")
    if len(present) < cfg["min_valid_ratio"] * len(window):
        return miss("insufficient_history",
                    f"{len(window)}일 중 유효 {len(present)}일 < {cfg['min_valid_ratio']:g} 비율")

    streak = 0
    for d in reversed(window):          # 결측일을 만나면 멈춘다 — 건너뛰지 않는다
        v = values.get(d)
        if v is None or v <= 0:
            break
        streak += 1
    cum_long = sum(v for d in window if (v := values.get(d)) is not None)
    cum_short = sum(v for d in short_window if (v := values.get(d)) is not None)

    streak_points = tier_points(streak, p["streak_tiers"], p["streak_cmp"])
    cum_points = float(p["cum_points"]) if cum_long > 0 and cum_short > 0 else 0.0
    points = streak_points + cum_points
    if (cap := p.get("cap")) is not None:
        points = min(points, float(cap))

    actual: dict[str, Any] = {
        "streak": streak, "cum20": cum_long, "cum5": cum_short,
        "streak_points": streak_points, "cum_points": cum_points,
        "valid_days": len(present), "missing_days": missing_days,
        "derived_zero_days": derived_zero_days,
    }
    if turnover is not None:
        # 규모 보정은 참고값이다 — 거래대금이 비면 비운다 (상위 재백필로 비는 경우가 있다)
        amounts: list[int] = [a for d in window if (a := turnover.get(d)) is not None]
        complete = len(amounts) == len(window)
        turnover_sum = sum(amounts) if complete else None
        actual["turnover20"] = turnover_sum
        actual["cum20_over_turnover"] = (
            cum_long / turnover_sum if turnover_sum else None
        )
    return Part(item_id, AXIS, item.max, "observed", points, actual=actual)


def _shorting_part(
    shorts: Sequence[ShortDay], window: Sequence[date], market: str, rules: Rules
) -> Part:
    item = rules.item("flow.shorting")
    p = item.params
    cfg = rules.section("flow")
    by_day = {s.d: s for s in shorts}
    present = [by_day[d] for d in window if d in by_day]
    if len(present) < cfg["min_valid_ratio"] * len(window):
        return Part("flow.shorting", AXIS, item.max, "missing", None,
                    missing_reason="insufficient_history",
                    note=f"{len(window)}일 중 유효 {len(present)}일",
                    actual={"valid_days": len(present)})
    avg = sum(s.ratio for s in present) / len(present)
    tiers = p.get("markets", {}).get(market) or p["tiers"]
    return Part("flow.shorting", AXIS, item.max, "observed",
                tier_points(avg, tiers, p["cmp"]),
                actual={"avg_ratio": avg, "valid_days": len(present), "market": market,
                        "measure": "거래 비중(잔고 아님)"})


def score_flow(
    days: Sequence[FlowDay],
    shorts: Sequence[ShortDay],
    sessions: Sequence[date],
    t: date,
    market: str,
    rules: Rules,
    turnover: Mapping[date, int | None] | None = None,
) -> tuple[Part, ...]:
    """수급 3항목을 계산한다.

    Args:
        days: 종목의 투자자별 순매수 행 (창보다 길어도 된다).
        shorts: 종목의 공매도 행.
        sessions: 거래일 달력 (오름차순).
        t: 평가 거래일.
        market: KOSPI / KOSDAQ — 공매도 경계가 시장별이다.
        rules: 검증된 규칙.
        turnover: 날짜별 거래대금 (규모 보정 참고값, 없어도 된다).
    """
    cfg = rules.section("flow")
    usable = [d for d in sessions if d <= t]
    window = usable[-cfg["window_long"]:]
    short_window = usable[-cfg["window_short"]:]
    by_day, derived = _series(days, window)
    foreign = {d: r.foreign for d, r in by_day.items()}
    inst = {d: r.inst for d, r in by_day.items()}
    return (
        _investor_part("flow.foreign", foreign, window, short_window, t, derived, rules, turnover),
        _investor_part("flow.inst", inst, window, short_window, t, derived, rules, turnover),
        _shorting_part(shorts, window, market, rules),
    )
