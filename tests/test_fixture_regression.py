"""실데이터 회귀 — 유형별 25종목 픽스처로 전체 계산 경로를 네트워크 없이 검사한다.

픽스처: `scripts/export_fixture.py` (2026-09-18, 공개 시장 데이터).
여기서는 분류·상태·불변식·결정성만 본다. 점수값의 정답은 합성 데이터 골든 테스트가 맡는다.
"""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from scoring.calendar import Calendar
from scoring.compute import TickerResult, compute_scores, validate
from scoring.models import DailyBar, TickerMeta
from scoring.rules import load_rules
from scoring.sources.upstream import Snapshot

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
RULES = load_rules(ROOT / "rules" / "v0.toml")


def load_fixture() -> tuple[Snapshot, dict[str, str]]:
    meta = json.loads((FIX / "meta.json").read_text(encoding="utf-8"))
    sessions = tuple(date.fromisoformat(x) for x in (FIX / "calendar.txt").read_text().split())
    kinds: dict[str, str] = {}
    tickers = []
    with (FIX / "tickers.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            tickers.append(TickerMeta(r["ticker"], r["name"], r["market"], r["sector"]))
            kinds[r["ticker"]] = r["kind"]
    bars: dict[str, list[DailyBar]] = {}
    with (FIX / "bars.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            bars.setdefault(r["ticker"], []).append(DailyBar(
                d=date.fromisoformat(r["d"]), o=int(r["o"]), h=int(r["h"]), l=int(r["l"]),
                c=int(r["c"]), v=int(r["v"]), a=int(r["a"]) if r["a"] else None))
    snap = Snapshot(
        t=date.fromisoformat(meta["t"]), cal=Calendar(sessions=sessions),
        window_start=sessions[0], tickers=tuple(tickers),
        bars={k: tuple(v) for k, v in bars.items()}, drifted=frozenset(meta["drifted"]),
        drift_checked="", drift_listed=len(meta["drifted"]), drift_failed=len(meta["drifted"]),
        upstream_meta={}, source_id=meta["source"], rows=sum(len(v) for v in bars.values()),
        load_seconds=0.0,
    )
    return snap, kinds


SNAP, KINDS = load_fixture()


@pytest.fixture(scope="module")
def results() -> dict[str, TickerResult]:
    return {r.entry.meta.ticker: r for r in compute_scores(SNAP, RULES, "technical")}


def by_kind(kind: str) -> list[str]:
    return [t for t, k in KINDS.items() if k == kind]


def test_fixture_validates(results: dict[str, TickerResult]) -> None:
    validate(list(results.values()), SNAP, items_per_ticker=5)
    assert len(results) == 25


def test_fixture_preferred_and_spac_excluded(results: dict[str, TickerResult]) -> None:
    for t in by_kind("preferred"):
        assert results[t].row.status == "excluded"
        assert results[t].entry.excluded_reason == "preferred"
    for t in by_kind("spac"):
        assert results[t].entry.excluded_reason == "spac"


def test_fixture_special_sector_not_rank_eligible(results: dict[str, TickerResult]) -> None:
    for t in by_kind("special") + by_kind("reit"):
        assert "special_sector" in results[t].entry.classification
        assert results[t].row.rank_eligible is False


def test_fixture_halted_flagged(results: dict[str, TickerResult]) -> None:
    for t in by_kind("halted"):
        assert "suspected_suspension" in results[t].row.risk_flags
        assert results[t].row.rank_eligible is False


def test_fixture_no_bar_on_t_all_unavailable(results: dict[str, TickerResult]) -> None:
    for t in by_kind("no_bar_on_t"):
        r = results[t]
        assert r.row.status == "insufficient_data"
        assert {p.missing_reason for p in r.parts} == {"unavailable"}


def test_fixture_drift_stale(results: dict[str, TickerResult]) -> None:
    for t in by_kind("drift"):
        assert {p.missing_reason for p in results[t].parts} == {"stale"}
        assert "price_drift" in results[t].row.risk_flags


def test_fixture_new_listing_not_scored(results: dict[str, TickerResult]) -> None:
    for t in by_kind("new_listing"):
        r = results[t]
        assert "new_listing" in r.entry.classification
        assert r.row.status in {"insufficient_data", "provisional"}
        assert r.row.rank_eligible is False


def test_fixture_large_caps_scored(results: dict[str, TickerResult]) -> None:
    for t in by_kind("large_kospi"):
        r = results[t]
        assert r.row.status == "scored"
        assert r.row.raw_total is not None and 0 <= r.row.raw_total <= 35
        assert r.row.passes_screen in {True, False}


def test_fixture_deterministic() -> None:
    assert compute_scores(SNAP, RULES, "technical") == compute_scores(SNAP, RULES, "technical")


def test_fixture_future_bar_does_not_change_result(results: dict[str, TickerResult]) -> None:
    t = "005930"
    last = SNAP.bars[t][-1]
    future = replace(last, d=SNAP.t + timedelta(days=3), c=last.c * 2, v=last.v * 10)
    snap2 = replace(SNAP, bars={**SNAP.bars, t: (*SNAP.bars[t], future)})
    again = {r.entry.meta.ticker: r for r in compute_scores(snap2, RULES, "technical")}
    assert again[t] == results[t]
