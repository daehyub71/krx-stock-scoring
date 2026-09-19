"""회귀 픽스처 추출 — 유형별 25종목 × 400거래일 일봉 (공개 시장 데이터).

사용: venv/bin/python scripts/export_fixture.py [T]
출력: tests/fixtures/{tickers,bars,calendar,meta}.* — 테스트는 네트워크 없이 이 파일만 읽는다.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoring.config import connect_upstream  # noqa: E402
from scoring.rules import load_rules  # noqa: E402
from scoring.sources import upstream  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures"

# (유형, SQL — 조건에 맞는 종목 하나 이상)
PICKS = [
    ("large_kospi", "select ticker from ksc_tickers where ticker in ('005930','000660','005380')"),
    ("kosdaq", "select ticker from ksc_tickers where market='KOSDAQ' and right(ticker,1)='0' "
               "and name not like '%%스팩%%' order by mktcap desc nulls last limit 4"),
    ("preferred", "select ticker from ksc_tickers where ticker in ('005935','005385')"),
    ("spac", "select ticker from ksc_tickers where name like '%%스팩%%' order by ticker limit 2"),
    ("special", "select ticker from ksc_tickers where ticker in ('105560','055550')"),
    ("reit", "select ticker from ksc_tickers where name like '%%리츠%%' order by ticker limit 1"),
    ("alnum", "select ticker from ksc_tickers where ticker ~ '[A-Z]' and right(ticker,1)='0' "
              "order by ticker limit 1"),
    ("halted", "select ticker from ksc_bars where timeframe='D' and d=%(t)s and v=0 "
               "order by ticker limit 2"),
    ("new_listing", "select ticker from ksc_bars where timeframe='D' and d>=%(start)s "
                    "group by ticker having count(*) < 100 order by ticker limit 2"),
    ("no_bar_on_t", "select tk.ticker from ksc_tickers tk where not exists (select 1 from "
                    "ksc_bars b where b.ticker=tk.ticker and b.timeframe='D' and b.d=%(t)s) "
                    "order by ticker limit 1"),
    ("drift", "select ticker from ksc_tickers where ticker = any(%(drift)s) "
              "order by ticker limit 2"),
    ("mid_kospi", "select ticker from ksc_tickers where market='KOSPI' and right(ticker,1)='0' "
                  "order by mktcap desc nulls last offset 300 limit 3"),
]


def main() -> None:
    rules = load_rules(ROOT / "rules" / "v0.toml")
    requested = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 9, 18)
    with connect_upstream() as conn:
        info = upstream.load_calendar(conn, requested)
        window = rules.section("technical")["window_sessions"]
        sessions = tuple(s for s in info.cal.sessions if s <= info.t)[-window:]
        meta = upstream.load_meta(conn)
        drift = list((meta.get("drift") or {}).get("drifted") or [])
        chosen: dict[str, str] = {}
        with conn.cursor() as cur:
            for kind, sql in PICKS:
                cur.execute(sql, {"t": info.t, "start": sessions[0], "drift": drift})
                for (tk,) in cur.fetchall():
                    chosen.setdefault(str(tk), kind)
        snap = upstream.load_snapshot(conn, info, window, frozenset(chosen))

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "tickers.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "name", "market", "sector", "kind"])
        for m in snap.tickers:
            w.writerow([m.ticker, m.name, m.market, m.sector, chosen[m.ticker]])
    with (OUT / "bars.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "d", "o", "h", "l", "c", "v", "a"])
        for tk in sorted(snap.bars):
            for b in snap.bars[tk]:
                amount = "" if b.a is None else b.a
                w.writerow([tk, b.d.isoformat(), b.o, b.h, b.l, b.c, b.v, amount])
    (OUT / "calendar.txt").write_text("\n".join(s.isoformat() for s in sessions) + "\n")
    (OUT / "meta.json").write_text(json.dumps(
        {"t": info.t.isoformat(), "drifted": sorted(set(drift) & set(chosen)),
         "source": snap.source_id, "exported_from": "ksc_bars (public market data)"},
        ensure_ascii=False, indent=2) + "\n")
    print(f"{len(snap.tickers)}종목 · {snap.rows}행 · T={info.t}")
    for tk, kind in sorted(chosen.items(), key=lambda x: x[1]):
        print(f"  {kind:12s} {tk}")


if __name__ == "__main__":
    main()
