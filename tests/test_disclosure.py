"""공시 7점 — 30일 창 · 기본 4 · 호재 +1.5 · 악재 −2 · 치명 0 (SPEC §4.5 · §5.5).

기대값은 SPEC 표에서 손으로 계산한 값이다. 구현을 불러 기댓값을 만들지 않는다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from scoring.domain.disclosure import DisclosureRow, score_disclosure
from scoring.rules import Rules, load_rules

RULES_V0 = Path(__file__).resolve().parents[1] / "rules" / "v0.toml"
T = date(2026, 9, 18)


@pytest.fixture(scope="module")
def rules() -> Rules:
    return load_rules(RULES_V0)


def row(report_nm: str, day: date = T, rcept_no: str = "") -> DisclosureRow:
    return DisclosureRow(
        rcept_no=rcept_no or f"2026{day.month:02d}{day.day:02d}{abs(hash(report_nm)) % 10**6:06d}",
        rcept_dt=day,
        report_nm=report_nm,
    )


# ── 기본값과 창 ─────────────────────────────────────────────────


def test_no_events_gives_base_points(rules: Rules) -> None:
    """공시가 하나도 없으면 기본 4점 — 「확인된 사건 없음」이지 결측이 아니다."""
    part = score_disclosure([], T, "삼성전자", rules)
    assert part.state == "no_event"
    assert part.points == 4.0
    assert part.max == 7


def test_not_queried_is_missing_not_zero(rules: Rules) -> None:
    """조회하지 않았으면 0점이 아니라 결측이다."""
    part = score_disclosure([], T, "삼성전자", rules, queried=False)
    assert part.state == "missing"
    assert part.points is None
    assert part.missing_reason == "not_queried"


def test_events_outside_window_are_ignored(rules: Rules) -> None:
    old = row("주요사항보고서(유상증자결정)", date(2026, 8, 19))  # T-30일 — 창 밖
    part = score_disclosure([old], T, "삼성전자", rules)
    assert part.state == "no_event"
    assert part.points == 4.0


def test_window_boundary_is_thirty_days(rules: Rules) -> None:
    """창은 **T를 포함한 30달력일**이다 — T-29일은 창 안, T-30일은 창 밖."""
    inside = score_disclosure(
        [row("주요사항보고서(유상증자결정)", date(2026, 8, 20))], T, "삼성전자", rules
    )
    outside = score_disclosure(
        [row("주요사항보고서(유상증자결정)", date(2026, 8, 19))], T, "삼성전자", rules
    )
    assert inside.points == 2.0  # 4 − 2
    assert outside.points == 4.0


def test_future_disclosures_are_excluded(rules: Rules) -> None:
    """T 이후 공시는 그날 알 수 없었다 — 미래 입력 차단 (SPEC §5.1)."""
    part = score_disclosure(
        [row("주요사항보고서(자기주식취득결정)", date(2026, 9, 19))], T, "삼성전자", rules
    )
    assert part.state == "no_event"


# ── 가감점 ──────────────────────────────────────────────────────


def test_single_positive_adds_one_and_half(rules: Rules) -> None:
    part = score_disclosure([row("주요사항보고서(자기주식취득결정)")], T, "삼성전자", rules)
    assert part.points == 5.5
    assert part.state == "observed"


def test_single_negative_subtracts_two(rules: Rules) -> None:
    part = score_disclosure([row("주요사항보고서(감자결정)")], T, "삼성전자", rules)
    assert part.points == 2.0


def test_positive_and_negative_net_out(rules: Rules) -> None:
    rows = [
        row("주요사항보고서(자기주식취득결정)"),
        row("주요사항보고서(무상증자결정)"),
        row("주요사항보고서(감자결정)"),
    ]
    assert score_disclosure(rows, T, "삼성전자", rules).points == 5.0  # 4 + 1.5 + 1.5 − 2


def test_points_are_capped_at_seven(rules: Rules) -> None:
    rows = [
        row("주요사항보고서(자기주식취득결정)"),
        row("주요사항보고서(무상증자결정)"),
        row("현금ㆍ현물배당결정"),
        row("단일판매ㆍ공급계약체결"),
    ]
    assert score_disclosure(rows, T, "삼성전자", rules).points == 7.0  # 4+6=10 → 7


def test_points_floor_at_zero(rules: Rules) -> None:
    rows = [
        row("주요사항보고서(감자결정)"),
        row("소송등의제기ㆍ신청(경영권분쟁소송)"),
        row("주권매매거래정지              (자본감소)"),
    ]
    assert score_disclosure(rows, T, "삼성전자", rules).points == 0.0  # 4−6 → 0


# ── 치명 ────────────────────────────────────────────────────────


def test_fatal_overwrites_with_zero(rules: Rules) -> None:
    """호재가 아무리 많아도 치명 사건이 있으면 0점이다."""
    rows = [
        row("주요사항보고서(자기주식취득결정)"),
        row("현금ㆍ현물배당결정"),
        row("회생절차개시신청"),
    ]
    part = score_disclosure(rows, T, "삼성전자", rules)
    assert part.points == 0.0
    assert part.state == "adverse_defined"
    assert part.actual["fatal_rules"] == ["rehabilitation"]


def test_subsidiary_fatal_is_not_fatal(rules: Rules) -> None:
    """자회사 회생은 강등되어 치명이 아니다 — 악재 −2로만 센다."""
    part = score_disclosure(
        [row("회생절차개시신청(종속회사의 주요경영사항)")], T, "삼성전자", rules
    )
    assert part.points == 2.0
    assert part.state == "observed"


# ── 중복·정정 ───────────────────────────────────────────────────


def test_correction_of_same_event_counts_once(rules: Rules) -> None:
    """정정본과 원본은 한 사건이다 — 같은 제목을 두 번 세지 않는다."""
    rows = [
        row("주요사항보고서(유상증자결정)", date(2026, 9, 10), "20260910000001"),
        row("[기재정정]주요사항보고서(유상증자결정)", date(2026, 9, 17), "20260917000002"),
    ]
    part = score_disclosure(rows, T, "삼성전자", rules)
    assert part.points == 2.0  # 4 − 2, 두 번 빼지 않는다
    assert part.actual["event_count"] == 1


def test_different_contracts_are_separate_events(rules: Rules) -> None:
    """서로 다른 계약을 제목이 같다는 이유로 합치지 않는다 — 접수번호가 다르고 날짜도 다르다."""
    rows = [
        row("단일판매ㆍ공급계약체결", date(2026, 9, 10), "20260910000001"),
        row("단일판매ㆍ공급계약체결", date(2026, 9, 17), "20260917000002"),
    ]
    part = score_disclosure(rows, T, "삼성전자", rules)
    assert part.actual["event_count"] == 2
    assert part.points == 7.0  # 4 + 1.5 + 1.5 = 7


def test_unmatched_titles_do_not_move_the_score(rules: Rules) -> None:
    """규칙에 없는 공시는 무해하다 — 정기보고서로 점수가 흔들리지 않는다."""
    rows = [row("분기보고서 (2026.06)"), row("임원ㆍ주요주주특정증권등소유상황보고서")]
    part = score_disclosure(rows, T, "삼성전자", rules)
    assert part.points == 4.0
    assert part.state == "no_event"


# ── 근거 ────────────────────────────────────────────────────────


def test_actual_carries_evidence(rules: Rules) -> None:
    part = score_disclosure([row("주요사항보고서(감자결정)")], T, "삼성전자", rules)
    ev = part.actual["events"]
    assert len(ev) == 1
    assert ev[0]["rule"] == "capital_reduction"
    assert ev[0]["level"] == "amber"
    assert ev[0]["rcept_no"]
    assert part.actual["window"] == ["2026-08-20", "2026-09-18"]
