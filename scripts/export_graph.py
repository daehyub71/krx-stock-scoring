"""배치 그래프를 Mermaid로 내보낸다 → docs/GRAPH.md (코드가 정본, 문서는 생성물).

사용: venv/bin/python scripts/export_graph.py
그래프를 고치고 이것을 돌리지 않으면 tests/test_graph.py가 실패한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoring.graph import build_graph  # noqa: E402
from scoring.rules import load_rules  # noqa: E402
from scoring.state import RunContext  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "GRAPH.md"
HEADER = """# GRAPH.md — 채점 배치 그래프 (자동 생성)

> `scripts/export_graph.py`가 `scoring/graph.py`의 컴파일된 그래프에서 만든다.
> **직접 고치지 않는다.**
> 설명과 그림은 `PLAN.md` §2.3.

```mermaid
"""


def render() -> str:
    """현재 코드의 그래프를 GRAPH.md 본문으로."""
    ctx = RunContext(rules=load_rules(ROOT / "rules" / "v0.toml"), upstream=None, kss=None)  # type: ignore[arg-type]
    mermaid = str(build_graph(ctx).get_graph().draw_mermaid()).strip()
    return HEADER + mermaid + "\n```\n"


if __name__ == "__main__":
    TARGET.write_text(render(), encoding="utf-8")
    print(f"썼다: {TARGET.relative_to(ROOT)}")
