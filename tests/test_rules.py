"""rules/v0.toml — 배점 산술과 정규 해시 (SPEC §4.1·§4.2·§4.5).

기대값은 SPEC 표에서 손으로 옮긴 독립 값이다. 규칙 파일을 읽어 같은 파일과 비교하지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scoring.rules import Rules, RulesError, canonical_hash, load_rules

RULES_V0 = Path(__file__).resolve().parents[1] / "rules" / "v0.toml"

# SPEC §4.1 · §4.5 에서 옮긴 값 (코드와 독립)
EXPECTED_ITEM_MAX = {
    "tech.alignment": 11,
    "tech.trend": 6,
    "tech.volume": 6,
    "tech.volume_profile": 7,
    "tech.rsi_macd": 5,
    "fund.per": 7,
    "fund.pbr": 6,
    "fund.op_margin": 7,
    "fund.growth": 7,
    "fund.roe": 5,
    "fund.debt_ratio": 3,
    "disc.dart": 7,
    "flow.foreign": 6,
    "flow.inst": 5,
    "flow.shorting": 2,
    "news.naver": 8,
    "flow.credit": 2,
}


@pytest.fixture(scope="module")
def rules() -> Rules:
    return load_rules(RULES_V0)


def test_rules_item_max_matches_spec(rules: Rules) -> None:
    assert {i.id: i.max for i in rules.items} == EXPECTED_ITEM_MAX


def test_rules_concept_item_count_is_17(rules: Rules) -> None:
    # 공통 15 + 뉴스 별도 1 + 신용 비활성 1 (SPEC §4.1)
    assert len(rules.items) == 17
    assert len(rules.common_items) == 15


def test_rules_common_max_is_90(rules: Rules) -> None:
    assert rules.common_max == 90


def test_rules_axis_max(rules: Rules) -> None:
    assert rules.axis_max("technical") == 35
    assert rules.axis_max("fundamental") == 35
    assert rules.axis_max("disclosure") == 7
    assert rules.axis_max("flow") == 13


def test_rules_news_separate_and_credit_inactive(rules: Rules) -> None:
    by_id = {i.id: i for i in rules.items}
    assert by_id["news.naver"].role == "separate"
    assert by_id["flow.credit"].role == "inactive"
    assert "news.naver" not in {i.id for i in rules.common_items}
    assert "flow.credit" not in {i.id for i in rules.common_items}


def test_rules_tier_points_never_exceed_item_max(rules: Rules) -> None:
    # 단계 배점 합(최대 경로)이 항목 만점을 넘지 않는다
    for item in rules.items:
        assert item.reachable_max() <= item.max, item.id


def test_rules_tier_reachable_max_equals_item_max(rules: Rules) -> None:
    # 확정 항목은 만점에 도달 가능해야 한다 (보류·비활성 항목 제외)
    for item in rules.common_items:
        if item.pending:
            continue
        assert item.reachable_max() == item.max, item.id


def test_rules_thresholds(rules: Rules) -> None:
    assert rules.coverage_total_min == pytest.approx(0.80)
    assert rules.coverage_axis_min == pytest.approx(0.60)
    assert rules.grade_cuts == (75, 60, 45)
    assert rules.experimental is True


def test_rules_hash_stable_across_key_order() -> None:
    a = {"x": 1, "y": {"b": [1, 2], "a": "k"}}
    b = {"y": {"a": "k", "b": [1, 2]}, "x": 1}
    assert canonical_hash(a) == canonical_hash(b)


def test_rules_hash_changes_on_value_change() -> None:
    assert canonical_hash({"x": 1}) != canonical_hash({"x": 2})


def test_rules_hash_is_sha256_hex(rules: Rules) -> None:
    assert len(rules.hash) == 64
    int(rules.hash, 16)


def test_rules_rejects_axis_sum_mismatch(tmp_path: Path) -> None:
    bad = RULES_V0.read_text(encoding="utf-8").replace("common_max = 90", "common_max = 91")
    p = tmp_path / "bad.toml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(RulesError):
        load_rules(p)
