"""flags — report_nm 정규화 · 규칙표 · 판정. 순수 함수 (SPEC §5.5).

원칙: **규칙마다 양성 1 + 헷갈리는 음성 1.**
키워드 하나를 지워도 통과하면 그 규칙은 검증되지 않은 것이다.
제목은 전부 `tests/fixtures/report_names.txt`(2026-08-29 실표본)에 있는 실제 형태다.

verify에서 이식하면서 호재 5종·치명 4종을 더했다(SPEC v2.9) — 그 표본도 같은 규칙으로 넣는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scoring.domain.flags import RULES, match, normalize

FIXTURES = Path(__file__).parent / "fixtures"


# ── normalize ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "name", "corrected", "note"),
    [
        ("유상증자결정", "유상증자결정", False, ""),
        ("[기재정정]주요사항보고서(유상증자결정)", "주요사항보고서(유상증자결정)", True, ""),
        ("[정정]유상증자결정", "유상증자결정", True, ""),
        ("[정정제출요구]증권신고서(합병)", "증권신고서(합병)", True, ""),
        # 정정이 아닌 접두 — 떼되 corrected는 아니다
        (
            "[발행조건확정]주요사항보고서(전환사채권발행결정)",
            "주요사항보고서(전환사채권발행결정)",
            False,
            "",
        ),
        # 공백은 전부 지운다 · 가운뎃점은 하나로 통일
        ("유상증자 결정", "유상증자결정", False, ""),
        ("횡령ㆍ배임혐의발생", "횡령·배임혐의발생", False, ""),
        # 뒤에 공백 여러 칸 + 괄호 설명 → note로 분리
        (
            "감사보고서제출              (감사의견 의견거절)",
            "감사보고서제출",
            False,
            "감사의견 의견거절",
        ),
        ("현금ㆍ현물배당결정              (분기배당)", "현금·현물배당결정", False, "분기배당"),
    ],
)
def test_normalize(raw: str, name: str, corrected: bool, note: str) -> None:
    n = normalize(raw)
    assert (n.name, n.corrected, n.note) == (name, corrected, note)


# ── 규칙표 — 규칙당 양성 1 + 음성 1 ─────────────────────────────

# rule → (양성 제목, 헷갈리는 음성 제목).
# 음성은 "이 규칙에는 걸리면 안 되는" 것이지 무해하다는 뜻이 아니다.
SAMPLES: dict[str, tuple[str, str]] = {
    # 🔴
    "cb": (
        "[기재정정]주요사항보고서(전환사채권발행결정)",
        "전환사채(해외전환사채포함)발행후만기전사채취득",
    ),
    "bw": ("주요사항보고서(신주인수권부사채권발행결정)", "신주인수권행사              (제15회차)"),
    "eb": ("주요사항보고서(교환사채권발행결정)", "전환청구권ㆍ신주인수권ㆍ교환청구권행사"),
    "rights_issue": (
        "주요사항보고서(유상증자결정)",
        "유상증자또는주식관련사채등의발행결과(자율공시)",
    ),
    "controller_change": ("최대주주변경", "최대주주등소유주식변동신고서"),
    "admin_issue": ("관리종목지정", "기타시장안내(관리종목지정우려종목)"),
    "caution_issue": ("투자주의환기종목지정", "투자주의환기종목지정해제"),
    "unfaithful": ("불성실공시법인지정", "불성실공시법인지정예고              (공시불이행)"),
    # 🔴 치명
    "delisting": ("상장폐지결정", "상장폐지사유해소"),
    "embezzlement": ("횡령ㆍ배임혐의발생", "임원ㆍ주요주주특정증권등소유상황보고서"),
    "rehabilitation": ("회생절차개시신청", "회생절차종결"),
    "audit": ("감사보고서제출              (감사의견 의견거절)", "감사보고서제출"),
    # 🟢 호재 (SPEC v2.9)
    "treasury_buy": ("주요사항보고서(자기주식취득결정)", "자기주식취득결과보고서"),
    "treasury_trust": (
        "주요사항보고서(자기주식취득신탁계약체결결정)",
        "주요사항보고서(자기주식취득신탁계약해지결정)",
    ),
    "bonus_issue": ("주요사항보고서(무상증자결정)", "권리락              (무상증자)"),
    "dividend": ("현금ㆍ현물배당결정", "부동산투자회사금전배당결정"),
    "supply_contract": (
        "단일판매ㆍ공급계약체결(자율공시)",
        "[기재정정]단일판매ㆍ공급계약해지(자회사의 주요경영사항)",
    ),
    # 🟡
    "trading_halt": (
        "주권매매거래정지              (자본감소)",
        "주권매매거래정지해제              (감자 주권 변경상장)",
    ),
    "lawsuit": ("소송등의제기ㆍ신청(경영권분쟁소송)", "소송등의판결ㆍ결정"),
    "treasury_sale": ("주요사항보고서(자기주식처분결정)", "자기주식처분결과보고서"),
    "pledge": ("최대주주변경을수반하는주식담보제공계약체결", "최대주주변경"),
    "admin_warning": (
        "기타시장안내(관리종목지정우려종목)              (주가 1,000원 미달)",
        "관리종목지정",
    ),
    "unfaithful_warning": (
        "불성실공시법인지정예고              (공시불이행 2건)",
        "불성실공시법인미지정              (지정유예)",
    ),
    "market_warning": ("투자경고종목지정", "투자경고종목지정해제"),
    "capital_reduction": ("주요사항보고서(감자결정)", "감자완료"),
}


def test_every_rule_has_samples() -> None:
    """규칙을 추가하면 표본도 추가해야 한다 — 검증되지 않은 규칙이 규칙표에 들어오지 못하게."""
    assert {r.id for r in RULES} == set(SAMPLES)


@pytest.mark.parametrize("rule_id", list(SAMPLES))
def test_rule_positive(rule_id: str) -> None:
    positive, _ = SAMPLES[rule_id]
    m = match(positive)
    assert m is not None, f"{rule_id}: 양성이 걸리지 않았다 — {positive!r}"
    assert m.rule == rule_id, f"{rule_id}: 다른 규칙({m.rule})에 먼저 걸렸다"
    expected = next(r.level for r in RULES if r.id == rule_id)
    assert m.level == expected


@pytest.mark.parametrize("rule_id", list(SAMPLES))
def test_rule_negative(rule_id: str) -> None:
    _, negative = SAMPLES[rule_id]
    m = match(negative)
    assert m is None or m.rule != rule_id, f"{rule_id}: 음성이 걸렸다 — {negative!r}"


# ── 강등 — 자회사·리츠 ───────────────────────────────────────────


def test_subsidiary_downgrades_red_to_amber() -> None:
    m = match("주요사항보고서(유상증자결정)(자회사의 주요경영사항)")
    assert m is not None and m.rule == "rights_issue"
    assert m.level == "amber" and m.subsidiary is True


def test_reit_rights_issue_is_amber() -> None:
    m = match("주요사항보고서(유상증자결정)", company_name="이지스밸류플러스리츠")
    assert m is not None and m.level == "amber"


# ── 치명 — 강등을 거친 뒤에도 🔴인 것만 (SPEC v2.9) ──────────────


@pytest.mark.parametrize(
    "report_nm",
    [
        "상장폐지결정",
        "회생절차개시신청",
        "횡령ㆍ배임혐의발생",
        "감사보고서제출   (감사의견 의견거절)",
    ],
)
def test_fatal_rules_are_fatal(report_nm: str) -> None:
    m = match(report_nm)
    assert m is not None and m.fatal is True


def test_subsidiary_rehabilitation_is_not_fatal() -> None:
    """모회사가 자회사 회생으로 0점이 되지 않는다 — 강등되면 치명도 풀린다."""
    m = match("회생절차개시신청(종속회사의 주요경영사항)")
    assert m is not None and m.level == "amber" and m.fatal is False


def test_non_fatal_red_is_not_fatal() -> None:
    m = match("주요사항보고서(유상증자결정)")
    assert m is not None and m.level == "red" and m.fatal is False


def test_positive_is_never_fatal() -> None:
    m = match("주요사항보고서(자기주식취득결정)")
    assert m is not None and m.level == "positive" and m.fatal is False


# ── 유무상증자 — 무상증자 호재로 세지 않는다 ────────────────────


def test_mixed_issue_counts_as_rights_issue() -> None:
    """`유무상증자결정`은 유상분이 섞여 있다 — 호재가 아니라 🔴다."""
    m = match("주요사항보고서(유무상증자결정)")
    assert m is not None and m.rule == "rights_issue" and m.level == "red"


# ── 실표본 회귀 — 354줄 전부를 판정해 본다 ──────────────────────


def test_real_sample_titles_never_crash() -> None:
    """실표본 전체가 예외 없이 판정되고, 걸린 규칙은 모두 규칙표에 있는 것이어야 한다."""
    lines = (FIXTURES / "report_names.txt").read_text(encoding="utf-8").splitlines()
    titles = [line.split("\t", 1)[-1].strip() for line in lines if line.strip()]
    assert len(titles) > 300
    ids = {r.id for r in RULES}
    for title in titles:
        m = match(title)
        assert m is None or m.rule in ids
