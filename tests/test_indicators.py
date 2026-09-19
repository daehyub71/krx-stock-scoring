"""이동평균·RSI(Wilder)·MACD — 손계산 골든 값 (SPEC §5.2).

기대값은 짧은 수열로 손으로 계산해 주석에 과정을 남겼다.
"""

from __future__ import annotations

import pytest

from scoring.domain.indicators import ema, macd, rsi_wilder, sma


def approx_list(xs: list[float | None]) -> list[object]:
    return [None if x is None else pytest.approx(x, abs=1e-9) for x in xs]


def test_indicators_sma() -> None:
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_indicators_sma_short_series_all_none() -> None:
    assert sma([1, 2], 3) == [None, None]


def test_indicators_ema_seeded_with_sma() -> None:
    # alpha = 2/(3+1) = 0.5, 시드 = SMA(1,2,3) = 2
    # 3: 0.5·4 + 0.5·2 = 3 → 4 → 5
    assert ema([1, 2, 3, 4, 5, 6], 3) == approx_list([None, None, 2.0, 3.0, 4.0, 5.0])


def test_indicators_ema_skips_leading_none() -> None:
    assert ema([None, None, 1, 2, 3, 4], 3) == approx_list([None, None, None, None, 2.0, 3.0])


def test_indicators_rsi_wilder_hand_computed() -> None:
    # 종가 10 11 12 11 13 12 → 변화 +1 +1 −1 +2 −1, 기간 3
    # idx3: 평균이익 (1+1+0)/3 = 2/3, 평균손실 1/3 → RS 2 → RSI 66.667
    # idx4: 이익 (2/3·2 + 2)/3 = 10/9, 손실 (1/3·2 + 0)/3 = 2/9 → RS 5 → RSI 83.333
    # idx5: 이익 (10/9·2)/3 = 20/27, 손실 (2/9·2 + 1)/3 = 13/27 → RS 20/13 → RSI 60.606
    got = rsi_wilder([10, 11, 12, 11, 13, 12], 3)
    assert got == approx_list([None, None, None, 200 / 3, 250 / 3, 100 - 100 / (1 + 20 / 13)])


def test_indicators_rsi_all_gains_is_100() -> None:
    assert rsi_wilder([1, 2, 3, 4, 5], 3)[-1] == pytest.approx(100.0)


def test_indicators_rsi_flat_is_none() -> None:
    # 이익·손실이 모두 0이면 정의되지 않는다 → None (0이나 50으로 만들지 않는다)
    assert rsi_wilder([5, 5, 5, 5, 5], 3)[-1] is None


def test_indicators_macd_hand_computed() -> None:
    # fast 2 (alpha 2/3), slow 3 (alpha 1/2), signal 2 — 종가 1 2 4 7 11
    # ema2: 시드 1.5 → 2/3·4 + 1/3·1.5 = 19/6 → 2/3·7 + 1/3·19/6 = 103/18
    #       → 2/3·11 + 1/3·103/18 = 499/54
    # ema3: 시드 7/3 → 0.5·7 + 0.5·7/3 = 14/3 → 0.5·11 + 0.5·14/3 = 47/6
    # macd(idx2..4) = 19/6 − 7/3 = 5/6 · 103/18 − 14/3 = 19/18 · 499/54 − 47/6 = 38/27
    # signal: 시드 idx3 = (5/6 + 19/18)/2 = 17/18 → idx4 = 2/3·38/27 + 1/3·17/18 = 203/162
    m, s, h = macd([1, 2, 4, 7, 11], 2, 3, 2)
    assert m == approx_list([None, None, 5 / 6, 19 / 18, 38 / 27])
    assert s == approx_list([None, None, None, 17 / 18, 203 / 162])
    assert h == approx_list([None, None, None, 19 / 18 - 17 / 18, 38 / 27 - 203 / 162])
