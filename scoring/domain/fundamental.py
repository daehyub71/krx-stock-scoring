"""기본 36 — 순수 함수 (SPEC §4.3·§4.5·§5.3, v2.5~v2.7).

입력: 시장 데이터(종가·합산 주식 수·배당수익률), 업종 중앙값, 최신 정기보고서·최신 사업보고서.

계약
- **PER·PBR·ROE는 KRX 공표값이 아니라 보고서에서 직접 계산한다**(v2.5~v2.6).
  KRX PER·PBR은 직전 사업연도 이익·자본 기준이라 실적이 바뀐 종목에서 크게 어긋난다
  (2026-09-19 실측: 삼성전자 39.52 → 6.39).
- EPS·BPS는 네이버 정의를 따른다(v2.6):
  `EPS = TTM 순이익 ÷ (보통주+우선주 상장주식수)` · `BPS = 자본총계 ÷ 같은 주식 수`.
  TTM = 직전 사업연도 + 당기 누적 − 전년 동기 누적. 만들 수 없으면 누적을 12개월로 연환산하고
  `basis=annualized`로 남긴다. 우리가 가진 것은 상장주식수이므로 `shares_basis=listed`를 적는다.
- 배당수익률은 **KRX 공표값을 그대로** 쓴다(v2.7). 무배당 0%는 관측이며 결측이 아니다.
- 업종 비교는 같은 시장·업종의 **유효 양수 표본** 중앙값(우리가 계산한 PER·PBR로 만든다).
  표본이 모자라면 시장 중앙값으로 폴백하고 기록한다.
- 금융·보험·리츠(special_sector)에는 매출·영업이익률·부채비율 개념을 그대로 쓰지 않는다
  → `not_applicable`.
- 보고서가 오래되면 `stale`. 오래된 값을 최신처럼 쓰지 않는다.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from scoring.domain.financial import Statement
from scoring.domain.tiers import tier_points
from scoring.domain.universe import UniverseEntry
from scoring.models import MissingReason, Part
from scoring.rules import Rules
from scoring.sources.dart_fin import ANNUAL, HALF, Q1, Q3

AXIS = "fundamental"
ITEMS = ("fund.per", "fund.pbr", "fund.op_margin", "fund.growth", "fund.roe",
         "fund.debt_ratio", "fund.div")
NOT_FOR_SPECIAL = frozenset({"fund.op_margin", "fund.growth", "fund.debt_ratio"})
MONTHS = {Q1: 3, HALF: 6, Q3: 9, ANNUAL: 12}


@dataclass(frozen=True)
class MarketData:
    """T일 시장 값. `shares`는 보통주+우선주 상장주식수 합이다."""

    close: float
    shares: int
    shares_basis: str = "listed"
    div_yield: float | None = None   # KRX 공표 배당수익률 (%)
    dps: float | None = None         # 주당배당금 (원) — 근거 표시용


@dataclass(frozen=True)
class Valuation:
    """한 종목의 TTM 기반 밸류에이션."""

    ttm_net: float | None
    basis: str            # ttm / annual / annualized / none
    shares: int | None
    eps: float | None
    bps: float | None
    per: float | None
    pbr: float | None


@dataclass(frozen=True)
class Median:
    """비교 기준 중앙값."""

    value: float
    n: int
    basis: str   # sector / market


class SectorStats:
    """시장·업종별 PER/PBR 유효 양수 표본 중앙값 (+ 시장 폴백)."""

    def __init__(self, samples: dict[tuple[str, str, str], list[float]], min_samples: int):
        self.samples = samples
        self.min_samples = min_samples
        self.market: dict[tuple[str, str], list[float]] = {}
        for (m, _s, metric), vals in samples.items():
            self.market.setdefault((m, metric), []).extend(vals)

    def lookup(self, market: str, sector: str, metric: str) -> Median | None:
        """업종 표본이 충분하면 업종, 아니면 시장 중앙값."""
        vals = self.samples.get((market, sector, metric), [])
        if len(vals) >= self.min_samples:
            return Median(statistics.median(vals), len(vals), "sector")
        mvals = self.market.get((market, metric), [])
        if mvals:
            return Median(statistics.median(mvals), len(mvals), "market")
        return None

    def rows(self) -> list[tuple[str, str, str, float, int]]:
        """(시장, 업종, 지표, 중앙값, 표본 수) — kss_sector_stats 저장용."""
        return [
            (m, s, k, statistics.median(v), len(v))
            for (m, s, k), v in self.samples.items()
            if v
        ]


def sector_stats(rows: Iterable[tuple[str, str, float, float]], min_samples: int) -> SectorStats:
    """(시장, 업종, PER, PBR) → 유효 양수 표본만 모은 통계."""
    samples: dict[tuple[str, str, str], list[float]] = {}
    for market, sector, per, pbr in rows:
        if per > 0:
            samples.setdefault((market, sector, "per"), []).append(float(per))
        if pbr > 0:
            samples.setdefault((market, sector, "pbr"), []).append(float(pbr))
    return SectorStats(samples, min_samples)


def share_counts(rows: Sequence[tuple[str, int | None]], prefix_len: int) -> dict[str, int]:
    """보통주 티커 → 보통주+우선주 상장주식수 합 (SPEC §5.3 v2.6).

    우선주는 DART corp_code에 없으므로 **티커 앞 `prefix_len`자리**로 묶는다
    (2026-09-20 실측: 우선주 114개가 모두 보통주와 짝이 맞았다).
    """
    common = {t: (n or 0) for t, n in rows if len(t) == 6 and t[5] == "0"}
    for ticker, n in rows:
        if len(ticker) == 6 and ticker[5] != "0":
            base = ticker[:prefix_len] + "0"
            if base in common:
                common[base] += n or 0
    return common


def ttm_net(periodic: Statement | None, annual: Statement | None,
            rules: Rules) -> tuple[float | None, str]:
    """최근 4분기 합산 순이익과 그 근거 (SPEC §5.3 v2.6).

    Returns:
        (값, basis) — basis는 `ttm` / `annual` / `annualized` / `none`.
    """
    if periodic is None:
        return None, "none"
    if periodic.reprt_code == ANNUAL and periodic.net.now is not None:
        return float(periodic.net.now), "annual"
    now, prev = periodic.net.now, periodic.net.prev
    if (annual is not None and annual.net.now is not None and now is not None
            and prev is not None and periodic.net.periods_match):
        return float(annual.net.now + now - prev), "ttm"
    months = MONTHS.get(periodic.reprt_code)
    if rules.section("fundamental").get("annualize_fallback") and now is not None and months:
        return float(now) * 12 / months, "annualized"
    return None, "none"


def valuation(md: MarketData | None, periodic: Statement | None, annual: Statement | None,
              rules: Rules) -> Valuation:
    """TTM 순이익·자본총계와 합산 주식 수로 EPS·BPS·PER·PBR을 만든다."""
    net, basis = ttm_net(periodic, annual, rules)
    shares = md.shares if md and md.shares else None
    eps = net / shares if net is not None and shares else None
    equity = periodic.equity if periodic else None
    bps = equity / shares if equity is not None and shares else None
    close = md.close if md else None
    per = close / eps if close is not None and eps and eps > 0 else None
    pbr = close / bps if close is not None and bps and bps > 0 else None
    return Valuation(ttm_net=net, basis=basis, shares=shares, eps=eps, bps=bps, per=per, pbr=pbr)


@dataclass(frozen=True)
class FundResult:
    """기본 7항목과 재무에서 나온 위험 표지."""

    parts: tuple[Part, ...]
    risk_flags: tuple[str, ...]
    valuation: Valuation


class _Missing(Exception):  # noqa: N818 — 흐름 제어용 내부 신호
    def __init__(self, reason: MissingReason, note: str):
        super().__init__(note)
        self.reason, self.note = reason, note


class _Adverse(Exception):  # noqa: N818
    def __init__(self, note: str, actual: dict[str, Any], risk: str | None = None):
        super().__init__(note)
        self.note, self.actual, self.risk = note, actual, risk


def _fresh(s: Statement | None, t: date, max_age: int, what: str) -> Statement:
    if s is None:
        raise _Missing("unavailable", f"{what} 없음")
    if s.period_end is None or s.period_end < t - timedelta(days=max_age):
        raise _Missing("stale", f"{s.label} — 기간 말 {s.period_end}가 {max_age}일보다 오래됨")
    return s


def score_fundamental(
    entry: UniverseEntry,
    md: MarketData | None,
    stats: SectorStats,
    periodic: Statement | None,
    annual: Statement | None,
    t: date,
    rules: Rules,
) -> FundResult:
    """기본 7항목을 계산한다."""
    cfg = rules.section("fundamental")
    special = "special_sector" in entry.classification
    meta = entry.meta
    risks: list[str] = []
    val = valuation(md, periodic, annual, rules)

    def interim() -> Statement:
        return _fresh(periodic, t, cfg["interim_max_age_days"], "정기보고서")

    def relative(metric: str) -> tuple[float, dict[str, Any]]:
        p = rules.item(f"fund.{metric}").params
        s = interim()                       # 신선도는 두 지표 모두 최신 보고서 기준
        if md is None or not val.shares:
            raise _Missing("unavailable", "종가·주식 수 없음")
        if metric == "per":
            if val.ttm_net is None:
                raise _Missing("unavailable", f"{s.label} 기준 TTM 순이익 없음")
            if val.ttm_net <= 0:
                raise _Adverse("TTM 순손실 — PER 산출 불가",
                               {"ttm_net": val.ttm_net, "basis": val.basis, "report": s.label})
            value, extra = val.per, {"eps": val.eps, "ttm_net": val.ttm_net, "basis": val.basis}
        else:
            if s.equity is None:
                raise _Missing("unavailable", f"{s.label}에 자본총계 없음")
            if s.equity <= 0:
                raise _Adverse("자본총계 ≤ 0 (자본잠식)", {"equity": s.equity},
                               "capital_impairment")
            value, extra = val.pbr, {"bps": val.bps, "equity": s.equity}
        if value is None or value <= 0:
            raise _Missing("invalid_value", f"{metric} 산출 불가")
        med = stats.lookup(meta.market, meta.sector, metric)
        if med is None or med.value <= 0:
            raise _Missing("unavailable", "비교 표본 없음")
        ratio = value / med.value
        return tier_points(ratio, p["tiers"], p["cmp"]), {
            metric: value, "median": med.value, "median_n": med.n, "median_basis": med.basis,
            "ratio": ratio, "shares": val.shares, "shares_basis": md.shares_basis,
            "report": s.label, **extra}

    def op_margin() -> tuple[float, dict[str, Any]]:
        s = interim()
        rev, op = s.revenue.now, s.operating.now
        if rev is None or op is None:
            raise _Missing("unavailable", f"{s.label}에 매출액·영업이익 없음")
        if rev <= 0:
            raise _Missing("invalid_value", "매출액 ≤ 0")
        margin = op / rev * 100
        p = rules.item("fund.op_margin").params
        return tier_points(margin, p["tiers"], p["cmp"]), {
            "op_margin": margin, "revenue": rev, "operating": op, "report": s.label,
            "basis": s.basis}

    def growth() -> tuple[float, dict[str, Any]]:
        s = interim()
        p = rules.item("fund.growth").params
        rev = s.revenue
        if rev.now is None or rev.prev is None or not rev.periods_match or rev.prev <= 0:
            raise _Missing("invalid_value", "매출 누적 전년 비교 불가 (기간·전년 ≤ 0·값 없음)")
        rev_pct = rev.growth_pct
        assert rev_pct is not None
        op = s.operating
        if op.now is None or op.prev is None or not op.periods_match:
            raise _Missing("invalid_value", "영업이익 누적 전년 비교 불가")
        turnaround = op.prev <= 0 < op.now
        if op.prev > 0:
            op_pct = op.growth_pct
            assert op_pct is not None
            op_pts = tier_points(op_pct, p["op_income_tiers"], p["cmp"])
        else:
            op_pct, op_pts = None, float(p["op_turnaround"]) if turnaround else 0.0
        rev_pts = tier_points(rev_pct, p["revenue_tiers"], p["cmp"])
        return rev_pts + op_pts, {"revenue_yoy": rev_pct, "op_yoy": op_pct,
                                  "op_turnaround": turnaround, "revenue_points": rev_pts,
                                  "op_points": op_pts, "report": s.label, "basis": s.basis}

    def roe() -> tuple[float, dict[str, Any]]:
        s = interim()
        avg = s.average_equity
        if val.ttm_net is None or avg is None:
            raise _Missing("unavailable", f"{s.label} 기준 TTM 순이익·평균 자본 없음")
        if avg <= 0:
            raise _Adverse("평균 자본 ≤ 0", {"ttm_net": val.ttm_net, "avg_equity": avg},
                           "capital_impairment")
        value = val.ttm_net / avg * 100
        p = rules.item("fund.roe").params
        return tier_points(value, p["tiers"], p["cmp"]), {
            "roe": value, "ttm_net": val.ttm_net, "avg_equity": avg, "basis": val.basis,
            "report": s.label}

    def debt() -> tuple[float, dict[str, Any]]:
        s = interim()
        if s.liabilities is None or s.equity is None:
            raise _Missing("unavailable", f"{s.label}에 부채·자본총계 없음")
        if s.equity <= 0:
            raise _Adverse("자본총계 ≤ 0 (자본잠식)",
                           {"liabilities": s.liabilities, "equity": s.equity},
                           "capital_impairment")
        ratio = s.liabilities / s.equity * 100
        p = rules.item("fund.debt_ratio").params
        return tier_points(ratio, p["tiers"], p["cmp"]), {
            "debt_ratio": ratio, "report": s.label, "basis": s.basis}

    def dividend() -> tuple[float, dict[str, Any]]:
        # v2.7 — KRX 공표 배당수익률을 그대로 쓴다. 무배당 0%는 관측이다
        if md is None or md.div_yield is None:
            raise _Missing("unavailable", "배당수익률 없음")
        p = rules.item("fund.div").params
        actual: dict[str, Any] = {"div_yield": md.div_yield, "dps": md.dps, "source": "krx"}
        if "리츠" in meta.name:
            actual["note"] = "리츠 — KRX가 12개월 연환산으로 제공"
        return tier_points(md.div_yield, p["tiers"], p["cmp"]), actual

    funcs = {"fund.per": lambda: relative("per"), "fund.pbr": lambda: relative("pbr"),
             "fund.op_margin": op_margin, "fund.growth": growth, "fund.roe": roe,
             "fund.debt_ratio": debt, "fund.div": dividend}
    parts = []
    for item_id in ITEMS:
        mx = rules.item(item_id).max
        if special and item_id in NOT_FOR_SPECIAL:
            parts.append(Part(item_id, AXIS, mx, "not_applicable", None,
                              note="금융·보험·리츠 — 일반기업 산식 미적용 (SPEC §5.3)"))
            continue
        try:
            pts, actual = funcs[item_id]()
            parts.append(Part(item_id, AXIS, mx, "observed", pts, actual=actual))
        except _Missing as m:
            parts.append(Part(item_id, AXIS, mx, "missing", None, missing_reason=m.reason,
                              note=m.note))
        except _Adverse as a:
            parts.append(Part(item_id, AXIS, mx, "adverse_defined", 0.0, actual=a.actual,
                              note=a.note))
            if a.risk and a.risk not in risks:
                risks.append(a.risk)
    return FundResult(parts=tuple(parts), risk_flags=tuple(risks), valuation=val)
