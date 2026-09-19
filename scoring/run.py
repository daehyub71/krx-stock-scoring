"""CLI — `python -m scoring.run score --profile technical [--date YYYY-MM-DD] [--dry-run]`.

실패하면 실행 행을 failed로 남기고 0이 아닌 코드로 끝난다. 예외 메시지에 비밀값을 싣지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from scoring.config import ROOT, connect_kss_batch, connect_upstream, load_env
from scoring.graph import build_graph
from scoring.rules import load_rules
from scoring.state import RunContext, RunState
from scoring.store import writer


def _code_sha() -> str | None:
    if sha := os.environ.get("GITHUB_SHA"):
        return sha
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, check=True)
        return out.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def main(argv: list[str] | None = None) -> int:
    """배치 진입점."""
    ap = argparse.ArgumentParser(prog="scoring.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("score", help="점수 계산·게시")
    sc.add_argument("--profile", default="technical", choices=["technical"])
    sc.add_argument("--date", default=None, help="평가 거래일 T (기본: 마지막 거래일)")
    sc.add_argument("--dry-run", action="store_true", help="계산만 — kss에 쓰지 않는다")
    sc.add_argument("--trigger", default="manual")
    sc.add_argument("--tickers", default=None, help="쉼표 구분 종목 (시험용)")
    sc.add_argument("--rules", default=str(ROOT / "rules" / "v0.toml"))
    args = ap.parse_args(argv)

    env = load_env()
    rules = load_rules(Path(args.rules))
    started = time.monotonic()
    upstream = connect_upstream(env)
    kss = None if args.dry_run else connect_kss_batch(env)
    ctx = RunContext(
        rules=rules, upstream=upstream, kss=kss, code_sha=_code_sha(),
        tickers_filter=frozenset(args.tickers.split(",")) if args.tickers else None,
    )
    state: RunState = {"profile": args.profile, "requested_t": args.date,
                       "trigger": args.trigger, "dry_run": args.dry_run}
    try:
        final: dict[str, Any] = build_graph(ctx).invoke(state)
    except Exception as exc:
        if ctx.run_id is not None and kss is not None:
            kss.rollback()
            writer.update_run(kss, ctx.run_id, "failed", error=f"{type(exc).__name__}: {exc}"[:500],
                              stats={"timings": ctx.timings}, finished=True)
        raise
    finally:
        upstream.close()
    total = round(time.monotonic() - started, 2)
    if kss is not None:
        if ctx.run_id is not None:
            writer.update_run(kss, ctx.run_id, final.get("status", "published"),
                              stats={"timings": {**ctx.timings, "total": total}})
        kss.close()
    report = {"run_id": final.get("run_id"), "t": final.get("t"), "status": final.get("status"),
              "timings": {**ctx.timings, "total": total}, **final.get("stats", {})}
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if final.get("status") in {"published", "dry_run"} else 2


if __name__ == "__main__":
    sys.exit(main())
