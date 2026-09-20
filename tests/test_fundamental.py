"""기본 35 — 항목별 손계산·경계·결측·불리 상태 (SPEC §4.3·§4.5·§5.3, v2.6).

v2.6: PER·PBR·ROE는 KRX 값이 아니라 최신 보고서에서 직접 계산한다.
EPS = TTM 순이익 ÷ (보통주+우선주 상장주식수), BPS = 자본총계 ÷ 같은 주식 수.
TTM = 직전 사업연도 + 당기 누적 − 전년 동기 누적.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from scoring.domain.financial import Flow, Statement
from scoring.domain.fundamental import (
    MarketData,
    score_fundamental,
    sector_stats,
    share_counts,
    ttm_net,
    valuation,
)
from scoring.domain.universe import UniverseEntry
from scoring.models import Part, TickerMeta
from scoring.rules import load_rules

RULES = load_rules(Path(__file__).resolve().parents[1] / "rules" / "v0.toml")
T = date(2026, 9, 18)
META = TickerMeta("000001", "테스트", "KOSPI", "화학")
ENTRY = UniverseEntry(meta=META, excluded_reason=None, classification=(), risk_flags=())
# 화학 업종 PER 중앙값 10, PBR 중앙값 1.0 (표본 5개)
STATS = sector_stats([("KOSPI", "화학", p, b) for p, b in
                      ((8, 0.8), (9, 0.9), (10, 1.0), (11, 1.1), (12, 1.2))], min_samples=5)


def stmt(**kw: object) -> Statement:
    base = Statement(
        label="2026년 반기보고서", reprt_code="11012", rcept_no="20260814000001",
        rcept_date=date(2026, 8, 14), basis="CFS", period_end=date(2026, 6, 30),
        revenue=Flow(1000, 800, True), operating=Flow(150, 100, True), net=Flow(120, 90, True),
        liabilities=500, equity=1000, equity_prev=900,
    )
    return replace(base, **kw)  # type: ignore[arg-type]


ANNUAL = stmt(label="2025년 사업보고서", reprt_code="11011", period_end=date(2025, 12, 31),
              net=Flow(190, 150, True), equity=1000, equity_prev=900)   # ROE 190/950 = 20%


# 주식 수 100주 · 종가 700원 → TTM 순이익 100이면 EPS 1.0, PER 700/10 = 70
MD = MarketData(close=700.0, shares=100, shares_basis="listed", div_yield=3.2, dps=22.4)


def run(md: MarketData | None = MD, periodic: Statement | None = None,
        annual: Statement | None = ANNUAL, entry: UniverseEntry = ENTRY,
        rules: object = RULES) -> dict[str, Part]:
    res = score_fundamental(entry, md, STATS, periodic or stmt(), annual, T, rules)  # type: ignore[arg-type]
    return {p.item: p for p in res.parts}


def test_fundamental_ttm_from_annual_and_cumulative() -> None:
    # TTM = 직전 사업연도 190 + 당기 누적 120 − 전년 동기 누적 90 = 220
    value, basis = ttm_net(stmt(), ANNUAL, RULES)
    assert (value, basis) == (220, "ttm")


def test_fundamental_ttm_uses_annual_itself_when_latest_is_annual() -> None:
    value, basis = ttm_net(ANNUAL, ANNUAL, RULES)
    assert (value, basis) == (190, "annual")


def test_fundamental_ttm_falls_back_to_annualized() -> None:
    # 사업보고서가 없으면 반기 누적 120 × 2 = 240
    value, basis = ttm_net(stmt(), None, RULES)
    assert (value, basis) == (240, "annualized")


def test_fundamental_valuation_eps_bps_hand_computed() -> None:
    # TTM 220 / 100주 = EPS 2.2 → PER 700/2.2 = 318.18
    # BPS 자본 1000 / 100주 = 10 → PBR 700/10 = 70
    v = valuation(MD, stmt(), ANNUAL, RULES)
    assert v.eps == pytest.approx(2.2) and v.per == pytest.approx(318.18, abs=0.01)
    assert v.bps == pytest.approx(10.0) and v.pbr == pytest.approx(70.0)
    assert v.basis == "ttm" and v.shares == 100


def test_fundamental_share_counts_add_preferred() -> None:
    # 삼성전자 모양 — 보통주 + 우선주(앞 5자리 같음)
    counts = share_counts([("005930", 5_846_278_608), ("005935", 802_371_203),
                           ("000660", 728_002_365)], prefix_len=5)
    assert counts["005930"] == 6_648_649_811
    assert counts["000660"] == 728_002_365
    assert "005935" not in counts


def test_fundamental_all_items_hand_computed() -> None:
    # PER 318.18 / 업종 중앙값 10 = 31.8배 → 0 · PBR 70 / 1.0 = 70배 → 0
    # 영업이익률 150/1000 = 15% → 5 · 매출 +25% → 2, 영업이익 +50% → 3 = 5
    # ROE = TTM 220 / 평균자본 950 = 23.2% → 5 · 부채비율 500/1000 = 50% → 3
    p = run()
    assert [(i, p[i].points) for i in ("fund.per", "fund.pbr", "fund.op_margin", "fund.growth",
                                        "fund.roe", "fund.debt_ratio")] == [
        ("fund.per", 0), ("fund.pbr", 0), ("fund.op_margin", 5), ("fund.growth", 5),
        ("fund.roe", 5), ("fund.debt_ratio", 3)]
    assert p["fund.roe"].actual["basis"] == "ttm"
    assert p["fund.per"].actual["median_basis"] == "sector"


def test_fundamental_per_tier_with_cheap_valuation() -> None:
    # 종가 7원 · TTM 220 · 100주 → EPS 2.2 · PER 3.18 / 중앙값 10 = 0.318 ≤ 0.7 → 6(만점)
    p = run(md=MarketData(close=7.0, shares=100))
    assert p["fund.per"].points == 6
    assert p["fund.per"].actual["per"] == pytest.approx(3.18, abs=0.01)


def test_fundamental_negative_ttm_is_adverse() -> None:
    # TTM = −300 + 120 − 90 = −270 → 적자 (PER 산출 불가)
    p = run(annual=replace(ANNUAL, net=Flow(-300, 10, True)))
    assert (p["fund.per"].state, p["fund.per"].points) == ("adverse_defined", 0)
    assert p["fund.per"].actual["ttm_net"] == -270


def test_fundamental_missing_shares_is_unavailable() -> None:
    p = run(md=None)
    assert (p["fund.per"].state, p["fund.per"].missing_reason) == ("missing", "unavailable")
    assert (p["fund.pbr"].state, p["fund.pbr"].missing_reason) == ("missing", "unavailable")
    assert p["fund.roe"].state == "observed"        # ROE는 주식 수가 필요 없다


def test_fundamental_market_fallback_when_sector_small() -> None:
    stats = sector_stats([("KOSPI", "화학", 10, 1.0)] * 2 + [("KOSPI", "기타", 20, 2.0)] * 5,
                         min_samples=5)
    res = score_fundamental(ENTRY, MD, stats, stmt(), ANNUAL, T, RULES)
    per = {p.item: p for p in res.parts}["fund.per"]
    assert per.actual["median_basis"] == "market" and per.actual["median"] == 20


def test_fundamental_op_margin_boundaries() -> None:
    at5 = run(periodic=stmt(operating=Flow(50, 40, True)))["fund.op_margin"]      # 5.0% → 2
    tiny = run(periodic=stmt(operating=Flow(1, 1, True)))["fund.op_margin"]       # 0.1% → 1 (흑자)
    zero = run(periodic=stmt(operating=Flow(0, 1, True)))["fund.op_margin"]       # 0% → 0
    loss = run(periodic=stmt(operating=Flow(-10, 1, True)))["fund.op_margin"]     # 적자 → 0
    assert (at5.points, tiny.points, zero.points, loss.points) == (2, 1, 0, 0)


def test_fundamental_growth_turnaround_and_loss() -> None:
    turn = run(periodic=stmt(operating=Flow(10, -5, True)))["fund.growth"]
    still = run(periodic=stmt(operating=Flow(-10, -5, True)))["fund.growth"]
    # 매출 +25% → 2, 흑자전환(초안) → 3 / 적자 지속 → 0
    assert (turn.points, turn.actual["op_turnaround"]) == (5, True)
    assert still.points == 2


def test_fundamental_growth_missing_on_period_mismatch() -> None:
    p = run(periodic=stmt(revenue=Flow(1000, 800, False)))["fund.growth"]
    assert (p.state, p.missing_reason) == ("missing", "invalid_value")


def test_fundamental_capital_impairment() -> None:
    res = score_fundamental(ENTRY, MD, STATS, stmt(equity=-100), ANNUAL, T, RULES)
    p = {x.item: x for x in res.parts}
    assert (p["fund.debt_ratio"].state, p["fund.debt_ratio"].points) == ("adverse_defined", 0)
    assert (p["fund.pbr"].state, p["fund.pbr"].points) == ("adverse_defined", 0)
    assert res.risk_flags == ("capital_impairment",)


def test_fundamental_stale_reports() -> None:
    old = stmt(period_end=date(2025, 6, 30))            # T보다 445일 전 > 270
    p = run(periodic=old, annual=replace(ANNUAL, period_end=date(2024, 12, 31)))  # 626일 > 548
    for item in ("fund.op_margin", "fund.growth", "fund.debt_ratio", "fund.roe"):
        assert (p[item].state, p[item].missing_reason) == ("missing", "stale"), item


def test_fundamental_no_reports_unavailable() -> None:
    # 보고서가 없으면 재무 6항목은 결측. 배당은 KRX 값이라 그대로 관측된다
    res = score_fundamental(ENTRY, MD, STATS, None, None, T, RULES)
    by_id = {p.item: p for p in res.parts}
    assert {p.missing_reason for p in res.parts if p.item != "fund.div"} == {"unavailable"}
    assert by_id["fund.div"].state == "observed"


def test_fundamental_special_sector_not_applicable() -> None:
    fin = UniverseEntry(meta=TickerMeta("105560", "KB금융", "KOSPI", "기타금융"),
                        excluded_reason=None, classification=("special_sector",), risk_flags=())
    p = run(entry=fin)
    for item in ("fund.op_margin", "fund.growth", "fund.debt_ratio"):
        assert (p[item].state, p[item].points) == ("not_applicable", None), item
    assert p["fund.roe"].state == "observed"


def test_fundamental_roe_boundaries() -> None:
    # TTM = 65 + 120 − 90 = 95 · 평균자본 950 → 10.0% → 3
    at10 = run(annual=replace(ANNUAL, net=Flow(65, 1, True)))["fund.roe"]
    assert at10.points == 3
    assert at10.actual["roe"] == pytest.approx(10.0)


def test_fundamental_parts_order_and_max() -> None:
    res = score_fundamental(ENTRY, MD, STATS, stmt(), ANNUAL, T, RULES)
    assert [(p.item, p.max) for p in res.parts] == [
        ("fund.per", 6), ("fund.pbr", 5), ("fund.op_margin", 7), ("fund.growth", 6),
        ("fund.roe", 5), ("fund.debt_ratio", 3), ("fund.div", 4)]
    assert sum(p.max for p in res.parts) == 36


def test_fundamental_dividend_tiers() -> None:
    # v2.7 — KRX 공표 배당수익률 그대로. 5%/3%/1% → 4/2/1, 무배당 0%는 관측 0점
    def div(y: float | None) -> Part:
        return run(md=MarketData(close=700.0, shares=100, div_yield=y, dps=1.0))["fund.div"]

    assert [div(v).points for v in (5.0, 3.2, 1.0, 0.0)] == [4, 2, 1, 0]
    assert div(0.0).state == "observed"
    none = run(md=MarketData(close=700.0, shares=100))["fund.div"]
    assert (none.state, none.missing_reason) == ("missing", "unavailable")


def test_fundamental_dividend_applies_to_special_sector() -> None:
    reit = UniverseEntry(meta=TickerMeta("330590", "롯데리츠", "KOSPI", "부동산"),
                         excluded_reason=None, classification=("special_sector",), risk_flags=())
    p = run(entry=reit)
    assert p["fund.div"].state == "observed"
    assert "리츠" in str(p["fund.div"].actual.get("note"))
