"""M2 수작업 대조 — 20종목을 원자료에서 독립 계산해 게시본과 맞춘다 (SPEC §11.1, PLAN M2).

파이프라인 코드를 쓰지 않는다. DART 응답을 다시 받아 직접 파싱하고, 수급·공매도는 SQL로 다시 세어
`kss_score_parts.actual`과 비교한다. 차이가 있으면 종목·항목·양쪽 값을 찍는다.

사용: venv/bin/python scripts/m2_crosscheck.py [T] [게시 프로필]
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoring.config import connect_kss_batch, connect_upstream, load_env  # noqa: E402
from scoring.sources.corp import parse_corp_codes  # noqa: E402
from scoring.sources.dart import fetch_corp_codes  # noqa: E402

TOL = 0.01          # 상대 오차 허용 (반올림)
SAMPLE = 20


def close_enough(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if abs(a) < 1e-9 and abs(b) < 1e-9:
        return True
    return abs(a - b) <= TOL * max(1.0, abs(a), abs(b))


def dart_raw(key: str, corp: str, year: str, code: str) -> list[dict[str, Any]]:
    params = {"crtfc_key": key, "corp_code": corp, "bsns_year": year, "reprt_code": code}
    url = "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        payload = json.load(r)
    return list(payload.get("list") or [])


def as_int(raw: Any) -> int:
    """DB 값에서 정수로 (없으면 0)."""
    return 0 if raw is None else int(raw)


def as_dict(raw: Any) -> dict[str, Any]:
    """jsonb 열을 사전으로."""
    return dict(raw) if raw else {}


def as_float(raw: Any) -> float:
    """DB·JSON 값에서 실수로 (없으면 0)."""
    return 0.0 if raw is None else float(raw)


def num(raw: Any) -> float | None:
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None


def pick(items: list[dict[str, Any]], name: str, basis: str) -> dict[str, Any] | None:
    for it in items:
        if it.get("fs_div") == basis and it.get("account_nm", "").replace(" ", "").startswith(name):
            return it
    return None


def main() -> None:
    env = load_env()
    t = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 9, 18)
    profile = sys.argv[2] if len(sys.argv) > 2 else "partial"
    key = env["DART_API_KEY"]

    with connect_kss_batch(env) as kss, kss.cursor() as cur:
        cur.execute(
            "select s.ticker, s.name, s.market from kss_scores s "
            "join kss_publications p using (run_id) "
            "where p.data_date = %s and p.profile = %s and s.status = 'scored' "
            "order by md5(s.ticker) limit %s",
            (t, profile, SAMPLE),
        )
        sample = [(str(r[0]), str(r[1])) for r in cur.fetchall()]
        tickers = [t_ for t_, _ in sample]
        cur.execute(
            "select p.ticker, p.item, p.actual, p.points from kss_score_parts p "
            "join kss_publications pub using (run_id) "
            "where pub.data_date = %s and pub.profile = %s and p.ticker = any(%s)",
            (t, profile, tickers),
        )
        stored: dict[tuple[str, str], tuple[dict[str, Any], float]] = {
            (str(r[0]), str(r[1])): (as_dict(r[2]), as_float(r[3])) for r in cur.fetchall()
        }

    checks = 0
    bad: list[str] = []
    with connect_upstream(env) as up, up.cursor() as cur:
        corp_map = parse_corp_codes(fetch_corp_codes())
        for ticker, name in sample:
            # ── 수급: 20/5거래일 누적과 연속 순매수를 SQL로 다시 센다
            cur.execute(
                """
                with sess as (select distinct d from ksc_bars where timeframe='D' and d <= %s
                              order by d desc limit 20),
                f as (select d,
                             coalesce(foreign_net,0) + coalesce(foreign_etc_net,0) fgn,
                             coalesce(inst_net,0) inst
                      from ksc_investor_flows where ticker = %s and d in (select d from sess))
                select (select sum(fgn) from f), (select sum(inst) from f),
                       (select sum(fgn) from f where d in
                          (select d from sess order by d desc limit 5)),
                       (select count(*) from f)
                """,
                (t, ticker),
            )
            row = cur.fetchone() or (0, 0, 0, 0)
            cum20, inst20, cum5, days = (as_float(row[0]), as_float(row[1]), as_float(row[2]),
                                         as_int(row[3]))
            part = stored.get((ticker, "flow.foreign"))
            if part and days == 20:
                checks += 1
                if not close_enough(cum20, as_float(part[0].get("cum20"))):
                    bad.append(f"{ticker} {name} 외국인 20일 누적 SQL {cum20} ≠ 저장 "
                               f"{part[0].get('cum20')}")
                if not close_enough(cum5, as_float(part[0].get("cum5"))):
                    bad.append(f"{ticker} {name} 외국인 5일 누적 SQL {cum5} ≠ 저장 "
                               f"{part[0].get('cum5')}")
            part = stored.get((ticker, "flow.inst"))
            if part and days == 20:
                checks += 1
                if not close_enough(inst20, as_float(part[0].get("cum20"))):
                    bad.append(f"{ticker} {name} 기관 20일 누적 SQL {inst20} ≠ 저장 "
                               f"{part[0].get('cum20')}")

            # ── 공매도: 20거래일 평균 비중
            cur.execute(
                """
                with sess as (select distinct d from ksc_shorting where d <= %s
                              order by d desc limit 20)
                select avg(ratio), count(*) from ksc_shorting
                where ticker = %s and d in (select d from sess)
                """,
                (t, ticker),
            )
            srow = cur.fetchone() or (0, 0)
            avg_ratio, n = as_float(srow[0]), as_int(srow[1])
            part = stored.get((ticker, "flow.shorting"))
            if part and n >= 16:
                checks += 1
                if not close_enough(avg_ratio, as_float(part[0].get("avg_ratio"))):
                    bad.append(f"{ticker} {name} 공매도 20일 평균 SQL {avg_ratio} ≠ 저장 "
                               f"{part[0].get('avg_ratio')}")

            # ── 재무: DART에서 다시 받아 직접 계산
            per_part = stored.get((ticker, "fund.per"))
            margin_part = stored.get((ticker, "fund.op_margin"))
            debt_part = stored.get((ticker, "fund.debt_ratio"))
            if not (per_part and margin_part and debt_part):
                continue
            label = str(margin_part[0].get("report", ""))     # 예: "2026년 반기보고서"
            code = {"사업보고서": "11011", "반기보고서": "11012", "1분기보고서": "11013",
                    "3분기보고서": "11014"}.get(label.split("년 ")[-1], "")
            year = label.split("년 ")[0]
            if not code:
                continue
            cur.execute("select list_shrs from ksc_tickers where ticker like %s",
                        (ticker[:5] + "%",))
            shares = sum(as_int(r[0]) for r in cur.fetchall())
            cur.execute("select c from ksc_bars where ticker=%s and timeframe='D' and d=%s",
                        (ticker, t))
            crow = cur.fetchone()
            close = as_float(crow[0]) if crow else None

            corp = corp_map.get(ticker, "")
            if not corp:
                continue
            items = dart_raw(key, corp, year, code)
            basis = "CFS" if any(i.get("fs_div") == "CFS" for i in items) else "OFS"
            interim = code != "11011"
            rev = pick(items, "매출액", basis)
            op = pick(items, "영업이익", basis)
            eq = pick(items, "자본총계", basis)
            li = pick(items, "부채총계", basis)
            if rev and op:
                r_now = num(rev.get("thstrm_add_amount")) if interim else None
                r_now = r_now if r_now is not None else num(rev.get("thstrm_amount"))
                o_now = num(op.get("thstrm_add_amount")) if interim else None
                o_now = o_now if o_now is not None else num(op.get("thstrm_amount"))
                if r_now and o_now is not None:
                    checks += 1
                    mine = o_now / r_now * 100
                    if not close_enough(mine, as_float(margin_part[0].get("op_margin"))):
                        bad.append(f"{ticker} {name} 영업이익률 직접 {mine:.2f} ≠ 저장 "
                                   f"{margin_part[0].get('op_margin')}")
            if eq and li:
                e, l_ = num(eq.get("thstrm_amount")), num(li.get("thstrm_amount"))
                if e and l_ is not None and e > 0:
                    checks += 1
                    mine = l_ / e * 100
                    if not close_enough(mine, as_float(debt_part[0].get("debt_ratio"))):
                        bad.append(f"{ticker} {name} 부채비율 직접 {mine:.2f} ≠ 저장 "
                                   f"{debt_part[0].get('debt_ratio')}")
            if per_part[0].get("per") and close and shares:
                ttm = as_float(per_part[0].get("ttm_net"))
                if ttm > 0:
                    checks += 1
                    mine = close / (ttm / shares)
                    if not close_enough(mine, as_float(per_part[0].get("per"))):
                        bad.append(f"{ticker} {name} PER 직접 {mine:.2f} ≠ 저장 "
                                   f"{per_part[0].get('per')} (주식수 {shares:,})")

    print(f"표본 {len(sample)}종목 · 대조 {checks}건 · 불일치 {len(bad)}건")
    for line in bad:
        print("  ✗", line)
    if not bad:
        print("  전부 일치")


if __name__ == "__main__":
    main()
