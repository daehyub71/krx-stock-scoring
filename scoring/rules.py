"""규칙 파일 로드·검증·정규 해시 (SPEC §4.2 — 배점·임계값의 단일 기준).

`rules/<version>.toml`을 읽어 산술을 검증한다: 축별 항목 만점 합 = 축 만점, 축 합 = 공통 만점,
각 항목의 도달 가능 최대 ≤ 만점. 틀리면 로드 단계에서 멈춘다 — 잘못된 규칙으로 점수를 내지 않는다.

해시는 파싱한 값을 키 정렬 JSON으로 직렬화한 SHA-256이다. 주석·공백·키 순서가 달라도 같다.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 공통 총점에 들어가는 축 (뉴스는 별도, 신용은 비활성)
COMMON_AXES = ("technical", "fundamental", "disclosure", "flow")


class RulesError(ValueError):
    """규칙 파일이 SPEC 산술과 맞지 않는다."""


def canonical_hash(data: Any) -> str:
    """값을 키 정렬 JSON으로 직렬화한 SHA-256 16진 문자열."""
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Item:
    """규칙 항목 하나. `params`는 항목별 계산 함수가 읽는 원본 표다."""

    id: str
    axis: str
    max: float
    role: str
    pending: bool
    params: dict[str, Any]

    def reachable_max(self) -> float:
        """규칙상 도달 가능한 최대 점수 — 단계 배점의 최대 경로 합(상한 적용).

        `*_points` 스칼라, `*tiers`의 최대 점수, 정배열의 쌍 배점을 더한다.
        """
        total = 0.0
        for key, value in self.params.items():
            if key.endswith("_points") and isinstance(value, int | float):
                total += value
            elif key.endswith("tiers") and value:
                total += max(tier[1] for tier in value)
        total += sum(pair[2] for pair in self.params.get("daily_pairs", []))
        if "weekly_pair" in self.params:
            total += self.params["weekly_pair"][2]
        if "monthly_close_above" in self.params:
            total += self.params["monthly_close_above"][1]
        cap = self.params.get("cap")
        return float(min(total, cap)) if cap is not None else total


@dataclass(frozen=True)
class Rules:
    """검증을 통과한 규칙 한 판."""

    version: str
    profile: str
    experimental: bool
    hash: str
    common_max: float
    axes: dict[str, float]
    items: tuple[Item, ...]
    coverage_total_min: float
    coverage_axis_min: float
    grade_cuts: tuple[int, ...]
    raw: dict[str, Any]

    @property
    def common_items(self) -> tuple[Item, ...]:
        """공통 총점에 들어가는 항목 (role = common)."""
        return tuple(i for i in self.items if i.role == "common")

    def axis_max(self, axis: str) -> float:
        """축 만점."""
        return self.axes[axis]


def _items(raw: dict[str, Any]) -> tuple[Item, ...]:
    items = []
    for item_id, spec in raw.get("items", {}).items():
        params = {k: v for k, v in spec.items() if k not in {"axis", "max", "role", "pending"}}
        items.append(
            Item(
                id=item_id,
                axis=spec["axis"],
                max=spec["max"],
                role=spec.get("role", "common"),
                pending=bool(spec.get("pending", False)),
                params=params,
            )
        )
    return tuple(items)


def _validate(rules: Rules) -> None:
    if sum(rules.axes.values()) != rules.common_max:
        raise RulesError(f"축 만점 합 {sum(rules.axes.values())} ≠ common_max {rules.common_max}")
    if set(rules.axes) != set(COMMON_AXES):
        raise RulesError(f"공통 축 {sorted(rules.axes)} ≠ {sorted(COMMON_AXES)}")
    for axis, axis_max in rules.axes.items():
        got = sum(i.max for i in rules.common_items if i.axis == axis)
        if got != axis_max:
            raise RulesError(f"{axis} 항목 합 {got} ≠ 축 만점 {axis_max}")
    for item in rules.items:
        if item.role not in {"common", "separate", "inactive"}:
            raise RulesError(f"{item.id}: 알 수 없는 role {item.role}")
        if item.reachable_max() > item.max:
            raise RulesError(f"{item.id}: 도달 가능 {item.reachable_max()} > 만점 {item.max}")


def load_rules(path: Path) -> Rules:
    """규칙 파일을 읽고 검증한다.

    Args:
        path: `rules/<version>.toml` 경로.

    Returns:
        검증된 규칙.

    Raises:
        RulesError: 배점 산술이 맞지 않을 때.
    """
    with path.open("rb") as f:
        raw = tomllib.load(f)
    model = raw["model"]
    rules = Rules(
        version=raw["meta"]["version"],
        profile=raw["meta"]["profile"],
        experimental=bool(raw["meta"].get("experimental", True)),
        hash=canonical_hash(raw),
        common_max=model["common_max"],
        axes=dict(model["axes"]),
        items=_items(raw),
        coverage_total_min=raw["eligibility"]["coverage_total_min"],
        coverage_axis_min=raw["eligibility"]["coverage_axis_min"],
        grade_cuts=tuple(raw["grade"]["cuts"]),
        raw=raw,
    )
    _validate(rules)
    return rules
