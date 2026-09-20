"""집계 — 상태·관측률·축 추정·자격 (SPEC §4.1·§4.3·§4.4).

- 관측(available) = observed · no_event · adverse_defined. missing·not_applicable은 빠진다.
- 상태: excluded(구조적 제외) → scored(완전 관측) → provisional(전체·축 하한 통과)
  → insufficient_data.
- 확정 total은 scored + 총점을 내는 프로필에서만. 부분 관측은 estimated_total로 분리한다.
- **총점은 99점 척도의 원점수 그대로다(v2.6) — 환산하지 않는다.** 뉴스(−9~+9)만 음수를 낼 수 있고,
  음수를 0으로 자르지 않는다.
- 규칙이 experimental이면 등급을 내지 않는다(M6 검증 전).
- 기본 랭킹 자격은 계산 가능 여부와 별개다 — 특수업종·위험 표지는 scored여도 제외.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from scoring.domain.universe import UniverseEntry
from scoring.models import Part
from scoring.rules import COMMON_AXES, Rules

AVAILABLE = frozenset({"observed", "no_event", "adverse_defined"})

# 프로필 → (축, 총점을 내는가). technical은 M1 기술 전용 대조(partial_technical)
PROFILES: dict[str, tuple[tuple[str, ...], bool]] = {
    "technical": (("technical",), False),
    "partial": (("technical", "fundamental", "flow"), False),   # M2 — 공시·뉴스 전, 총점 없음
    "common": (COMMON_AXES, True),
}
NOT_RANKABLE = frozenset({"special_sector", "classification_unknown"})


@dataclass(frozen=True)
class ScoreRow:
    """한 종목의 집계 결과 (`kss_scores` 한 행의 계산 부분)."""

    status: str
    profile_max: float
    raw_total: float | None
    total: float | None
    estimated_total: float | None
    grade: str | None
    coverage: float
    axis: dict[str, dict[str, Any]]
    availability_signature: str
    rank_eligible: bool
    risk_flags: tuple[str, ...]
    passes_screen: bool | None


def _grade(total: float, cuts: Sequence[int]) -> str:
    for letter, cut in zip("ABC", cuts, strict=False):
        if total >= cut:
            return letter
    return "D"


def aggregate(
    parts: Sequence[Part],
    entry: UniverseEntry,
    rules: Rules,
    profile: str,
    passes_screen: bool | None,
    extra_risk_flags: tuple[str, ...] = (),
) -> ScoreRow:
    """항목 결과를 한 종목 점수 행으로 묶는다.

    Raises:
        ValueError: 알 수 없는 프로필이거나, 점수가 항목 만점 범위를 벗어날 때.
    """
    axes, produces_total = PROFILES[profile]
    model_max = sum(rules.axis_max(a) for a in axes)
    in_profile = [p for p in parts if p.axis in axes]
    for p in in_profile:
        if p.points is not None and not -p.max <= p.points <= p.max:
            raise ValueError(f"{p.item}: 점수 {p.points}가 −{p.max}~{p.max} 밖")

    axis: dict[str, dict[str, Any]] = {}
    for a in axes:
        a_max = rules.axis_max(a)
        avail = [p for p in in_profile if p.axis == a and p.state in AVAILABLE]
        available_max = sum(p.max for p in avail)
        points = sum(p.points or 0.0 for p in avail)
        axis[a] = {
            "points": points,
            "available_max": available_max,
            "model_max": a_max,
            "coverage": available_max / a_max,
            "estimate": a_max * points / available_max if available_max else None,
        }

    available_total = sum(v["available_max"] for v in axis.values())
    coverage = available_total / model_max
    complete = available_total == model_max
    provisional_ok = coverage >= rules.coverage_total_min and all(
        v["coverage"] >= rules.coverage_axis_min for v in axis.values()
    )
    if entry.excluded_reason:
        status = "excluded"
    elif complete:
        status = "scored"
    elif provisional_ok:
        status = "provisional"
    else:
        status = "insufficient_data"

    # v2.8 — raw_total은 모든 상태에(관측 항목 합), estimated_total은 축마다 관측이 있을 때,
    # total은 완전 관측 + 100점 공통 프로필에서만 (척도가 다른 값을 한 열에 섞지 않는다)
    raw = sum(v["points"] for v in axis.values())
    raw_total = round(raw, 2) if available_total else None
    total = round(raw, 2) if status == "scored" and produces_total else None
    estimated_total = None
    if status != "scored" and all(v["estimate"] is not None for v in axis.values()):
        estimated_total = round(sum(v["estimate"] for v in axis.values()), 2)
    grade = None
    if total is not None and not rules.experimental:
        grade = _grade(total, rules.grade_cuts)

    signature_src = ",".join(sorted(p.item for p in in_profile if p.state in AVAILABLE))
    signature = hashlib.sha256(f"{profile}|{signature_src}".encode()).hexdigest()[:12]
    risk_flags = tuple(dict.fromkeys((*entry.risk_flags, *extra_risk_flags)))
    rank_eligible = (
        status == "scored"
        and not risk_flags
        and not (set(entry.classification) & NOT_RANKABLE)
    )
    return ScoreRow(
        status=status,
        profile_max=model_max,
        raw_total=raw_total,
        total=total,
        estimated_total=estimated_total,
        grade=grade,
        coverage=round(coverage, 4),
        axis=axis,
        availability_signature=signature,
        rank_eligible=rank_eligible,
        risk_flags=risk_flags,
        passes_screen=passes_screen,
    )
