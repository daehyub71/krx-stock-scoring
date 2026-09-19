"""유니버스 분류와 집계 — 상태·관측률·자격 (SPEC §1.3·§4.3·§4.4·§11.1).

기대값은 손으로 계산했다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from scoring.domain.aggregate import aggregate
from scoring.domain.universe import classify
from scoring.models import DailyBar, Part, TickerMeta
from scoring.rules import load_rules

RULES = load_rules(Path(__file__).resolve().parents[1] / "rules" / "v0.toml")
T = date(2026, 9, 18)
BAR = DailyBar(d=T, o=100, h=110, l=90, c=105, v=1000, a=None)
HALT = DailyBar(d=T, o=100, h=100, l=100, c=100, v=0, a=None)
TECH = {"tech.alignment": 11, "tech.trend": 6, "tech.volume": 6,
        "tech.volume_profile": 7, "tech.rsi_macd": 5}


def meta(ticker: str = "005930", name: str = "삼성전자", sector: str = "전기·전자",
         market: str = "KOSPI") -> TickerMeta:
    return TickerMeta(ticker=ticker, name=name, market=market, sector=sector)


def entry(**kw: object):  # type: ignore[no-untyped-def]
    m = kw.pop("meta", meta())
    return classify(m, n_daily=kw.pop("n_daily", 700), bar_on_t=kw.pop("bar", BAR),  # type: ignore[arg-type]
                    drifted=kw.pop("drifted", False), rules=RULES)  # type: ignore[arg-type]


def tech_parts(points: Mapping[str, float | None]) -> list[Part]:
    out = []
    for item, mx in TECH.items():
        pts = points.get(item)
        out.append(Part(item=item, axis="technical", max=mx,
                        state="observed" if pts is not None else "missing", points=pts,
                        missing_reason=None if pts is not None else "insufficient_history"))
    return out


# ─────────────────────────── 유니버스 ───────────────────────────


def test_universe_common_stock_clean() -> None:
    e = entry()
    assert (e.excluded_reason, e.classification, e.risk_flags) == (None, (), ())


def test_universe_preferred_by_ticker_last_char() -> None:
    e = entry(meta=meta("005935", "삼성전자우"))
    assert e.excluded_reason == "preferred"


def test_universe_name_ending_u_is_not_preferred() -> None:
    # 이름이 '우'로 끝나도 보통주 코드면 우선주가 아니다 (alerts 2026-08-17 교훈)
    assert entry(meta=meta("006800", "미래에셋대우")).excluded_reason is None


def test_universe_alnum_common_code_not_preferred() -> None:
    assert entry(meta=meta("0126Z0", "삼성에피스홀딩스")).excluded_reason is None


def test_universe_spac() -> None:
    e = entry(meta=meta("123450", "엔에이치스팩30호", "금융", "KOSDAQ"))
    assert e.excluded_reason == "spac"


def test_universe_special_sector_and_reit() -> None:
    assert "special_sector" in entry(meta=meta("105560", "KB금융", "기타금융")).classification
    assert "special_sector" in entry(meta=meta("330590", "롯데리츠", "부동산")).classification


def test_universe_new_listing_and_risks() -> None:
    assert "new_listing" in entry(n_daily=80).classification
    assert entry(bar=HALT).risk_flags == ("suspected_suspension",)
    assert entry(bar=None).risk_flags == ("no_bar_on_t",)
    assert entry(drifted=True).risk_flags == ("price_drift",)


def test_universe_unknown_market() -> None:
    e = entry(meta=meta(market="KONEX"))
    assert "classification_unknown" in e.classification


# ─────────────────────────── 집계: technical 프로필 ───────────────────────────


def test_aggregate_technical_complete_is_scored_without_total() -> None:
    parts = tech_parts({"tech.alignment": 11, "tech.trend": 4, "tech.volume": 4,
                        "tech.volume_profile": 7, "tech.rsi_macd": 0})
    row = aggregate(parts, entry(), RULES, "technical", passes_screen=True)
    assert row.status == "scored"
    assert row.raw_total == 26
    assert row.coverage == 1.0
    # partial_technical — 완성 총점·등급을 만들지 않는다 (SPEC §8)
    assert (row.total, row.estimated_total, row.grade) == (None, None, None)
    assert row.rank_eligible is True
    assert row.axis["technical"]["points"] == 26


def test_aggregate_rsi_only_does_not_get_full_score() -> None:
    # SPEC §11.1: RSI만 남은 종목이 기술 만점·확정 등급·기본 랭킹을 얻지 않는다
    parts = tech_parts({"tech.rsi_macd": 5})
    row = aggregate(parts, entry(), RULES, "technical", passes_screen=None)
    assert row.status == "insufficient_data"
    assert row.coverage == pytest.approx(5 / 35, abs=1e-4)  # DB numeric(5,4)
    assert row.raw_total is None and row.total is None and row.grade is None
    assert row.rank_eligible is False


def test_aggregate_provisional_threshold() -> None:
    # RSI·MACD(5) 결측 → 30/35 = 0.857 ≥ 0.80 → provisional, 랭킹 제외
    parts = tech_parts({"tech.alignment": 11, "tech.trend": 6, "tech.volume": 6,
                        "tech.volume_profile": 7})
    row = aggregate(parts, entry(), RULES, "technical", passes_screen=None)
    assert row.status == "provisional"
    assert row.axis["technical"]["estimate"] == pytest.approx(35 * 30 / 30)
    assert row.rank_eligible is False


def test_aggregate_below_threshold_insufficient() -> None:
    # 정배열(11) 결측 → 24/35 = 0.686 < 0.80
    parts = tech_parts({"tech.trend": 6, "tech.volume": 6, "tech.volume_profile": 7,
                        "tech.rsi_macd": 5})
    assert aggregate(parts, entry(), RULES, "technical", None).status == "insufficient_data"


def test_aggregate_excluded_preferred() -> None:
    parts = tech_parts(dict.fromkeys(TECH, 1.0))
    row = aggregate(parts, entry(meta=meta("005935", "삼성전자우")), RULES, "technical", None)
    assert (row.status, row.rank_eligible, row.raw_total) == ("excluded", False, None)


def test_aggregate_special_sector_and_risk_not_rank_eligible() -> None:
    parts = tech_parts(dict.fromkeys(TECH, 1.0))
    fin_entry = entry(meta=meta("105560", "KB금융", "기타금융"))
    fin = aggregate(parts, fin_entry, RULES, "technical", None)
    halted = aggregate(parts, entry(bar=HALT), RULES, "technical", None)
    assert fin.status == "scored" and fin.rank_eligible is False
    assert halted.status == "scored" and halted.rank_eligible is False
    assert halted.risk_flags == ("suspected_suspension",)


def test_aggregate_signature_tracks_availability() -> None:
    full = aggregate(tech_parts(dict.fromkeys(TECH, 1.0)), entry(), RULES, "technical", None)
    full2 = aggregate(tech_parts(dict.fromkeys(TECH, 2.0)), entry(), RULES, "technical", None)
    part = aggregate(tech_parts({"tech.trend": 1.0}), entry(), RULES, "technical", None)
    assert full.availability_signature == full2.availability_signature
    assert full.availability_signature != part.availability_signature


# ─────────────────────────── 집계: common 프로필 ───────────────────────────

COMMON = {"tech.alignment": ("technical", 11), "tech.trend": ("technical", 6),
          "tech.volume": ("technical", 6), "tech.volume_profile": ("technical", 7),
          "tech.rsi_macd": ("technical", 5), "fund.per": ("fundamental", 7),
          "fund.pbr": ("fundamental", 6), "fund.op_margin": ("fundamental", 7),
          "fund.growth": ("fundamental", 7), "fund.roe": ("fundamental", 5),
          "fund.debt_ratio": ("fundamental", 3), "disc.dart": ("disclosure", 7),
          "flow.foreign": ("flow", 6), "flow.inst": ("flow", 5), "flow.shorting": ("flow", 2)}


def common_parts(points: Mapping[str, float | None]) -> list[Part]:
    return [
        Part(item=i, axis=ax, max=mx, state="missing" if points.get(i) is None else "observed",
             points=points.get(i),
             missing_reason="insufficient_history" if points.get(i) is None else None)
        for i, (ax, mx) in COMMON.items()
    ]


def test_aggregate_common_full_total_is_scaled_to_100() -> None:
    # 원점수 30/90 → 33.33
    pts = dict.fromkeys(COMMON, 2.0)  # 15 × 2 = 30
    row = aggregate(common_parts(pts), entry(), RULES, "common", None)
    assert (row.status, row.raw_total, row.total) == ("scored", 30, 33.33)
    assert row.grade is None  # experimental 규칙은 확정 등급을 내지 않는다


def test_aggregate_common_grade_when_not_experimental() -> None:
    rules = replace(RULES, experimental=False)
    pts = {i: float(mx) for i, (_, mx) in COMMON.items()}  # 만점 90 → 100
    row = aggregate(common_parts(pts), entry(), rules, "common", None)
    assert (row.total, row.grade) == (100.0, "A")
    pts_low = dict.fromkeys(COMMON, 2.0)  # 30/90 → 33.33 → D
    assert aggregate(common_parts(pts_low), entry(), rules, "common", None).grade == "D"


def test_aggregate_common_provisional_estimate() -> None:
    # PER(7)·PBR(6) 결측: 기본 22/35 = 0.629 ≥ 0.6, 전체 77/90 = 0.856 ≥ 0.8 → provisional
    # 모든 관측 항목이 만점의 절반을 받았다고 하면 축 추정 = 축 만점의 절반 → 추정 총점 50.0
    pts = {i: mx / 2 for i, (_, mx) in COMMON.items() if i not in {"fund.per", "fund.pbr"}}
    row = aggregate(common_parts(pts), entry(), RULES, "common", None)
    assert row.status == "provisional"
    assert row.total is None
    assert row.estimated_total == pytest.approx(50.0)
    assert row.coverage == pytest.approx(77 / 90, abs=1e-4)


def test_aggregate_common_axis_zero_available_is_insufficient() -> None:
    # 공시 축 전체 결측: 전체 83/90 = 0.92 이지만 공시 축 0 < 0.6 → 자료 부족
    pts = {i: 1.0 for i in COMMON if i != "disc.dart"}
    row = aggregate(common_parts(pts), entry(), RULES, "common", None)
    assert row.status == "insufficient_data"
    assert row.estimated_total is None


def test_aggregate_adverse_and_no_event_count_as_available() -> None:
    parts = common_parts(dict.fromkeys(COMMON, 1.0))
    parts = [
        replace(p, state="adverse_defined", points=0.0) if p.item == "fund.per"
        else replace(p, state="no_event", points=4.0) if p.item == "disc.dart" else p
        for p in parts
    ]
    row = aggregate(parts, entry(), RULES, "common", None)
    assert row.status == "scored"
    assert row.raw_total == 13 * 1.0 + 0.0 + 4.0


def test_aggregate_rejects_points_above_max() -> None:
    parts = common_parts(dict.fromkeys(COMMON, 3.0))  # 공매도 만점 2 < 3
    with pytest.raises(ValueError):
        aggregate(parts, entry(), RULES, "common", None)
