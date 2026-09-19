"""배치 그래프 — 이 모듈만 LangGraph를 안다 (SPEC §10 v2.3, verify N4 방식).

calendar → create_run → gate ─┬─ load → compute → validate → persist → publish → cross → END
                              └─ wait → END   (상위 미완: waiting_upstream)

노드는 실행 문맥에 묶인 클로저다. 문맥에는 대량 데이터, 상태에는 요약만 있다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from scoring import nodes
from scoring.state import RunContext, RunState

Node = Callable[[RunState, RunContext], RunState]

PIPELINE: tuple[tuple[str, Node], ...] = (
    ("calendar", nodes.calendar),
    ("create_run", nodes.create_run),
    ("gate", nodes.gate),
    ("load", nodes.load),
    ("compute", nodes.compute_scores),
    ("validate", nodes.validate),
    ("persist", nodes.persist),
    ("publish", nodes.publish),
    ("cross", nodes.cross),
)


def _bind(fn: Node, ctx: RunContext) -> Any:
    # 반환 타입은 LangGraph 노드 프로토콜(매개변수 이름 state)에 맡긴다 — 프레임워크 경계
    def node(state: RunState) -> RunState:
        return fn(state, ctx)

    node.__name__ = fn.__name__
    return node


def build_graph(ctx: RunContext) -> Any:
    """실행 문맥에 묶인 그래프를 컴파일한다."""
    g: StateGraph[RunState] = StateGraph(RunState)
    for name, fn in PIPELINE:
        g.add_node(name, _bind(fn, ctx))
    g.add_node("wait", _bind(nodes.wait, ctx))

    g.add_edge(START, "calendar")
    g.add_edge("calendar", "create_run")
    g.add_edge("create_run", "gate")
    g.add_conditional_edges("gate", nodes.route_after_gate, {"load": "load", "wait": "wait"})
    names = [n for n, _ in PIPELINE]
    for a, b in zip(names[3:], names[4:], strict=False):
        g.add_edge(a, b)
    g.add_edge("cross", END)
    g.add_edge("wait", END)
    return g.compile()
