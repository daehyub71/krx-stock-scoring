"""기술 35 — 항목별 손계산·경계·결측 (SPEC §4.5·§5.2).

합성 일봉으로 각 항목의 기대 점수를 손으로 정한다. 실데이터 회귀는 픽스처 테스트가 맡는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import pytest

from scoring.calendar import Calendar
from scoring.domain.technical import score_technical
from scoring.models import DailyBar, Part
from scoring.rules import Rules, load_rules

RULES = load_rules(Path(__file__).resolve().parents[1] / "rules" / "v0.toml")


def sessions(n: int, end: date = date(2026, 9, 18)) -> list[date]:
    out: list[date] = []
    cur = end
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur)
        cur -= timedelta(days=1)
    return out[::-1]


def make(closes: Sequence[float], volumes: list[int] | None = None, end: date = date(2026, 9, 18),
         spread: float = 0.0) -> tuple[list[DailyBar], Calendar]:
    days = sessions(len(closes), end)
    vols = volumes or [1000] * len(closes)
    bars = [
        DailyBar(d=d, o=int(c), h=int(c + spread), l=int(c - spread), c=int(c), v=v, a=None)
        for d, c, v in zip(days, closes, vols, strict=True)
    ]
    return bars, Calendar(sessions=tuple(days))


def parts_by_id(parts: tuple[Part, ...]) -> dict[str, Part]:
    return {p.item: p for p in parts}


def run(bars: list[DailyBar], cal: Calendar, rules: Rules = RULES, **kw: object) -> dict[str, Part]:
    return parts_by_id(score_technical(bars, cal.sessions[-1], cal, rules, **kw).parts)  # type: ignore[arg-type]


# ─────────────────────────── 정배열 11 ───────────────────────────


def test_technical_alignment_full_uptrend() -> None:
    bars, cal = make([1000 + 10 * i for i in range(400)])
    p = run(bars, cal)["tech.alignment"]
    assert (p.state, p.points) == ("observed", 11)


def test_technical_alignment_downtrend_zero() -> None:
    bars, cal = make([10000 - 10 * i for i in range(400)])
    p = run(bars, cal)["tech.alignment"]
    assert (p.state, p.points) == ("observed", 0)


def test_technical_alignment_missing_without_sma120() -> None:
    # 100봉 — SMA120·주봉 20주·월봉 12개월이 없다 → 부분 점수 대신 결측
    bars, cal = make([1000 + i for i in range(100)])
    p = run(bars, cal)["tech.alignment"]
    assert (p.state, p.points, p.missing_reason) == ("missing", None, "insufficient_history")


# ─────────────────────────── 추세 6 ───────────────────────────


def test_technical_trend_golden_cross_recent() -> None:
    # 200봉 100원 뒤 10봉 200원: SMA20이 최근 10봉 안에 SMA60을 상향 돌파,
    # SMA20(T) > SMA20(T−5), 종가 200 > SMA20(=150) → 2+2+2
    bars, cal = make([100.0] * 200 + [200.0] * 10)
    p = run(bars, cal)["tech.trend"]
    assert (p.state, p.points) == ("observed", 6)
    assert p.actual["golden_cross"] is True


def test_technical_trend_flat_zero() -> None:
    # 기울기 0(> 아님), 돌파 없음, 종가 = SMA20(> 아님) → 0
    bars, cal = make([100.0] * 200)
    p = run(bars, cal)["tech.trend"]
    assert (p.state, p.points) == ("observed", 0)


# ─────────────────────────── 거래량 6 ───────────────────────────


def test_technical_volume_ratio_boundary_exactly_1_5() -> None:
    # 앞 15일 100, 마지막 5일 180 → 20일 평균 120, 5일 평균 180 → 1.5 (≥1.5 → 3)
    vols = [100] * 195 + [180] * 5
    bars, cal = make([100.0] * 200, vols)
    p = run(bars, cal)["tech.volume"]
    assert p.actual["ratio"] == pytest.approx(1.5)
    assert p.actual["ratio_points"] == 3


def test_technical_volume_ratio_mid_tier() -> None:
    # 앞 15일 100, 마지막 5일 150 → 150/112.5 = 1.333 → 2
    bars, cal = make([100.0] * 200, [100] * 195 + [150] * 5)
    assert run(bars, cal)["tech.volume"].actual["ratio_points"] == 2


def test_technical_volume_updown_advantage() -> None:
    # 상승일 거래량 300, 하락일 100 → 상승일 우위 3. 비율은 1.0 → 1
    closes, vols = [100.0] * 180, [200] * 180
    for i in range(20):
        closes.append(closes[-1] + (1 if i % 2 == 0 else -1))
        vols.append(300 if i % 2 == 0 else 100)
    bars, cal = make(closes, vols)
    p = run(bars, cal)["tech.volume"]
    assert p.actual["updown_points"] == 3
    assert p.points == p.actual["ratio_points"] + 3


def test_technical_volume_excludes_halted_days() -> None:
    # 20일 창 중 2일 정지(v=0) — 평균에서 빼고 계산 (유효 18 ≥ 16)
    vols = [100] * 180 + [0, 0] + [100] * 18
    bars, cal = make([100.0] * 200, vols)
    p = run(bars, cal)["tech.volume"]
    assert p.state == "observed"
    assert p.actual["valid_days_20"] == 18
    assert p.actual["ratio"] == pytest.approx(1.0)


def test_technical_volume_missing_when_too_many_halted() -> None:
    vols = [100] * 180 + [0] * 5 + [100] * 15
    bars, cal = make([100.0] * 200, vols)
    p = run(bars, cal)["tech.volume"]
    assert (p.state, p.missing_reason) == ("missing", "insufficient_history")


def test_technical_volume_window_is_calendar_not_rows() -> None:
    # 종목 행이 달력보다 적으면(상장 직후) 창을 과거 행으로 늘리지 않는다
    bars, cal = make([100.0] * 200)
    short = bars[-12:]  # 최근 12거래일만 존재
    p = run(short, cal)["tech.volume"]
    assert (p.state, p.missing_reason) == ("missing", "insufficient_history")


# ─────────────────────────── 매물대(근사) 7 ───────────────────────────


def test_technical_volume_profile_above_poc_low_overhead() -> None:
    # 앞 190봉 무관, 창 60봉: 50봉 100원(v10) 뒤 10봉 200원(v1), T 종가 200
    # 구간 폭 5 → POC = 0번 구간(중심 102.5) < 200 → 4, 현재가 위 구간 물량 0 → <10% → 3
    closes = [150.0] * 140 + [100.0] * 50 + [200.0] * 10
    vols = [10] * 140 + [10] * 50 + [1] * 10
    bars, cal = make(closes, vols)
    p = run(bars, cal)["tech.volume_profile"]
    assert (p.state, p.points) == ("observed", 7)


def test_technical_volume_profile_below_poc() -> None:
    # 창 60봉: 10봉 200원(v1) 뒤 50봉 100원(v10), T 종가 100
    # POC 중심 102.5 > 100 → 0, 위 구간 물량 10/510 ≈ 2% → 3
    closes = [150.0] * 140 + [200.0] * 10 + [100.0] * 50
    vols = [10] * 140 + [1] * 10 + [10] * 50
    bars, cal = make(closes, vols)
    p = run(bars, cal)["tech.volume_profile"]
    assert p.actual["poc_points"] == 0
    assert p.actual["overhead_ratio"] == pytest.approx(10 / 510)
    assert p.points == 3


def test_technical_volume_profile_flat_price_invalid() -> None:
    bars, cal = make([100.0] * 200)
    p = run(bars, cal)["tech.volume_profile"]
    assert (p.state, p.missing_reason) == ("missing", "invalid_value")


# ─────────────────────────── RSI·MACD 5 ───────────────────────────


def test_technical_rsi_macd_exponential_growth() -> None:
    # 매일 1% 상승: RSI 100(밴드 밖) → 0, MACD > 시그널이고 히스토그램 확대 → 3
    bars, cal = make([1000 * 1.01**i for i in range(200)])
    p = run(bars, cal)["tech.rsi_macd"]
    assert p.actual["rsi_points"] == 0
    assert p.actual["macd_points"] == 3
    assert p.points == 3


def test_technical_rsi_in_band() -> None:
    # +2 −1 반복 → Wilder RSI ≈ 66.7 (45~70 안) → 2
    closes = [1000.0]
    for i in range(299):
        closes.append(closes[-1] + (2 if i % 2 == 0 else -1))
    bars, cal = make(closes)
    p = run(bars, cal)["tech.rsi_macd"]
    assert 45 <= p.actual["rsi"] <= 70
    assert p.actual["rsi_points"] == 2


def test_technical_rsi_macd_missing_short_history() -> None:
    bars, cal = make([1000 + i for i in range(120)])
    p = run(bars, cal)["tech.rsi_macd"]
    assert (p.state, p.missing_reason) == ("missing", "insufficient_history")


# ─────────────────────────── 공통 ───────────────────────────


def test_technical_no_bar_on_t_all_missing() -> None:
    bars, cal = make([100.0] * 200)
    cal2 = Calendar(sessions=(*cal.sessions, date(2026, 9, 21)))
    res = score_technical(bars, date(2026, 9, 21), cal2, RULES)
    assert all(p.state == "missing" and p.missing_reason == "unavailable" for p in res.parts)
    assert res.passes_screen is None


def test_technical_stale_drift_all_missing() -> None:
    bars, cal = make([1000 + 10 * i for i in range(400)])
    res = score_technical(bars, cal.sessions[-1], cal, RULES, stale=True)
    assert all(p.missing_reason == "stale" for p in res.parts)


def test_technical_ignores_future_bars() -> None:
    bars, cal = make([1000 + 10 * i for i in range(400)])
    t = cal.sessions[-10]
    a = score_technical(bars, t, cal, RULES)
    b = score_technical([x for x in bars if x.d <= t], t, cal, RULES)
    assert a == b


def test_technical_five_parts_with_axis_and_max() -> None:
    bars, cal = make([1000 + 10 * i for i in range(400)])
    res = score_technical(bars, cal.sessions[-1], cal, RULES)
    assert [(p.item, p.max) for p in res.parts] == [
        ("tech.alignment", 11), ("tech.trend", 6), ("tech.volume", 6),
        ("tech.volume_profile", 7), ("tech.rsi_macd", 5),
    ]
    assert all(p.axis == "technical" for p in res.parts)


def test_technical_passes_screen_true_on_strong_uptrend() -> None:
    # 선형 상승 + 거래량 증가 + 매물대 위
    closes = [1000 + 10 * i for i in range(400)]
    vols = [1000] * 380 + [3000] * 20
    bars, cal = make(closes, vols, spread=5)
    res = score_technical(bars, cal.sessions[-1], cal, RULES)
    total = sum(p.points or 0 for p in res.parts)
    assert all(p.state == "observed" for p in res.parts)
    assert res.passes_screen is (total >= 21)
    assert total >= 21


def test_technical_passes_screen_none_when_any_missing() -> None:
    bars, cal = make([1000 + i for i in range(100)])
    assert score_technical(bars, cal.sessions[-1], cal, RULES).passes_screen is None
