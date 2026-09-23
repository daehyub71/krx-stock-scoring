"""전 종목 계산 — 스냅샷 → 종목별 (분류, 항목, 점수 행). I/O 없음."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from scoring.domain.aggregate import PROFILES, ScoreRow, aggregate
from scoring.domain.disclosure import DisclosureRow, score_disclosure
from scoring.domain.financial import Statement
from scoring.domain.flow import FlowDay, ShortDay, score_flow
from scoring.domain.fundamental import MarketData, SectorStats, score_fundamental
from scoring.domain.lexicon import Lexicon
from scoring.domain.news import Article, score_news
from scoring.domain.technical import score_technical
from scoring.domain.universe import UniverseEntry, classify
from scoring.models import Part
from scoring.rules import Rules
from scoring.sources.upstream import Snapshot


@dataclass(frozen=True)
class Fundamentals:
    """기본 축 입력 — 시장 값(종가·합산 주식수·배당)과 보고서, 업종 중앙값."""

    market: dict[str, MarketData]
    periodic: dict[str, Statement]
    annual: dict[str, Statement]
    stats: SectorStats
    flows: dict[str, list[FlowDay]]
    shorts: dict[str, list[ShortDay]]
    corp_map_version: str = ""
    corp_map: dict[str, str] = field(default_factory=dict)
    report_versions: tuple[tuple[Any, ...], ...] = ()   # kss_financial_versions 저장용


@dataclass(frozen=True)
class Events:
    """공시·뉴스 입력 (M3). 종목에 자료가 없는 것과 조회하지 않은 것을 구분해 담는다."""

    disclosures: dict[str, list[DisclosureRow]]
    disclosure_lexicon: Lexicon
    news: dict[str, list[Article]] = field(default_factory=dict)
    news_status: dict[str, str] = field(default_factory=dict)
    news_lexicon: Lexicon | None = None
    cutoff: datetime | None = None
    disclosure_queried: bool = True


@dataclass(frozen=True)
class TickerResult:
    """한 종목의 계산 결과."""

    entry: UniverseEntry
    parts: tuple[Part, ...]
    row: ScoreRow


class ValidationError(RuntimeError):
    """게시 전 검증 실패 — 이전 게시본을 유지한다."""


def items_per_ticker(profile: str) -> int:
    """프로필이 만드는 항목 수 — 검증에서 쓴다."""
    return {"technical": 5, "partial": 15, "common": 17}[profile]


def compute_scores(
    snapshot: Snapshot,
    rules: Rules,
    profile: str,
    fundamentals: Fundamentals | None = None,
    events: Events | None = None,
) -> list[TickerResult]:
    """전 종목 계산.

    `partial`은 기술 5 + 기본 7 + 수급 3항목, `common`은 여기에 공시 1 + 뉴스 1을 더해 17항목이다.
    """
    if profile not in PROFILES:
        raise ValueError(f"알 수 없는 프로필: {profile}")
    if profile in ("partial", "common") and fundamentals is None:
        raise ValueError(f"{profile} 프로필에는 기본 축 입력이 필요하다")
    if profile == "common" and events is None:
        raise ValueError("common 프로필에는 공시·뉴스 입력이 필요하다")
    out = []
    for meta in snapshot.tickers:
        # T 이후 봉은 분류에도 쓰지 않는다 (SPEC §11.1 미래 입력 차단)
        bars = tuple(b for b in snapshot.bars.get(meta.ticker, ()) if b.d <= snapshot.t)
        bar_on_t = bars[-1] if bars and bars[-1].d == snapshot.t else None
        entry = classify(meta, n_daily=len(bars), bar_on_t=bar_on_t,
                         drifted=meta.ticker in snapshot.drifted, rules=rules)
        tech = score_technical(bars, snapshot.t, snapshot.cal, rules,
                               stale=meta.ticker in snapshot.drifted)
        parts: tuple[Part, ...] = tech.parts
        extra_risks: tuple[str, ...] = ()
        if fundamentals is not None:
            fund = score_fundamental(
                entry, fundamentals.market.get(meta.ticker), fundamentals.stats,
                fundamentals.periodic.get(meta.ticker), fundamentals.annual.get(meta.ticker),
                snapshot.t, rules,
            )
            flow_parts = score_flow(
                fundamentals.flows.get(meta.ticker, []), fundamentals.shorts.get(meta.ticker, []),
                snapshot.cal.sessions, snapshot.t, meta.market, rules,
                turnover={b.d: b.a for b in bars},
            )
            parts = (*tech.parts, *fund.parts, *flow_parts)
            extra_risks = fund.risk_flags
        if events is not None:
            disc = score_disclosure(
                events.disclosures.get(meta.ticker, []), snapshot.t, meta.name, rules,
                queried=events.disclosure_queried,
            )
            news_lex = events.news_lexicon
            cutoff = events.cutoff or datetime.combine(snapshot.t, datetime.min.time())
            news = score_news(
                events.news.get(meta.ticker, []), cutoff, meta.name, news_lex, rules,
                status=events.news_status.get(meta.ticker, "not_queried"),
            ) if news_lex is not None else None
            parts = (*parts, disc) if news is None else (*parts, disc, news)
        row = aggregate(parts, entry, rules, profile, tech.passes_screen,
                        extra_risk_flags=extra_risks)
        out.append(TickerResult(entry=entry, parts=parts, row=row))
    return out


def summarize(results: list[TickerResult]) -> dict[str, object]:
    """상태·결측 사유·자격 분포 (run stats·로그용)."""
    missing = Counter(
        f"{p.item}:{p.missing_reason}" for r in results for p in r.parts if p.state == "missing"
    )
    return {
        "tickers": len(results),
        "status": dict(Counter(r.row.status for r in results)),
        "rank_eligible": sum(r.row.rank_eligible for r in results),
        "passes_screen": dict(Counter(str(r.row.passes_screen) for r in results)),
        "excluded": dict(
            Counter(r.entry.excluded_reason for r in results if r.entry.excluded_reason)
        ),
        "risk_flags": dict(Counter(f for r in results for f in r.row.risk_flags)),
        "missing": dict(missing.most_common()),
    }


def validate(results: list[TickerResult], snapshot: Snapshot, items_per_ticker: int) -> None:
    """게시 전 불변식. 하나라도 어긋나면 게시하지 않는다 (SPEC §6.4)."""
    if len(results) != len(snapshot.tickers):
        raise ValidationError(f"결과 {len(results)} ≠ 유니버스 {len(snapshot.tickers)}")
    tickers = [r.entry.meta.ticker for r in results]
    if len(set(tickers)) != len(tickers):
        raise ValidationError("종목 중복")
    for r in results:
        if len(r.parts) != items_per_ticker:
            raise ValidationError(f"{r.entry.meta.ticker}: 항목 {len(r.parts)}개")
        if r.row.status == "scored" and r.row.coverage != 1.0:
            raise ValidationError(f"{r.entry.meta.ticker}: scored인데 관측률 {r.row.coverage}")
        if r.row.rank_eligible and r.row.status != "scored":
            raise ValidationError(f"{r.entry.meta.ticker}: 비완전 관측이 랭킹 자격")
