"""배치 그래프(LangGraph) 그림 — 컴파일된 그래프의 노드·간선에서 SVG를 만든다.

사용:
    venv/bin/python scripts/draw_graph.py docs/img/graph.svg
그 뒤 PNG 렌더링(Chrome headless)은 PLAN §2.3 참조. 설명이 없는 노드가 생기면 실패한다 —
그래프를 고쳤으면 아래 NOTES도 함께 고친다.
"""

from __future__ import annotations

import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoring.graph import build_graph  # noqa: E402
from scoring.rules import load_rules  # noqa: E402
from scoring.state import RunContext  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FONT = "'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif"
MONO = "Menlo,'SF Mono',Consolas,monospace"
INK, SUB = "#0F172A", "#475569"
UP, BA, KS, WARN = "#475569", "#0F766E", "#6D28D9", "#B45309"

# 노드 → (설명 두 줄, 상위 읽기, kss 쓰기, 실행 상태)
NOTES: dict[str, tuple[tuple[str, str], bool, bool, str]] = {
    "calendar": (("거래일 달력 = 일봉 날짜 ∩ 지수 날짜", "T 결정 — 실행 시각(자정 이후)과 무관"),
                 True, False, ""),
    "create_run": (("kss_runs에 실행 행을 먼저 기록", "이후 실패해도 기록이 남는다"),
                   False, True, "created → checking"),
    "gate": (("시장별 T일 일봉 커버리지 ≥ 99% · ksc_meta 대조", "결과를 kss_source_checks에 기록"),
             True, True, ""),
    "wait": (("상위 수집 미완 — 게시하지 않는다", "07:17 복구 잡의 대상 (M4)"),
             False, True, "waiting_upstream"),
    "load": (("최근 400거래일 일봉을 서버 측 커서로 적재", "종목·드리프트 메타 (≈108만 행, 31초)"),
             True, False, "computing"),
    "compute": (("분류 → 기술 5항목 → 집계 (순수 함수)", "2,771종목 7초 · I/O 없음"),
                False, False, ""),
    "validate": (("종목 수 = 유니버스 · 종목당 항목 5개",
                  "scored ⇒ 관측률 1 · 비완전 관측 랭킹 금지"),
                 False, False, "validating"),
    "persist": (("점수·근거·유니버스를 500행 청크로 저장", "저장 행 수를 다시 세어 대조"),
                False, True, ""),
    "publish": (("잠금 → 게시 포인터 교체 → 이력", "한 트랜잭션 — 실패하면 이전 게시본 유지"),
                False, True, "published"),
    "cross": (("alerts 신호(d = T)와 같은 날 점수를 연결", "available_at_signal · 주봉 품질 표지"),
              True, True, ""),
}

W, H = 1500, 1270
out: list[str] = []


def t(x: float, y: float, s: str, size: float = 13, fill: str = INK, weight: int = 400,
      anchor: str = "start", mono: bool = False) -> None:
    fam = MONO if mono else FONT
    out.append(f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" fill="{fill}" '
               f'font-weight="{weight}" text-anchor="{anchor}">{escape(s)}</text>')


def r(x: float, y: float, w: float, h: float, fill: str, stroke: str, rx: float = 10,
      sw: float = 1.5, dash: str | None = None) -> None:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
               f'stroke="{stroke}" stroke-width="{sw}"{d}/>')


def arrow(pts: list[tuple[float, float]], color: str = BA, dash: str | None = None) -> None:
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    ds = f' stroke-dasharray="{dash}"' if dash else ""
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.8"{ds} '
               f'marker-end="url(#m)"/>')


def badge(x: float, y: float, label: str, color: str) -> float:
    w = 14 + 7.2 * len(label)
    r(x, y, w, 20, color, color, rx=10, sw=0)
    t(x + w / 2, y + 14, label, size=11, fill="#FFFFFF", weight=700, anchor="middle")
    return w


def main() -> None:
    rules = load_rules(ROOT / "rules" / "v0.toml")
    ctx = RunContext(rules=rules, upstream=None, kss=None)  # type: ignore[arg-type]
    g = build_graph(ctx).get_graph()
    names = [n for n in g.nodes if not n.startswith("__")]
    missing = set(names) ^ set(NOTES)
    if missing:
        raise SystemExit(f"NOTES와 그래프 노드가 다르다: {sorted(missing)}")
    edges = {(e.source, e.target): e.conditional for e in g.edges}
    branch = [b for (a, b), cond in edges.items() if a == "gate" and cond and b != "load"]
    main_line = [n for n in names if n not in branch]

    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
               f'viewBox="0 0 {W} {H}">')
    out.append('<defs><marker id="m" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
               'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
               f'fill="{BA}"/></marker></defs>')
    out.append(f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>')
    t(40, 46, "채점 배치 그래프 (LangGraph)", size=24, weight=800)
    t(40, 72, "scoring/graph.py — 노드는 domain·store 호출을 감싼 얇은 층. "
              "상태에는 요약만, 대량 데이터는 실행 문맥에", size=13, fill=SUB)

    nx, nw, nh, gap, top = 470, 420, 70, 26, 150
    pos: dict[str, float] = {}
    # 시작
    r(nx + nw / 2 - 60, top - 58, 120, 34, "#FFFFFF", BA, rx=17)
    t(nx + nw / 2, top - 36, "START", size=12, weight=700, anchor="middle", fill=BA, mono=True)
    arrow([(nx + nw / 2, top - 24), (nx + nw / 2, top - 2)])
    for i, name in enumerate(main_line):
        y = top + i * (nh + gap)
        pos[name] = y
        (l1, l2), reads, writes, status = NOTES[name]
        fill = "#F0FDFA" if name != "compute" else "#FFFFFF"
        r(nx, y, nw, nh, fill, BA, rx=12, sw=1.6)
        t(nx + 16, y + 25, name, size=15, weight=700, mono=True, fill=BA)
        t(nx + 16, y + 45, l1, size=12, fill=INK)
        t(nx + 16, y + 61, l2, size=11.5, fill=SUB)
        # 입출력 배지 (오른쪽)
        bx: float = nx + nw + 18
        if reads:
            bx += badge(bx, y + 14, "상위 읽기", UP) + 8
        if writes:
            badge(bx, y + 14, "kss 쓰기", KS)
        # 실행 상태 (왼쪽)
        if status:
            t(nx - 18, y + 40, f"kss_runs.status: {status}", size=12, fill=WARN, weight=600,
              anchor="end", mono=True)
        if i:
            arrow([(nx + nw / 2, y - gap), (nx + nw / 2, y - 2)])
    end_y = top + len(main_line) * (nh + gap)
    arrow([(nx + nw / 2, end_y - gap), (nx + nw / 2, end_y - 2)])
    r(nx + nw / 2 - 60, end_y, 120, 34, "#FFFFFF", BA, rx=17)
    t(nx + nw / 2, end_y + 22, "END", size=12, weight=700, anchor="middle", fill=BA, mono=True)

    # 게이트 분기
    for b in branch:
        gy = pos["gate"]
        bx, by = 1040, gy + nh + gap
        (l1, l2), _, _, status = NOTES[b]
        r(bx, by, 400, nh, "#FFFBEB", WARN, rx=12, sw=1.6, dash="6 4")
        t(bx + 16, by + 25, b, size=15, weight=700, mono=True, fill=WARN)
        t(bx + 16, by + 45, l1, size=12)
        t(bx + 16, by + 61, l2, size=11.5, fill=SUB)
        t(bx + 16, by + nh + 22, f"kss_runs.status: {status}", size=12, fill=WARN, weight=600,
          mono=True)
        arrow([(nx + nw, gy + 50), (bx + 200, gy + 50), (bx + 200, by - 2)],
              color=WARN, dash="6 4")
        t(bx + 210, gy + 44, "커버리지 미달", size=12, fill=WARN, weight=700)
        t(nx + nw / 2 + 10, gy + nh + 17, "통과", size=12, fill=BA, weight=700)
        arrow([(bx + 200, by + nh + 34), (bx + 200, end_y + 17), (nx + nw / 2 + 62, end_y + 17)],
              color=WARN, dash="6 4")

    # 범례(왼쪽 아래) · 실패 처리(오른쪽 아래)
    ly = end_y + 64
    r(40, ly, 700, 150, "#F8FAFC", "#E2E8F0", rx=12, sw=1)
    t(58, ly + 30, "상태(RunState) — 요약만", size=14, weight=700)
    for i, s in enumerate(["profile · requested_t · trigger · dry_run",
                           "t · run_id · gate_ok · status · stats"]):
        t(58, ly + 54 + i * 20, s, size=12, fill=SUB, mono=True)
    t(390, ly + 30, "실행 문맥(RunContext) — 노드에 묶임", size=14, weight=700)
    for i, s in enumerate(["상위·kss 연결 · 규칙", "스냅샷(일봉 108만 행) · 계산 결과",
                           "→ 체크포인트가 대량 데이터를 복제하지 않음"]):
        t(390, ly + 54 + i * 20, s, size=12, fill=SUB)
    t(58, ly + 130, "dry_run: create_run · persist · publish · cross는 kss에 쓰지 않는다",
      size=12, fill=KS, weight=600)
    r(760, ly, 700, 150, "#FEF2F2", "#FCA5A5", rx=12, sw=1)
    t(778, ly + 30, "실패 처리", size=14, weight=700, fill="#B91C1C")
    for i, s in enumerate(["어느 노드에서든 예외 → run.py가 kss_runs를 failed로 기록,",
                           "종료 코드 ≠ 0.",
                           "게시 포인터는 publish 트랜잭션 안에서만 바뀐다 →",
                           "이전 게시본이 그대로 보인다 (실DB 테스트로 확인)."]):
        t(778, ly + 56 + i * 21, s, size=12.5, fill=INK)
    out.append("</svg>")
    Path(sys.argv[1]).write_text("\n".join(out), encoding="utf-8")
    print("ok", main_line, branch)


if __name__ == "__main__":
    main()
