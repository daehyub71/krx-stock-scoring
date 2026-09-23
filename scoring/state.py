"""그래프 상태와 실행 문맥 (SPEC §10 v2.3).

상태(`RunState`)에는 **참조와 요약만** 싣는다 — run_id·T·게이트 결과·통계.
시세 행·항목 근거 같은 대량 데이터는 실행 문맥(`RunContext`)에 두고, 노드는 그래프를 만들 때
문맥에 묶인다. LangGraph 체크포인트가 대량 데이터를 복제하지 않게 하려는 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypedDict
from uuid import UUID

import psycopg

from scoring.compute import Events, Fundamentals, TickerResult
from scoring.rules import Rules
from scoring.sources.upstream import CalendarInfo, Snapshot


class RunState(TypedDict, total=False):
    """노드 사이를 오가는 작은 상태."""

    profile: str
    requested_t: str | None
    trigger: str
    dry_run: bool
    t: str
    run_id: str | None
    gate_ok: bool
    publish_decision: str
    status: str
    stats: dict[str, Any]


@dataclass
class RunContext:
    """한 실행의 연결·규칙·대량 데이터."""

    rules: Rules
    upstream: psycopg.Connection[tuple[Any, ...]]
    kss: psycopg.Connection[tuple[Any, ...]] | None
    code_sha: str | None = None
    tickers_filter: frozenset[str] | None = None
    calendar: CalendarInfo | None = None
    snapshot: Snapshot | None = None
    results: list[TickerResult] | None = None
    fundamentals: Fundamentals | None = None
    events: Events | None = None
    dart_calls: int = 0
    run_id: UUID | None = None
    published_at: datetime | None = None
    timings: dict[str, float] = field(default_factory=dict)
