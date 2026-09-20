"""단계 배점 — 규칙 파일의 `tiers`를 값에 적용한다 (순수 함수)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def tier_points(value: float, tiers: Sequence[Sequence[Any]], cmp: str) -> float:
    """위에서부터 먼저 만족하는 단계의 점수. 아무 단계도 아니면 0.

    단계에 세 번째 원소가 있으면 그 단계만 그 비교(ge/le/lt/gt)를 쓴다.
    """
    for tier in tiers:
        bound, points = float(tier[0]), float(tier[1])
        op = str(tier[2]) if len(tier) > 2 else cmp
        if (
            (op == "ge" and value >= bound)
            or (op == "le" and value <= bound)
            or (op == "lt" and value < bound)
            or (op == "gt" and value > bound)
        ):
            return points
    return 0.0
