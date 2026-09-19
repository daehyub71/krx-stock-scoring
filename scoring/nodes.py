"""그래프 노드 — domain·store 호출을 감싸는 얇은 층.

노드는 (상태, 문맥)을 받아 **상태 갱신분만** 돌려준다. 계산 규칙은 여기 두지 않는다.
dry_run이면 kss에 아무것도 쓰지 않는다.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from scoring import checks, compute
from scoring.sources import upstream
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
    if ctx.run_id is not None:
        writer.write_source_checks(_kss(ctx), ctx.run_id, rows)
    return {"gate_ok": ok, "stats": {"gate": {c.market: c.coverage for c in rows if c.coverage}}}


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
    if ctx.run_id is not None:
        writer.update_run(_kss(ctx), ctx.run_id, "computing")
    return {}


def compute_scores(state: RunState, ctx: RunContext) -> RunState:
    """전 종목 계산."""
    assert ctx.snapshot is not None
    started = time.monotonic()
    ctx.results = compute.compute_technical(ctx.snapshot, ctx.rules)
    _timed(ctx, "compute", started)
    return {}


def validate(state: RunState, ctx: RunContext) -> RunState:
    """게시 전 불변식 — 어긋나면 예외(이전 게시본 유지)."""
    assert ctx.snapshot is not None and ctx.results is not None
    if ctx.run_id is not None:
        writer.update_run(_kss(ctx), ctx.run_id, "validating")
    compute.validate(ctx.results, ctx.snapshot, items_per_ticker=5)
    summary = compute.summarize(ctx.results)
    summary.update(rows=ctx.snapshot.rows, window_start=ctx.snapshot.window_start.isoformat(),
                   source=ctx.snapshot.source_id)
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
    stored = writer.count_rows(kss, ctx.run_id)
    if stored != written:
        raise compute.ValidationError(f"저장 행 수 불일치: 기대 {written}, 실제 {stored}")
    _timed(ctx, "persist", started)
    return {"stats": {**state.get("stats", {}), "written": written}}


def publish(state: RunState, ctx: RunContext) -> RunState:
    """게시 포인터 교체 (한 트랜잭션)."""
    if state.get("dry_run") or ctx.run_id is None:
        return {"status": "dry_run"}
    assert ctx.snapshot is not None
    stats = {**state.get("stats", {}), "timings": ctx.timings}
    writer.update_run(_kss(ctx), ctx.run_id, "validating", stats=stats)
    ctx.published_at = writer.publish(
        _kss(ctx), ctx.run_id, ctx.snapshot.t, state["profile"], "published",
        reason=f"{state.get('trigger', 'manual')} run",
    )
    return {"status": "published"}


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
