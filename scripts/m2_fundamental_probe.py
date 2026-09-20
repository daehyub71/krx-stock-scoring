"""M2 기본 36 실데이터 분석 (v2.7) — 계산만 하고 kss에 쓰지 않는다.

사용: venv/bin/python scripts/m2_fundamental_probe.py [T] > docs/m2/fundamental_probe.txt
출력: 호출 수·시간, 항목별 상태·점수 분포, 흑자전환·자본잠식·stale 건수, 표본 종목 근거.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoring.config import connect_upstream, load_env  # noqa: E402
from scoring.domain.financial import read_statement  # noqa: E402
from scoring.domain.fundamental import (  # noqa: E402
    MarketData,
    score_fundamental,
    sector_stats,
    share_counts,
    valuation,
)
from scoring.domain.universe import classify  # noqa: E402
from scoring.rules import load_rules  # noqa: E402
from scoring.sources import dart, upstream  # noqa: E402
from scoring.sources.corp import parse_corp_codes  # noqa: E402
from scoring.sources.dart_fin import fetch_reports  # noqa: E402
from scoring.sources.krx import fetch_dividends  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ("005930", "000660", "005380", "035720", "105560", "252990")


def main() -> None:
    env = load_env()
    rules = load_rules(ROOT / "rules" / "v0.toml")
    req = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    timing: dict[str, float] = {}

    t0 = time.monotonic()
    with connect_upstream(env) as conn:
        info = upstream.load_calendar(conn, req)
        snap = upstream.load_snapshot(conn, info, rules.section("technical")["window_sessions"])
    t = snap.t
    timing["upstream"] = round(time.monotonic() - t0, 1)

    t0 = time.monotonic()
    div = fetch_dividends(t, env)
    timing["pykrx_div"] = round(time.monotonic() - t0, 1)
    with connect_upstream(env) as conn, conn.cursor() as cur:
        cur.execute("select ticker, list_shrs from ksc_tickers")
        share_rows: list[tuple[str, int | None]] = []
        for ticker, listed in cur.fetchall():
            share_rows.append((str(ticker), int(listed) if isinstance(listed, int) else None))
        shares = share_counts(share_rows,
                              rules.section("fundamental")["preferred_prefix_len"])

    t0 = time.monotonic()
    corp_map = parse_corp_codes(dart.fetch_corp_codes())
    timing["corp_codes"] = round(time.monotonic() - t0, 1)

    entries = {}
    for m in snap.tickers:
        bars = tuple(b for b in snap.bars.get(m.ticker, ()) if b.d <= t)
        bar_t = bars[-1] if bars and bars[-1].d == t else None
        entries[m.ticker] = classify(m, n_daily=len(bars), bar_on_t=bar_t,
                                     drifted=m.ticker in snap.drifted, rules=rules)
    general = [e for e in entries.values() if not e.excluded_reason]
    closes = {tk: bars[-1].c for tk, bars in snap.bars.items() if bars and bars[-1].d == t}
    market_data: dict[str, MarketData] = {}
    for e in general:
        tk = e.meta.ticker
        if tk in closes and shares.get(tk):
            d = div.get(tk)
            market_data[tk] = MarketData(close=float(closes[tk]), shares=shares[tk],
                                         div_yield=d.div_yield if d else None,
                                         dps=d.dps if d else None)

    wanted = sorted({corp_map[e.meta.ticker] for e in general if e.meta.ticker in corp_map})
    t0 = time.monotonic()
    fetched = fetch_reports(wanted, t, dart.get_json,
                            rules.section("fundamental")["filing_lag_days"])
    timing["dart_reports"] = round(time.monotonic() - t0, 1)

    # 업종 중앙값은 우리가 계산한 PER·PBR로 만든다 (v2.6)
    statements: dict[str, tuple[Any, Any]] = {}
    val_rows = []
    for e in general:
        corp = corp_map.get(e.meta.ticker)
        per_r = fetched.periodic.get(corp) if corp else None
        ann_r = fetched.annual.get(corp) if corp else None
        st = (read_statement(per_r) if per_r else None, read_statement(ann_r) if ann_r else None)
        statements[e.meta.ticker] = st
        v = valuation(market_data.get(e.meta.ticker), st[0], st[1], rules)
        if v.per or v.pbr:
            val_rows.append((e.meta.market, e.meta.sector, v.per or 0.0, v.pbr or 0.0))
    stats = sector_stats(val_rows, rules.item("fund.per").params["sector_min_samples"])

    states: dict[str, Counter[str]] = {}
    points: dict[str, Counter[float]] = {}
    risks: Counter[str] = Counter()
    turnaround = 0
    basis: Counter[str] = Counter()
    report_kind: Counter[str] = Counter()
    samples = {}
    rows_out = []
    for e in general:
        tk = e.meta.ticker
        periodic, annual = statements[tk]
        if periodic:
            report_kind[periodic.label[5:]] += 1
        res = score_fundamental(e, market_data.get(tk), stats, periodic, annual, t, rules)
        basis[f"ttm:{res.valuation.basis}"] += 1
        risks.update(res.risk_flags)
        avail = [p for p in res.parts if p.state in ("observed", "adverse_defined")]
        rows_out.append([tk, e.meta.name, e.meta.market, e.meta.sector,
                         "special" if "special_sector" in e.classification else "",
                         sum(p.max for p in avail), sum(p.points or 0 for p in avail),
                         *[(p.points if p.points is not None else p.state) for p in res.parts],
                         ";".join(res.risk_flags)])
        for p in res.parts:
            key = p.state if p.state != "missing" else f"missing:{p.missing_reason}"
            states.setdefault(p.item, Counter())[key] += 1
            if p.points is not None:
                points.setdefault(p.item, Counter())[p.points] += 1
            if p.item == "fund.growth" and p.actual.get("op_turnaround"):
                turnaround += 1
            if p.item == "fund.per" and "median_basis" in p.actual:
                basis[p.actual["median_basis"]] += 1
        if tk in SAMPLES:
            samples[tk] = {p.item: {"state": p.state, "points": p.points, "note": p.note,
                                    **{k: (round(v, 2) if isinstance(v, float) else v)
                                       for k, v in p.actual.items()}}
                           for p in res.parts}

    out = {
        "t": t.isoformat(), "timing_s": timing, "tickers_general": len(general),
        "div_rows": len(div), "market_data": len(market_data), "corp_mapped": len(wanted),
        "dart_calls": fetched.calls, "dart_rounds": fetched.rounds,
        "periodic_found": len(fetched.periodic), "annual_found": len(fetched.annual),
        "periodic_report_kind": dict(report_kind.most_common()),
        "states": {k: dict(v.most_common()) for k, v in states.items()},
        "points": {k: dict(sorted(v.items())) for k, v in points.items()},
        "risk_flags": dict(risks), "op_turnaround": turnaround,
        "per_median_basis": dict(basis), "samples": samples,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    csv_path = ROOT / "docs" / "m2" / f"fundamental_scores_{t:%Y%m%d}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "name", "market", "sector", "special", "available_max", "points",
                    "per", "pbr", "op_margin", "growth", "roe", "debt_ratio", "div",
                    "risk_flags"])
        w.writerows(rows_out)


if __name__ == "__main__":
    main()
