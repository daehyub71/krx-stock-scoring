"""이동평균·RSI·MACD — 순수 함수 (SPEC §5.2).

모든 함수는 입력과 같은 길이의 목록을 돌려주고, 값이 정의되지 않는 자리는 None이다.
워밍업 부족을 0이나 중립값으로 채우지 않는다 — 결측은 호출한 쪽이 `missing`으로 처리한다.

- SMA: 단순 이동평균 (정배열 — 원본 계획서 §4.1a: SMA 고정)
- EMA: 첫 n개 SMA를 시드로 하는 지수 이동평균, alpha = 2/(n+1)
- RSI: Wilder 평활 — 첫 평균은 n개 변화의 단순 평균, 이후 (이전·(n−1) + 현재)/n
- MACD: EMA(fast) − EMA(slow), 시그널 = MACD의 EMA(signal), 히스토그램 = MACD − 시그널
"""

from __future__ import annotations

from collections.abc import Sequence

Series = list[float | None]


def sma(xs: Sequence[float], n: int) -> Series:
    """단순 이동평균."""
    out: Series = [None] * len(xs)
    total = 0.0
    for i, x in enumerate(xs):
        total += x
        if i >= n:
            total -= xs[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(xs: Sequence[float | None], n: int) -> Series:
    """SMA 시드 EMA. 앞쪽 None은 건너뛰고 첫 유효값부터 센다."""
    out: Series = [None] * len(xs)
    start = next((i for i, x in enumerate(xs) if x is not None), len(xs))
    vals = [x for x in xs[start:] if x is not None]
    if len(vals) != len(xs) - start or len(vals) < n:
        return out  # 중간 None은 계약 위반 — 계산하지 않는다
    alpha = 2.0 / (n + 1)
    prev = sum(vals[:n]) / n
    out[start + n - 1] = prev
    for j in range(n, len(vals)):
        prev = alpha * vals[j] + (1 - alpha) * prev
        out[start + j] = prev
    return out


def rsi_wilder(closes: Sequence[float], n: int) -> Series:
    """Wilder RSI. 이익·손실 평균이 모두 0이면 정의되지 않는다(None)."""
    out: Series = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n

    def value(g: float, loss: float) -> float | None:
        if g == 0 and loss == 0:
            return None
        if loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + g / loss)

    out[n] = value(avg_g, avg_l)
    for i in range(n + 1, len(closes)):
        avg_g = (avg_g * (n - 1) + gains[i - 1]) / n
        avg_l = (avg_l * (n - 1) + losses[i - 1]) / n
        out[i] = value(avg_g, avg_l)
    return out


def macd(
    closes: Sequence[float], fast: int, slow: int, signal: int
) -> tuple[Series, Series, Series]:
    """(MACD, 시그널, 히스토그램)."""
    ef, es = ema(closes, fast), ema(closes, slow)
    line: Series = [
        None if a is None or b is None else a - b for a, b in zip(ef, es, strict=True)
    ]
    sig = ema(line, signal)
    hist: Series = [
        None if a is None or b is None else a - b for a, b in zip(line, sig, strict=True)
    ]
    return line, sig, hist
