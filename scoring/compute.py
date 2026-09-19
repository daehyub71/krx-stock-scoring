"""전 종목 계산 — 스냅샷 → 종목별 (분류, 항목, 점수 행). I/O 없음."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from scoring.domain.aggregate import ScoreRow, aggregate
from scoring.domain.technical import score_technical
from scoring.domain.universe import UniverseEntry, classify
from scoring.models import Part
from scoring.rules import Rules
from scoring.sources.upstream import Snapshot


@dataclass(frozen=True)
class TickerResult:
    """한 종목의 계산 결과."""

    entry: UniverseEntry
    parts: tuple[Part, ...]
    row: ScoreRow


class ValidationError(RuntimeError):
    """게시 전 검증 실패 — 이전 게시본을 유지한다."""


def compute_technical(snapshot: Snapshot, rules: Rules) -> list[TickerResult]:
    """technical 프로필 — 전 종목 기술 5항목과 집계."""
    out = []
    for meta in snapshot.tickers:
        # T 이후 봉은 분류에도 쓰지 않는다 (SPEC §11.1 미래 입력 차단)
        bars = tuple(b for b in snapshot.bars.get(meta.ticker, ()) if b.d <= snapshot.t)
        bar_on_t = bars[-1] if bars and bars[-1].d == snapshot.t else None
        entry = classify(meta, n_daily=len(bars), bar_on_t=bar_on_t,
                         drifted=meta.ticker in snapshot.drifted, rules=rules)
        tech = score_technical(bars, snapshot.t, snapshot.cal, rules,
                               stale=meta.ticker in snapshot.drifted)
        row = aggregate(tech.parts, entry, rules, "technical", tech.passes_screen)
        out.append(TickerResult(entry=entry, parts=tech.parts, row=row))
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
