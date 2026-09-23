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

from scoring import archive
from scoring.checks import RECOVERY_WINDOW, recovery_targets
from scoring.config import ROOT, connect_kss_batch, connect_upstream, load_env
from scoring.graph import build_graph
from scoring.rules import load_rules
from scoring.sources.upstream import load_calendar
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


def _archive(args: argparse.Namespace) -> int:
    """보존 기간 밖 실행을 내보내고, 검증을 통과한 것만 지운다 (SPEC §7.4).

    순서가 안전장치다 — **내보내기 → 되읽어 확인 → 기록 → 그때서야 삭제**.
    검증에 실패하면 그 실행은 지우지 않고 넘어간다.

    Returns:
        종료 코드. 검증 실패가 하나라도 있으면 1이다.
    """
    out_root = Path(args.out)
    env = load_env()
    upstream_conn = connect_upstream(env)
    kss = connect_kss_batch(env)
    failures = 0
    try:
        info = load_calendar(upstream_conn)
        sessions = [d for d in info.cal.sessions if d <= info.t]
        cutoffs = archive.retention_cutoffs(
            sessions, args.keep_scores, args.keep_parts, args.keep_universe)
        print("보존 경계: " + " · ".join(
            f"{k} {v.isoformat() if v else '해당 없음'}" for k, v in cutoffs.items()))

        for key in archive.PRUNE_ORDER:
            cutoff = cutoffs[key]
            if cutoff is None:
                continue
            targets = writer.archivable_runs(kss, cutoff)
            if not targets:
                print(f"  {key}: 지울 실행 없음")
                continue
            kept = []
            for run_id in targets:
                out_dir = out_root / str(run_id)
                manifest = writer.export_run(kss, run_id, out_dir)
                ok, reason = archive.verify(manifest, out_dir)
                writer.record_snapshot(kss, manifest, str(out_dir), ok)
                if ok:
                    kept.append(run_id)
                else:
                    failures += 1
                    print(f"  ⚠ {run_id} 복원 검증 실패: {reason} — 지우지 않는다",
                          file=sys.stderr)
            if args.prune and kept:
                deleted = writer.prune(kss, key, kept)
                print(f"  {key}: {len(kept)}개 실행 · {deleted:,}행 삭제")
            else:
                print(f"  {key}: {len(kept)}개 실행 내보냄 (삭제는 --prune)")
    finally:
        upstream_conn.close()
        kss.close()
    return 1 if failures else 0


def _recover(args: argparse.Namespace) -> int:
    """미게시 거래일을 찾아 차례로 채점한다 (SPEC §6.4).

    두 가지를 한다.

    1. **좀비 실행 정리** — heartbeat가 끊긴 실행을 `failed`로 닫는다. 그대로 두면 다음 실행이
       같은 T를 이미 처리 중이라고 오해한다.
    2. **미게시일 재처리** — 최근 `window`거래일 중 게시본이 없는 날을 오래된 것부터 다시 돌린다.
       그 이전은 자동으로 손대지 않는다 — 날짜를 지정해 `score`로 돌린다.

    Returns:
        종료 코드. 한 날이라도 실패하면 1이다.
    """
    env = load_env()
    upstream = connect_upstream(env)
    kss = connect_kss_batch(env)
    try:
        closed = writer.sweep_stale_runs(kss, args.stale_minutes)
        if closed:
            print(f"좀비 실행 {len(closed)}건을 failed로 닫았다")
        info = load_calendar(upstream)
        sessions = [d for d in info.cal.sessions if d <= info.t]
        since = sessions[-args.window] if len(sessions) >= args.window else sessions[0]
        done = writer.published_dates(kss, args.profile, since)
        targets = recovery_targets(sessions, done, args.window)
    finally:
        upstream.close()
        kss.close()

    if not targets:
        print(f"복구 대상 없음 — 최근 {args.window}거래일이 모두 게시돼 있다")
        return 0
    print(f"복구 대상 {len(targets)}일: " + ", ".join(d.isoformat() for d in targets))
    if args.dry_run:
        return 0

    failed = 0
    for d in targets:
        print(f"\n── {d.isoformat()} 재처리 ──")
        code = main(["score", "--profile", args.profile, "--date", d.isoformat(),
                     "--trigger", "recovery", "--rules", args.rules])
        failed += 1 if code else 0
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    """배치 진입점."""
    ap = argparse.ArgumentParser(prog="scoring.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ar = sub.add_parser("archive", help="오래된 상세를 내보내고 보존 기간 밖을 지운다 (SPEC §7.4)")
    ar.add_argument("--out", default=str(ROOT / "archive_out"), help="아카이브 디렉토리")
    ar.add_argument("--profile", default="common", choices=["technical", "partial", "common"])
    ar.add_argument("--keep-scores", type=int, default=archive.KEEP_SCORES)
    ar.add_argument("--keep-parts", type=int, default=archive.KEEP_PARTS)
    ar.add_argument("--keep-universe", type=int, default=archive.KEEP_UNIVERSE)
    ar.add_argument("--prune", action="store_true",
                    help="검증을 통과한 실행의 상세를 실제로 지운다 (기본은 내보내기만)")
    rc = sub.add_parser("recover", help="최근 거래일 중 미게시일을 다시 채점한다 (SPEC §6.4)")
    rc.add_argument("--profile", default="common", choices=["technical", "partial", "common"])
    rc.add_argument("--window", type=int, default=RECOVERY_WINDOW, help="자동 복구 범위 (거래일)")
    rc.add_argument("--stale-minutes", type=int, default=90,
                    help="heartbeat가 이만큼 끊긴 실행을 failed로 닫는다")
    rc.add_argument("--dry-run", action="store_true", help="대상만 보고 실행하지 않는다")
    rc.add_argument("--rules", default=str(ROOT / "rules" / "v0.toml"))
    sc = sub.add_parser("score", help="점수 계산·게시")
    sc.add_argument("--profile", default="partial",
                    choices=["technical", "partial", "common"])
    sc.add_argument("--date", default=None, help="평가 거래일 T (기본: 마지막 거래일)")
    sc.add_argument("--dry-run", action="store_true", help="계산만 — kss에 쓰지 않는다")
    sc.add_argument("--trigger", default="manual")
    sc.add_argument("--tickers", default=None, help="쉼표 구분 종목 (시험용)")
    sc.add_argument("--rules", default=str(ROOT / "rules" / "v0.toml"))
    args = ap.parse_args(argv)
    if args.cmd == "recover":
        return _recover(args)
    if args.cmd == "archive":
        return _archive(args)

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
