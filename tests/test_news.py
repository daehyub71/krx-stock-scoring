"""뉴스 9점 — 관련성 필터 · 사전 판정 · 상태 구분 (SPEC §4.5 · §5.5).

제목은 2026-09-23 네이버 실호출에서 본 형태를 그대로 쓴다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scoring.domain.lexicon import Lexicon, Term, news_lexicon
from scoring.domain.news import Article, is_related, judge, score_news
from scoring.rules import Rules, load_rules

RULES_V0 = Path(__file__).resolve().parents[1] / "rules" / "v0.toml"
NOW = datetime(2026, 9, 18, 15, 30)


@pytest.fixture(scope="module")
def rules() -> Rules:
    return load_rules(RULES_V0)


@pytest.fixture(scope="module")
def lex() -> Lexicon:
    return news_lexicon()


def art(title: str, hours_ago: int = 1, link: str = "https://n.news/1") -> Article:
    return Article(title=title, link=link, published_at=NOW - timedelta(hours=hours_ago))


# ── 관련성 필터 ─────────────────────────────────────────────────


def test_title_must_name_the_company() -> None:
    assert is_related("삼성전자, 3분기 최대실적 전망", "삼성전자") is True


def test_market_roundup_is_not_related() -> None:
    """실측에서 나온 형태 — 종목명이 본문에만 있는 시황 기사는 그 회사의 사건이 아니다."""
    assert is_related("코스닥, 개인과 기관 매수에 상승 마감", "에코프로비엠") is False


def test_short_name_needs_stock_context() -> None:
    """두 글자 이름은 우연히 겹치기 쉽다 — 주식 문맥어가 있어야 통과한다."""
    assert is_related("한샘 가구 신제품 출시 소식", "한샘") is False
    assert is_related("한샘 주가 급등, 목표가 상향", "한샘") is True


def test_short_name_is_not_always_dropped() -> None:
    """두 글자라는 이유만으로 그 회사의 기사를 전부 버리지 않는다 (SPEC §5.5)."""
    assert is_related("한샘, 2분기 영업이익 흑자전환", "한샘") is True


def test_spacing_does_not_matter() -> None:
    assert is_related("에코프로 비엠, 수주 공시", "에코프로비엠") is True


# ── 사전 판정 ───────────────────────────────────────────────────


def test_judge_positive(lex: Lexicon) -> None:
    verdict, term = judge("삼성전자, 2분기 흑자전환 성공", lex)
    assert verdict == "positive" and term == "흑자전환"


def test_judge_negative(lex: Lexicon) -> None:
    verdict, term = judge("A사 대표 횡령 혐의로 압수수색", lex)
    assert verdict == "negative"


def test_judge_neutral_when_nothing_matches(lex: Lexicon) -> None:
    assert judge("삼성전자, 신제품 발표회 개최", lex) == ("neutral", "")


def test_exclusion_blocks_the_term(lex: Lexicon) -> None:
    """「수주 취소」는 호재가 아니다."""
    verdict, _ = judge("B사, 대형 수주 취소 통보", lex)
    assert verdict != "positive"


def test_priority_puts_severe_terms_first(lex: Lexicon) -> None:
    """한 제목에 호재·악재가 같이 있으면 우선순위가 낮은 숫자(중대한 쪽)가 이긴다."""
    verdict, term = judge("C사, 수주 소식에도 상장폐지 사유 발생", lex)
    assert verdict == "negative" and term == "상장폐지"


# ── 점수 ────────────────────────────────────────────────────────


def test_three_positive_articles_give_nine(rules: Rules, lex: Lexicon) -> None:
    arts = [
        art("삼성전자, 흑자전환 달성", 1),
        art("삼성전자 신고가 경신", 2),
        art("삼성전자, 대규모 수주 체결", 3),
    ]
    part = score_news(arts, NOW, "삼성전자", lex, rules)
    assert part.points == 9.0
    assert part.state == "observed"
    assert part.max == 9


def test_three_negative_articles_give_minus_nine(rules: Rules, lex: Lexicon) -> None:
    arts = [
        art("삼성전자, 적자전환", 1),
        art("삼성전자 압수수색", 2),
        art("삼성전자, 유상증자 결정", 3),
    ]
    assert score_news(arts, NOW, "삼성전자", lex, rules).points == -9.0


def test_neutral_articles_score_zero(rules: Rules, lex: Lexicon) -> None:
    arts = [art("삼성전자, 신제품 공개", 1), art("삼성전자 대표 인터뷰", 2)]
    part = score_news(arts, NOW, "삼성전자", lex, rules)
    assert part.points == 0.0
    assert part.state == "observed"  # 기사는 있었다 — no_event와 다르다


def test_only_three_newest_articles_count(rules: Rules, lex: Lexicon) -> None:
    arts = [art(f"삼성전자, 흑자전환 {i}", i) for i in range(1, 6)]
    part = score_news(arts, NOW, "삼성전자", lex, rules)
    assert part.points == 9.0
    assert part.actual["used"] == 3
    assert part.actual["related"] == 5


def test_articles_outside_window_are_dropped(rules: Rules, lex: Lexicon) -> None:
    part = score_news([art("삼성전자, 흑자전환", 24 * 8)], NOW, "삼성전자", lex, rules)
    assert part.state == "no_event"
    assert part.points == 0.0


def test_unrelated_articles_leave_no_event(rules: Rules, lex: Lexicon) -> None:
    """조회는 됐지만 관련 기사가 없다 — 0점이되 결측이 아니다."""
    part = score_news([art("코스닥 상승 마감")], NOW, "에코프로비엠", lex, rules)
    assert part.state == "no_event"
    assert part.points == 0.0
    assert part.actual["fetched"] == 1 and part.actual["related"] == 0


# ── 상태 셋을 섞지 않는다 ───────────────────────────────────────


def test_not_queried_is_missing(rules: Rules, lex: Lexicon) -> None:
    part = score_news([], NOW, "삼성전자", lex, rules, status="not_queried")
    assert part.state == "missing" and part.points is None
    assert part.missing_reason == "not_queried"


def test_source_error_is_missing(rules: Rules, lex: Lexicon) -> None:
    part = score_news([], NOW, "삼성전자", lex, rules, status="source_error")
    assert part.state == "missing" and part.missing_reason == "source_error"


# ── 사전 버전 ───────────────────────────────────────────────────


def test_two_lexicon_versions_coexist_and_reproduce(rules: Rules, lex: Lexicon) -> None:
    """한 항목을 내리면 다른 버전이 되고, 각 버전이 그 버전의 점수를 낸다 (M3 완료 조건)."""
    v2 = Lexicon(
        scope="news",
        terms=tuple(t for t in lex.terms if t.norm != "흑자전환"),
    )
    arts = [art("삼성전자, 흑자전환 달성")]
    assert lex.version != v2.version
    assert score_news(arts, NOW, "삼성전자", lex, rules).points == 3.0
    assert score_news(arts, NOW, "삼성전자", v2, rules).points == 0.0


def test_version_is_stable_for_same_content(lex: Lexicon) -> None:
    assert news_lexicon().version == lex.version


def test_disabled_terms_do_not_change_the_version(lex: Lexicon) -> None:
    """비활성 항목은 버전에 들어가지 않는다 — 내린 낱말이 해시를 흔들지 않는다."""
    extra = Lexicon(
        scope="news",
        terms=(*lex.terms, Term("실험어", "실험어", "positive", "news", enabled=False)),
    )
    assert extra.version == lex.version


# ── 사법·수사 (2026-09-23 실측 보완) ────────────────────────────
#
# 시드 v1이 공시체 낱말(흑자전환·수주·유상증자)만 담고 있어 실제 기사 제목이 중립으로 나왔다.
# 사용자 지적으로 갈래를 채웠다 — 「주가조작 도피 조력」은 분명한 악재다.


@pytest.mark.parametrize(
    "title",
    [
        "‘삼부토건 주가조작’ 이기훈 도피 조력…상장사 회장 실형 확정",
        "검찰, 'SM엔터 시세조종' 혐의 김범수 카카오 창업자에 2심도 징역 15년 구형",
        "A사 전 대표 분식회계 혐의로 기소",
        "공정위, B사에 담합 과징금 부과",
        "C사, 상장적격성 실질심사 대상 결정",
    ],
)
def test_legal_risk_titles_are_negative(title: str, lex: Lexicon) -> None:
    verdict, term = judge(title, lex)
    assert verdict == "negative", f"중립으로 새어 나갔다 — 걸린 낱말 {term!r}"


@pytest.mark.parametrize(
    "title",
    ["D사 대표, 1심 무죄 판결…징역 구형 뒤집혀", "E사, 담합 과징금 취소 판결", "F사 불기소 처분"],
)
def test_cleared_legal_titles_are_not_negative(title: str, lex: Lexicon) -> None:
    """무죄·불기소·취소는 같은 낱말이 들어가도 악재가 아니다."""
    verdict, _ = judge(title, lex)
    assert verdict != "negative"


def test_legal_terms_outrank_positive_ones(lex: Lexicon) -> None:
    """수주 소식이 함께 있어도 수사 기사는 악재로 읽는다."""
    verdict, _ = judge("G사, 대형 수주에도 대표 주가조작 혐의 기소", lex)
    assert verdict == "negative"
