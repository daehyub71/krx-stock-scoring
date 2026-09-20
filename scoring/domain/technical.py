"""기술 35 — 순수 함수 (SPEC §4.5·§5.2).

한 종목의 일봉(≤ T)과 거래일 달력으로 기술 5항목의 `Part`를 만든다.

계약
- 창은 **달력 기준 최근 N거래일**이다. 종목 행이 모자라면 창을 과거 행으로 늘리지 않고 결측이다.
- 항목에 필요한 입력이 하나라도 없으면 그 항목 전체가 `missing` —
  일부 하위 조건만으로 점수를 내지 않는다.
- 거래정지 의심일(v=0)은 **거래량·매물대 계산에서만** 뺀다. 가격 지표는 원본 행을 그대로 쓴다.
- 주/월봉은 D에서 직접 집계한 **T까지 완성된 봉**만 쓴다(상위 W/M 저장 행 미사용).
- 규칙 값(창·단계·배점)은 모두 `rules`에서 읽는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from scoring.calendar import Calendar
from scoring.domain.indicators import macd, rsi_wilder, sma
from scoring.domain.resample import resample
from scoring.domain.tiers import tier_points
from scoring.models import DailyBar, MissingReason, Part
from scoring.rules import Rules

AXIS = "technical"
ITEMS = ("tech.alignment", "tech.trend", "tech.volume", "tech.volume_profile", "tech.rsi_macd")


@dataclass(frozen=True)
class TechResult:
    """기술 5항목 결과와 스크리닝 표지."""

    parts: tuple[Part, ...]
    passes_screen: bool | None


class _MissingError(Exception):
    def __init__(self, reason: MissingReason, note: str, actual: dict[str, Any] | None = None):
        super().__init__(note)
        self.reason = reason
        self.note = note
        self.actual = actual or {}


def _last(xs: Sequence[float | None], back: int = 0) -> float:
    idx = len(xs) - 1 - back
    value = xs[idx] if idx >= 0 else None
    if value is None:
        raise _MissingError("insufficient_history", f"값 없음(뒤에서 {back})")
    return value


@dataclass(frozen=True)
class _Ctx:
    t: date
    window: tuple[date, ...]        # 달력 기준 최근 N거래일 (≤ T)
    bars: tuple[DailyBar, ...]      # 창 안 종목 일봉 (≤ T)
    by_day: dict[date, DailyBar]
    closes: tuple[float, ...]
    cal: Calendar
    rules: Rules
    min_valid_ratio: float

    def last_sessions(self, n: int) -> tuple[date, ...]:
        if len(self.window) < n:
            raise _MissingError("insufficient_history", f"달력 {len(self.window)} < {n}거래일")
        return self.window[-n:]

    def valid_bars(self, days: Sequence[date]) -> list[DailyBar]:
        """거래가 있었던 날(v>0)의 봉만. 유효일이 하한 미만이면 결측."""
        valid = [b for d in days if (b := self.by_day.get(d)) is not None and b.v > 0]
        need = self.min_valid_ratio * len(days)
        if len(valid) < need:
            raise _MissingError(
                "insufficient_history", f"{len(days)}일 중 유효 {len(valid)}일 < {need:g}"
            )
        return valid


def _alignment(ctx: _Ctx) -> tuple[float, dict[str, Any]]:
    params = ctx.rules.item("tech.alignment").params
    actual: dict[str, Any] = {}
    points = 0.0
    for short, long, pts in params["daily_pairs"]:
        a, b = _last(sma(ctx.closes, short)), _last(sma(ctx.closes, long))
        ok = a > b
        actual[f"d_sma{short}>sma{long}"] = ok
        points += pts if ok else 0
    ws, wl, wpts = params["weekly_pair"]
    weekly = [w.c for w in resample(ctx.bars, "W", ctx.t, ctx.cal) if w.is_complete]
    a, b = _last(sma(weekly, ws)), _last(sma(weekly, wl))
    actual[f"w_sma{ws}>sma{wl}"] = a > b
    points += wpts if a > b else 0
    mn, mpts = params["monthly_close_above"]
    monthly = [m.c for m in resample(ctx.bars, "M", ctx.t, ctx.cal) if m.is_complete]
    close_m, avg_m = _last(monthly), _last(sma(monthly, mn))
    actual[f"m_close>sma{mn}"] = close_m > avg_m
    points += mpts if close_m > avg_m else 0
    return points, actual


def _trend(ctx: _Ctx) -> tuple[float, dict[str, Any]]:
    p = ctx.rules.item("tech.trend").params
    s_short = sma(ctx.closes, p["golden_cross"][0])
    s_long = sma(ctx.closes, p["golden_cross"][1])
    slope_series = sma(ctx.closes, p["slope_sma"])
    window = p["golden_window"]
    if len(ctx.closes) < p["golden_cross"][1] + window:
        raise _MissingError("insufficient_history", "골든크로스 창 부족")
    slope_up = _last(slope_series) > _last(slope_series, p["slope_lookback"])
    n = len(ctx.closes)
    golden = False
    for k in range(n - window, n):
        prev_s, prev_l, cur_s, cur_l = s_short[k - 1], s_long[k - 1], s_short[k], s_long[k]
        if prev_s is None or prev_l is None or cur_s is None or cur_l is None:
            raise _MissingError("insufficient_history", "골든크로스 SMA 부족")
        if prev_s <= prev_l and cur_s > cur_l:
            golden = True
    close_above = ctx.closes[-1] > _last(sma(ctx.closes, p["close_above_sma"]))
    points = (
        (p["slope_points"] if slope_up else 0)
        + (p["golden_points"] if golden else 0)
        + (p["close_points"] if close_above else 0)
    )
    return float(points), {"slope_up": slope_up, "golden_cross": golden, "close_above": close_above}


def _volume(ctx: _Ctx) -> tuple[float, dict[str, Any]]:
    p = ctx.rules.item("tech.volume").params
    short_n, long_n = p["ratio"]
    long_days = ctx.last_sessions(long_n)
    valid_long = ctx.valid_bars(long_days)
    valid_short = ctx.valid_bars(long_days[-short_n:])
    avg_long = sum(b.v for b in valid_long) / len(valid_long)
    avg_short = sum(b.v for b in valid_short) / len(valid_short)
    if avg_long <= 0:
        raise _MissingError("invalid_value", "20일 평균 거래량 0")
    ratio = avg_short / avg_long
    ratio_points = tier_points(ratio, p["ratio_tiers"], p["ratio_cmp"])

    # 상승일·하락일 평균 거래량 — 전일 종가는 종목의 직전 봉
    index = {b.d: i for i, b in enumerate(ctx.bars)}
    ups, downs = [], []
    for b in ctx.valid_bars(ctx.last_sessions(p["updown_window"])):
        i = index[b.d]
        if i == 0:
            continue
        prev = ctx.bars[i - 1].c
        if b.c > prev:
            ups.append(b.v)
        elif b.c < prev:
            downs.append(b.v)
    up_avg = sum(ups) / len(ups) if ups else 0.0
    down_avg = sum(downs) / len(downs) if downs else 0.0
    updown_points = float(p["updown_points"]) if up_avg > down_avg else 0.0
    return ratio_points + updown_points, {
        "ratio": ratio,
        "ratio_points": ratio_points,
        "valid_days_20": len(valid_long),
        "up_days": len(ups),
        "down_days": len(downs),
        "up_avg_volume": up_avg,
        "down_avg_volume": down_avg,
        "updown_points": updown_points,
    }


def _volume_profile(ctx: _Ctx) -> tuple[float, dict[str, Any]]:
    p = ctx.rules.item("tech.volume_profile").params
    valid = ctx.valid_bars(ctx.last_sessions(p["window"]))
    lo = min(b.l for b in valid)
    hi = max(b.h for b in valid)
    if hi <= lo:
        raise _MissingError("invalid_value", "창 안 가격 범위 0")
    bins = int(p["bins"])
    width = (hi - lo) / bins

    def idx(price: float) -> int:
        return min(max(int((price - lo) / width), 0), bins - 1)

    volume = [0.0] * bins
    for b in valid:
        volume[idx((b.h + b.l + b.c) / 3)] += b.v
    total = sum(volume)
    if total <= 0:
        raise _MissingError("invalid_value", "창 안 거래량 0")
    poc = max(range(bins), key=lambda k: (volume[k], -k))  # 동률이면 낮은 가격 구간
    poc_price = lo + (poc + 0.5) * width
    close = ctx.closes[-1]
    overhead = sum(volume[idx(close) + 1 :]) / total  # 현재가 구간보다 위 구간의 물량
    poc_points = float(p["poc_points"]) if close > poc_price else 0.0
    overhead_points = tier_points(overhead, p["overhead_tiers"], p["overhead_cmp"])
    return poc_points + overhead_points, {
        "poc_price": poc_price,
        "close": close,
        "overhead_ratio": overhead,
        "poc_points": poc_points,
        "overhead_points": overhead_points,
        "approximation": "ohlcv_hlc3",
    }


def _rsi_macd(ctx: _Ctx) -> tuple[float, dict[str, Any]]:
    p = ctx.rules.item("tech.rsi_macd").params
    tech = ctx.rules.section("technical")
    need = max(tech["rsi_min_bars"], tech["macd_min_bars"])
    if len(ctx.closes) < need:
        raise _MissingError("insufficient_history", f"{len(ctx.closes)}봉 < 워밍업 {need}봉")
    rsi = _last(rsi_wilder(ctx.closes, p["rsi_period"]))
    fast, slow, sig = p["macd"]
    line, signal, hist = macd(ctx.closes, fast, slow, sig)
    m, s, h, h_prev = _last(line), _last(signal), _last(hist), _last(hist, 1)
    lo, hi = p["rsi_band"]
    rsi_points = float(p["rsi_points"]) if lo <= rsi <= hi else 0.0
    macd_points = float(p["macd_points"]) if m > s and h > h_prev else 0.0
    return rsi_points + macd_points, {
        "rsi": rsi,
        "macd": m,
        "signal": s,
        "hist": h,
        "hist_prev": h_prev,
        "rsi_points": rsi_points,
        "macd_points": macd_points,
    }


_FUNCS = {
    "tech.alignment": _alignment,
    "tech.trend": _trend,
    "tech.volume": _volume,
    "tech.volume_profile": _volume_profile,
    "tech.rsi_macd": _rsi_macd,
}


def _missing_all(rules: Rules, reason: MissingReason, note: str) -> TechResult:
    parts = tuple(
        Part(item=i, axis=AXIS, max=rules.item(i).max, state="missing", points=None,
             missing_reason=reason, note=note)
        for i in ITEMS
    )
    return TechResult(parts=parts, passes_screen=None)


def score_technical(
    bars: Sequence[DailyBar],
    t: date,
    cal: Calendar,
    rules: Rules,
    *,
    stale: bool = False,
) -> TechResult:
    """기술 5항목을 계산한다.

    Args:
        bars: 한 종목의 일봉 (순서 무관, T 이후 봉은 무시).
        t: 평가 거래일.
        cal: 거래일 달력.
        rules: 검증된 규칙.
        stale: 수정주가 드리프트 등으로 가격이 낡았다고 알려진 종목 — 전 항목 결측.

    Returns:
        항목 순서가 고정된 5개 `Part`와 `passes_screen`.
    """
    if stale:
        return _missing_all(rules, "stale", "수정주가 드리프트 — 상위 재백필 대상")
    tech = rules.section("technical")
    window = tuple(s for s in cal.sessions if s <= t)[-tech["window_sessions"] :]
    start = window[0] if window else t
    in_window = tuple(sorted((b for b in bars if start <= b.d <= t), key=lambda b: b.d))
    if not in_window or in_window[-1].d != t:
        return _missing_all(rules, "unavailable", "T일 일봉 없음")

    ctx = _Ctx(
        t=t,
        window=window,
        bars=in_window,
        by_day={b.d: b for b in in_window},
        closes=tuple(float(b.c) for b in in_window),
        cal=cal,
        rules=rules,
        min_valid_ratio=tech["min_valid_ratio"],
    )
    parts = []
    for item_id in ITEMS:
        item = rules.item(item_id)
        try:
            points, actual = _FUNCS[item_id](ctx)
            parts.append(Part(item=item_id, axis=AXIS, max=item.max, state="observed",
                              points=points, actual=actual))
        except _MissingError as m:
            parts.append(Part(item=item_id, axis=AXIS, max=item.max, state="missing",
                              points=None, missing_reason=m.reason, actual=m.actual, note=m.note))

    passes: bool | None = None
    if all(p.state == "observed" for p in parts):
        screen = rules.section("screen")
        a, b, c = (sma(ctx.closes, n)[-1] for n in screen["sma_order"])
        if a is not None and b is not None and c is not None:
            total = sum(p.points or 0.0 for p in parts)
            passes = total >= screen["tech_min"] and a > b > c
    return TechResult(parts=tuple(parts), passes_screen=passes)
