"""그래프 배선 — 게이트 분기와 노드 순서 (DB 없이 노드를 가짜로 바꿔 검사한다)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scoring import graph, nodes
from scoring.rules import load_rules
from scoring.state import RunContext, RunState

RULES = load_rules(Path(__file__).resolve().parents[1] / "rules" / "v0.toml")


def fake_ctx() -> RunContext:
    return RunContext(rules=RULES, upstream=None, kss=None)  # type: ignore[arg-type]


def wire(monkeypatch: pytest.MonkeyPatch, gate_ok: bool) -> list[str]:
    calls: list[str] = []

    def make(name: str, update: RunState | None = None) -> Any:
        def fn(state: RunState, ctx: RunContext) -> RunState:
            calls.append(name)
            return update or {}
        fn.__name__ = name
        return fn

    pipeline = tuple(
        (n, make(n, {"gate_ok": gate_ok} if n == "gate" else {"status": "published"}
                 if n == "publish" else None))
        for n, _ in graph.PIPELINE
    )
    monkeypatch.setattr(graph, "PIPELINE", pipeline)
    monkeypatch.setattr(nodes, "wait", make("wait", {"status": "waiting_upstream"}))
    return calls


def test_graph_full_order_when_gate_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = wire(monkeypatch, gate_ok=True)
    out = graph.build_graph(fake_ctx()).invoke({"profile": "technical", "dry_run": True})
    assert calls == ["calendar", "create_run", "gate", "load", "compute", "validate",
                     "persist", "publish", "cross"]
    assert out["status"] == "published"


def test_graph_gate_failure_waits_without_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = wire(monkeypatch, gate_ok=False)
    out = graph.build_graph(fake_ctx()).invoke({"profile": "technical", "dry_run": True})
    assert calls == ["calendar", "create_run", "gate", "wait"]
    assert out["status"] == "waiting_upstream"


def test_graph_doc_is_current() -> None:
    # 그래프를 고쳤으면 `python scripts/export_graph.py`로 docs/GRAPH.md를 다시 만든다
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "export_graph.py"
    spec = importlib.util.spec_from_file_location("export_graph", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (root / "docs" / "GRAPH.md").read_text(encoding="utf-8") == mod.render()
