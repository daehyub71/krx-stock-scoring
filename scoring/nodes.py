"""그래프 노드 — domain·store 호출을 감싸는 얇은 층.

노드는 (상태, 문맥)을 받아 **상태 갱신분만** 돌려준다. 계산 규칙은 여기 두지 않는다.
dry_run이면 kss에 아무것도 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timedelta
from typing import Any

from scoring import checks, compute
from scoring.config import load_env
from scoring.domain.disclosure import DisclosureRow
from scoring.domain.financial import read_statement
from scoring.domain.fundamental import MarketData, sector_stats, share_counts, valuation
from scoring.domain.lexicon import from_rules, news_lexicon
from scoring.domain.news import Article
from scoring.sources import dart, naver, upstream
from scoring.sources import disclosure as disclosure_src
from scoring.sources.corp import parse_corp_codes
from scoring.sources.dart_fin import fetch_reports
from scoring.sources.krx import fetch_dividends
from scoring.state import RunContext, RunState
from scoring.store import writer

WEEKLY_OR_MONTHLY_STRATEGIES = frozenset({"mtf", "pullback", "squeeze", "turnaround"})


def _timed(ctx: RunContext, name: str, started: float) -> None:
    ctx.timings[name] = round(time.monotonic() - started, 2)


def _kss(ctx: RunContext) -> Any:
    if ctx.kss is None:
        raise RuntimeError("kss 연결 없음")
    return ctx.kss


def calendar(state: RunState, ctx: RunContext) -> RunState:
    """거래일 달력과 T."""
    started = time.monotonic()
    req = state.get("requested_t")
    ctx.calendar = upstream.load_calendar(ctx.upstream, date.fromisoformat(req) if req else None)
    _timed(ctx, "calendar", started)
    return {"t": ctx.calendar.t.isoformat()}


def create_run(state: RunState, ctx: RunContext) -> RunState:
    """실행 행을 먼저 남긴다 (실패해도 기록이 있다)."""
    if state.get("dry_run"):
        return {"run_id": None}
    ctx.run_id = writer.create_run(
        _kss(ctx), data_date=date.fromisoformat(state["t"]), profile=state["profile"],
        rules=ctx.rules, trigger=state.get("trigger", "manual"), code_sha=ctx.code_sha,
    )
    writer.update_run(_kss(ctx), ctx.run_id, "checking")
    return {"run_id": str(ctx.run_id)}


def gate(state: RunState, ctx: RunContext) -> RunState:
    """출처별 품질 게이트."""
    assert ctx.calendar is not None
    t = ctx.calendar.t
    counts = upstream.gate_counts(ctx.upstream, t)
    meta = upstream.load_meta(ctx.upstream)
    window = ctx.rules.section("technical")["window_sessions"]
    recent = tuple(d for d in ctx.calendar.mismatch if d >= ctx.calendar.cal.sessions[-window])
    ok, rows = checks.gate_bars(counts, t, meta, recent)

    # 보조 갈래는 **막지 않는다** — 모자라면 degraded로 게시한다 (SPEC §6.3)
    aux_ok = True
    for source, c in upstream.aux_counts(ctx.upstream, t).items():
        passed, check = checks.gate_aux(source, c, t)
        aux_ok &= passed
        rows.append(check)
    ctx.upstream.rollback()

    if ctx.run_id is not None:
        writer.write_source_checks(_kss(ctx), ctx.run_id, rows)
    decision = checks.publish_decision(ok, aux_ok)
    return {
        "gate_ok": ok,
        "publish_decision": decision,
        "stats": {"gate": {f"{c.source}:{c.market}": c.coverage for c in rows if c.coverage}},
    }


def route_after_gate(state: RunState) -> str:
    """게이트 통과면 적재로, 아니면 대기 종료로."""
    return "load" if state.get("gate_ok") else "wait"


def wait(state: RunState, ctx: RunContext) -> RunState:
    """상위 미완 — 게시하지 않고 복구 대상으로 남긴다."""
    if ctx.run_id is not None:
        writer.update_run(_kss(ctx), ctx.run_id, "waiting_upstream", finished=True)
    return {"status": "waiting_upstream"}


def load(state: RunState, ctx: RunContext) -> RunState:
    """T 기준 창의 일봉·종목·메타 스냅샷."""
    assert ctx.calendar is not None
    started = time.monotonic()
    ctx.snapshot = upstream.load_snapshot(
        ctx.upstream, ctx.calendar, ctx.rules.section("technical")["window_sessions"],
        ctx.tickers_filter,
    )
    ctx.upstream.rollback()  # 읽기 트랜잭션을 닫는다 (풀러 점유 최소화)
    _timed(ctx, "load", started)
    if state["profile"] != "technical":
        started = time.monotonic()
        ctx.fundamentals = _load_fundamentals(ctx)
        _timed(ctx, "load_fundamentals", started)
    if state["profile"] == "common":
        started = time.monotonic()
        ctx.events = _load_events(ctx, state)
        _timed(ctx, "load_events", started)
    if ctx.run_id is not None:
        writer.update_run(_kss(ctx), ctx.run_id, "computing")
    return {}


# 점수에 쓰는 계정만 보존한다 — 원본 전체를 매일 복제하지 않는다 (SPEC §7.3)
KEEP_ACCOUNTS = ("매출액", "영업이익", "당기순이익", "자본총계", "부채총계")


def _version_row(rep: Any, st: Any) -> tuple[Any, ...]:
    """보고서 하나를 `kss_financial_versions` 한 행으로 (내용 해시 포함)."""
    items = [
        it for it in rep.items
        if any(str(it.get("account_nm", "")).replace(" ", "").startswith(a) for a in KEEP_ACCOUNTS)
    ]
    payload = json.dumps(items, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    from psycopg.types.json import Jsonb  # noqa: PLC0415 — 저장 층 어댑터

    return (rep.corp_code, rep.bsns_year, rep.reprt_code, rep.rcept_no, digest,
            st.basis, st.period_end, rep.rcept_date, Jsonb(items))


def _load_fundamentals(ctx: RunContext) -> compute.Fundamentals:
    """기본 축 입력 — 배당(pykrx 2회) · 합산 주식 수 · DART 보고서 · 업종 중앙값."""
    assert ctx.snapshot is not None
    snap, rules = ctx.snapshot, ctx.rules
    env = load_env()
    cfg = rules.section("fundamental")
    div = fetch_dividends(snap.t, env)
    with ctx.upstream.cursor() as cur:
        cur.execute("select ticker, list_shrs from ksc_tickers")
        rows: list[tuple[str, int | None]] = []
        for ticker, listed in cur.fetchall():
            rows.append((str(ticker), int(listed) if isinstance(listed, int) else None))
    ctx.upstream.rollback()
    shares = share_counts(rows, cfg["preferred_prefix_len"])

    market: dict[str, MarketData] = {}
    for meta in snap.tickers:
        bars = snap.bars.get(meta.ticker, ())
        if not bars or bars[-1].d != snap.t or not shares.get(meta.ticker):
            continue
        d = div.get(meta.ticker)
        market[meta.ticker] = MarketData(
            close=float(bars[-1].c), shares=shares[meta.ticker],
            div_yield=d.div_yield if d else None, dps=d.dps if d else None)

    flows = upstream.load_flows(ctx.upstream, snap.window_start, snap.t)
    shorts = upstream.load_shorting(ctx.upstream, snap.window_start, snap.t)
    ctx.upstream.rollback()

    corp_bytes = dart.fetch_corp_codes()
    corp_map = parse_corp_codes(corp_bytes)
    corp_version = hashlib.sha256(corp_bytes).hexdigest()[:16]
    general = [m for m in snap.tickers if m.ticker in corp_map]
    fetched = fetch_reports(sorted({corp_map[m.ticker] for m in general}), snap.t,
                            dart.get_json, cfg["filing_lag_days"])
    periodic, annual = {}, {}
    versions: dict[tuple[str, str], tuple[Any, ...]] = {}
    for m in general:
        corp = corp_map[m.ticker]
        for rep in (fetched.periodic.get(corp), fetched.annual.get(corp)):
            if rep is None:
                continue
            st = read_statement(rep)
            if rep is fetched.periodic.get(corp):
                periodic[m.ticker] = st
            if rep is fetched.annual.get(corp):
                annual[m.ticker] = st
            versions[(rep.corp_code, rep.rcept_no)] = _version_row(rep, st)

    # 업종 중앙값은 우리가 계산한 PER·PBR로 만든다 (SPEC v2.6)
    val_rows = []
    for m in snap.tickers:
        v = valuation(market.get(m.ticker), periodic.get(m.ticker), annual.get(m.ticker), rules)
        if v.per or v.pbr:
            val_rows.append((m.market, m.sector, v.per or 0.0, v.pbr or 0.0))
    stats = sector_stats(val_rows, rules.item("fund.per").params["sector_min_samples"])
    ctx.dart_calls = fetched.calls
    return compute.Fundamentals(market=market, periodic=periodic, annual=annual, stats=stats,
                                flows=flows, shorts=shorts, corp_map_version=corp_version,
                                corp_map=corp_map, report_versions=tuple(versions.values()))


# ── M3 공시·뉴스 ────────────────────────────────────────────────

# 이미 받은 날도 이만큼은 다시 훑는다 — 늦게 접수되거나 정정된 공시를 놓치지 않기 위해 (SPEC §5.5)
DISCLOSURE_REFETCH_DAYS = 2


def _sync_disclosures(ctx: RunContext, t: date, window_days: int) -> int:
    """창에 필요한 공시를 DART에서 받아 `kss_disclosures`에 쌓는다.

    공시는 불변이라 매번 30일을 다시 받을 이유가 없다. 저장된 마지막 접수일에서 이어 받되,
    최근 며칠은 겹쳐 다시 훑는다.

    Returns:
        이번에 받은 행 수.
    """
    start = t - timedelta(days=window_days - 1)
    kss = _kss(ctx)
    with kss.cursor() as cur:
        cur.execute("select max(rcept_dt) from kss_disclosures")
        row = cur.fetchone()
    last = row[0] if row and row[0] else None
    since = max(start, last - timedelta(days=DISCLOSURE_REFETCH_DAYS)) if last else start
    records = disclosure_src.fetch_days(since, t)
    ctx.dart_calls += 1
    rows = [
        (r.rcept_no, r.corp_code, r.stock_code, r.corp_name, r.corp_cls, r.report_nm,
         r.rcept_dt, r.flr_nm, r.rm, r.norm_name, r.corrected, r.note)
        for r in records
    ]
    writer.write_disclosures(kss, rows)
    return len(rows)


def _read_disclosure_window(
    ctx: RunContext, t: date, window_days: int
) -> dict[str, list[DisclosureRow]]:
    """창 안의 공시를 종목별로 읽는다 (상장 종목코드가 있는 것만)."""
    start = t - timedelta(days=window_days - 1)
    out: dict[str, list[DisclosureRow]] = {}
    with _kss(ctx).cursor() as cur:
        cur.execute(
            "select stock_code, rcept_no, rcept_dt, report_nm from kss_disclosures "
            "where rcept_dt between %s and %s and stock_code <> '' order by rcept_dt",
            (start, t))
        for stock, rcept_no, rcept_dt, report_nm in cur.fetchall():
            out.setdefault(str(stock), []).append(
                DisclosureRow(rcept_no=str(rcept_no), rcept_dt=rcept_dt, report_nm=str(report_nm)))
    return out


# 종목 사이 간격. 붙여서 2,585회를 돌리면 지속 호출로 막힌다 (2026-09-23 실측 815종목 실패).
# 호출 자체가 90~135ms라 이 값이면 초당 6~7회다.
NEWS_DELAY = 0.05


def _fetch_news(
    ctx: RunContext, env: dict[str, str]
) -> tuple[dict[str, list[Article]], dict[str, str]]:
    """전 종목 뉴스. 한 종목이 실패해도 나머지는 계속한다 — 상태로 남긴다."""
    assert ctx.snapshot is not None
    articles: dict[str, list[Article]] = {}
    status: dict[str, str] = {}
    for meta in ctx.snapshot.tickers:
        try:
            articles[meta.ticker] = naver.search_news(meta.name, env=env)
            status[meta.ticker] = "observed"
        except naver.NaverError:
            status[meta.ticker] = "source_error"
        time.sleep(NEWS_DELAY)
    return articles, status


def _load_events(ctx: RunContext, state: RunState) -> compute.Events:
    """공시·뉴스 입력을 모은다 (common 프로필)."""
    assert ctx.snapshot is not None
    t = ctx.snapshot.t
    window_days = int(ctx.rules.item("disc.dart").params["window_days"])
    disc_lex, news_lex = from_rules(), news_lexicon()
    if not state.get("dry_run") and ctx.kss is not None:
        writer.write_lexicon_version(_kss(ctx), disc_lex.version, "disclosure", disc_lex.entries())
        writer.write_lexicon_version(_kss(ctx), news_lex.version, "news", news_lex.entries())
        fetched = _sync_disclosures(ctx, t, window_days)
        ctx.timings["disclosures_fetched"] = float(fetched)
        disclosures = _read_disclosure_window(ctx, t, window_days)
    else:
        disclosures = {}
    # 창의 끝은 T의 자정이 아니라 **실행 시각**이다 (SPEC §5.1·§5.5).
    # 네이버는 최신순 20건만 주고 날짜 조건이 없어서, T 자정으로 자르면 활발한 종목은
    # 받은 20건이 전부 창 밖으로 나간다 (2026-09-23 실측: 4종목 모두 fetched 20 · in_window 0).
    cutoff = datetime.now().replace(microsecond=0)
    lag_days = int(ctx.rules.item("news.naver").params.get("max_lag_days", 3))
    backdated = (cutoff.date() - t).days > lag_days
    env = load_env()
    if backdated:
        # 소급 실행 — 그 시점 기사를 받을 방법이 없다. 오늘 기사로 과거 점수를 만들지 않는다
        articles: dict[str, list[Article]] = {}
        status = {meta.ticker: "not_queried" for meta in ctx.snapshot.tickers}
    else:
        articles, status = _fetch_news(ctx, env)
    return compute.Events(
        disclosures=disclosures, disclosure_lexicon=disc_lex,
        news=articles, news_status=status, news_lexicon=news_lex,
        cutoff=cutoff,
        disclosure_queried=bool(disclosures) or not state.get("dry_run", False),
    )


def compute_scores(state: RunState, ctx: RunContext) -> RunState:
    """전 종목 계산."""
    assert ctx.snapshot is not None
    started = time.monotonic()
    ctx.results = compute.compute_scores(ctx.snapshot, ctx.rules, state["profile"],
                                         ctx.fundamentals, ctx.events)
    _timed(ctx, "compute", started)
    return {}


def validate(state: RunState, ctx: RunContext) -> RunState:
    """게시 전 불변식 — 어긋나면 예외(이전 게시본 유지)."""
    assert ctx.snapshot is not None and ctx.results is not None
    if ctx.run_id is not None:
        writer.update_run(_kss(ctx), ctx.run_id, "validating")
    compute.validate(ctx.results, ctx.snapshot,
                     items_per_ticker=compute.items_per_ticker(state["profile"]))
    summary = compute.summarize(ctx.results)
    summary.update(rows=ctx.snapshot.rows, window_start=ctx.snapshot.window_start.isoformat(),
                   source=ctx.snapshot.source_id, dart_calls=ctx.dart_calls,
                   drift={"checked": ctx.snapshot.drift_checked,
                          "refetched": ctx.snapshot.drift_listed,
                          "failed": ctx.snapshot.drift_failed,
                          "held": len(ctx.snapshot.drifted)})
    return {"stats": {**state.get("stats", {}), "summary": summary}}


def persist(state: RunState, ctx: RunContext) -> RunState:
    """청크 저장 후 행 수 대조."""
    if state.get("dry_run") or ctx.run_id is None:
        return {}
    assert ctx.snapshot is not None and ctx.results is not None
    started = time.monotonic()
    kss = _kss(ctx)
    written = writer.write_results(kss, ctx.run_id, ctx.snapshot.t, state["profile"],
                                   ctx.results, ctx.snapshot.source_id)
    if ctx.fundamentals is not None:
        f = ctx.fundamentals
        writer.write_sector_stats(kss, ctx.run_id, f.stats.rows())
        writer.write_financial_versions(kss, f.report_versions)
        writer.write_corp_map(kss, f.corp_map_version, f.corp_map)
    if ctx.events is not None:
        _persist_events(ctx, kss)
    stored = writer.count_rows(kss, ctx.run_id)
    if stored != written:
        raise compute.ValidationError(f"저장 행 수 불일치: 기대 {written}, 실제 {stored}")
    _timed(ctx, "persist", started)
    return {"stats": {**state.get("stats", {}), "written": written}}


def _persist_events(ctx: RunContext, kss: Any) -> None:
    """판정된 공시 사건과 뉴스 관측을 남긴다 (M3).

    사건은 **사전 버전과 함께** 쌓는다 — 사전을 고쳐도 과거 판정이 덮이지 않는다.
    뉴스는 점수에 쓴 기사만 담는다. 미조회·실패도 그대로 상태로 남긴다.
    """
    assert ctx.events is not None and ctx.results is not None and ctx.run_id is not None
    ev = ctx.events
    risk_rows: list[tuple[Any, ...]] = []
    news_rows: list[tuple[Any, ...]] = []
    for result in ctx.results:
        ticker = result.entry.meta.ticker
        for part in result.parts:
            if part.item == "disc.dart":
                for e in part.actual.get("events", []):
                    risk_rows.append((
                        ticker, e["rcept_no"], e["rule"], ev.disclosure_lexicon.version,
                        e["level"], e["fatal"], e["subsidiary"], date.fromisoformat(e["rcept_dt"]),
                    ))
            elif part.item == "news.naver":
                window = part.actual.get("window") or [None, None]
                status = ("not_queried" if part.missing_reason == "not_queried"
                          else "source_error" if part.missing_reason == "source_error"
                          else part.state if part.state == "no_event" else "observed")
                news_rows.append((
                    ticker, status, result.entry.meta.name,
                    date.fromisoformat(window[0]) if window[0] else None,
                    date.fromisoformat(window[1]) if window[1] else None,
                    writer.json_value(part.actual.get("articles", [])),
                    part.points,
                    part.actual.get("lexicon_version"),
                    part.note,
                ))
    writer.write_risk_events(kss, risk_rows)
    writer.write_news_observations(kss, ctx.run_id, news_rows)
    ctx.timings["risk_events"] = float(len(risk_rows))


def publish(state: RunState, ctx: RunContext) -> RunState:
    """게시 포인터 교체 (한 트랜잭션)."""
    if state.get("dry_run") or ctx.run_id is None:
        return {"status": "dry_run"}
    assert ctx.snapshot is not None
    stats = {**state.get("stats", {}), "timings": ctx.timings}
    writer.update_run(_kss(ctx), ctx.run_id, "validating", stats=stats)
    degraded = state.get("publish_decision") == "publish_degraded"
    status = "published_degraded" if degraded else "published"
    ctx.published_at = writer.publish(
        _kss(ctx), ctx.run_id, ctx.snapshot.t, state["profile"], status,
        reason=f"{state.get('trigger', 'manual')} run" + (" (보조 갈래 부족)" if degraded else ""),
    )
    return {"status": status}


def cross(state: RunState, ctx: RunContext) -> RunState:
    """alerts 신호와 같은 기준일 점수를 잇는다 (partial_technical, SPEC §8)."""
    if state.get("dry_run") or ctx.run_id is None or ctx.published_at is None:
        return {}
    assert ctx.snapshot is not None and ctx.results is not None
    signals = upstream.load_signals(ctx.upstream, ctx.snapshot.t)
    ctx.upstream.rollback()
    by_ticker = {r.entry.meta.ticker: r for r in ctx.results}
    rows = []
    for s in signals:
        r = by_ticker.get(s.ticker)
        rows.append((
            s.d, s.ticker, s.strategy, ctx.run_id, "partial_technical", s.created_at,
            ctx.published_at, ctx.published_at <= s.created_at, s.rank_no, s.suppressed,
            r.row.status if r else None,
            r.row.axis["technical"]["points"] if r and r.row.status == "scored" else None,
            r.row.passes_screen if r else None,
            s.strategy in WEEKLY_OR_MONTHLY_STRATEGIES,
            None if r else "점수 유니버스에 없음",
        ))
    n = writer.write_signal_cross(_kss(ctx), rows)
    return {"stats": {**state.get("stats", {}), "signal_cross": n}}
