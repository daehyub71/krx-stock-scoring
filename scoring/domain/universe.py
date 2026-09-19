"""유니버스 분류 — 순수 함수 (SPEC §1.3).

모든 종목을 목록에 남기고, 제외·특수업종·위험을 **표지로** 붙인다. 이름 하나나 봉 부재만으로
상장폐지를 확정하지 않는다. 보조 판별은 alerts `universe.py`(2e0637e)에서 이식했다:
- 우선주: 티커 6번째 자리가 0이 아니다 (이름 '…우'로 판정하면 `미래에셋대우` 같은 보통주가 빠진다)
- 스팩: 이름에 '스팩'
"""

from __future__ import annotations

from dataclasses import dataclass

from scoring.models import DailyBar, TickerMeta
from scoring.rules import Rules

MARKETS = ("KOSPI", "KOSDAQ")


@dataclass(frozen=True)
class UniverseEntry:
    """한 종목의 분류 결과.

    - excluded_reason: 기본 랭킹 대상이 아닌 구조적 사유 (preferred · spac)
    - classification: 참고 표지 (special_sector · new_listing · classification_unknown)
    - risk_flags: 판단일 위험 (suspected_suspension · no_bar_on_t · price_drift)
    """

    meta: TickerMeta
    excluded_reason: str | None
    classification: tuple[str, ...]
    risk_flags: tuple[str, ...]


def is_spac(name: str) -> bool:
    """스팩인가."""
    return "스팩" in name


def is_preferred(ticker: str) -> bool:
    """우선주인가 — 티커 6번째 자리만 본다."""
    return len(ticker) == 6 and ticker[5] != "0"


def classify(
    meta: TickerMeta,
    *,
    n_daily: int,
    bar_on_t: DailyBar | None,
    drifted: bool,
    rules: Rules,
) -> UniverseEntry:
    """종목 하나를 분류한다.

    Args:
        meta: 종목 메타.
        n_daily: 창 안 일봉 수.
        bar_on_t: T일 일봉 (없으면 None).
        drifted: 상위 `ksc_meta.drift`에 수정주가 드리프트로 올라 있는가.
        rules: 규칙 (`[universe]` 표).
    """
    uni = rules.section("universe")
    excluded = "spac" if is_spac(meta.name) else "preferred" if is_preferred(meta.ticker) else None

    labels = []
    if meta.sector in uni["special_sectors"] or "리츠" in meta.name:
        labels.append("special_sector")
    if n_daily < uni["new_listing_sessions"]:
        labels.append("new_listing")
    if meta.market not in MARKETS:
        labels.append("classification_unknown")

    risks = []
    if bar_on_t is None:
        risks.append("no_bar_on_t")
    elif bar_on_t.v == 0:
        risks.append("suspected_suspension")
    if drifted:
        risks.append("price_drift")
    return UniverseEntry(meta=meta, excluded_reason=excluded, classification=tuple(labels),
                         risk_flags=tuple(risks))
