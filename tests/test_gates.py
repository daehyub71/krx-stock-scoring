"""갈래별 품질 게이트와 게시 등급 — M4 (SPEC §6.3 · §6.4). 순수 함수.

가르는 것은 셋이다.

| 결과 | 뜻 | 게시 |
|---|---|---|
| `publish` | 모든 갈래가 기준을 넘었다 | 게시한다 |
| `publish_degraded` | 보조 갈래가 모자라다 (수급·공매도·공시) | 게시하되 모자란 것을 공개한다 |
| `wait` | 일봉이 시장 단위로 모자라다 | 게시하지 않고 복구 큐에 남긴다 |
"""

from __future__ import annotations

from datetime import date

import pytest

from scoring.checks import (
    AUX_COVERAGE_MIN,
    D_COVERAGE_MIN,
    gate_aux,
    gate_bars,
    publish_decision,
)

T = date(2026, 9, 18)
META_OK = {
    "update": {"updated": T.isoformat()},
    "universe": {"updated": T.isoformat()},
}


def counts(kospi: tuple[int, int], kosdaq: tuple[int, int]) -> dict[str, dict[str, int]]:
    def row(pair: tuple[int, int]) -> dict[str, int]:
        return {"expected": pair[0], "stored": pair[1], "valid": pair[1], "amount_null": 0}

    return {"KOSPI": row(kospi), "KOSDAQ": row(kosdaq)}


# ── 보조 갈래 게이트 ────────────────────────────────────────────


def test_aux_gate_passes_at_threshold() -> None:
    """기준값과 같으면 통과다 — 경계는 포함이다."""
    ok, check = gate_aux("flows", {"expected": 100, "stored": 95, "valid": 95}, T)
    assert pytest.approx(0.95) == AUX_COVERAGE_MIN
    assert ok is True and check.status == "ok"


def test_aux_gate_degrades_below_threshold() -> None:
    ok, check = gate_aux("flows", {"expected": 100, "stored": 90, "valid": 90}, T)
    assert ok is False
    assert check.status == "degraded"
    assert check.coverage == pytest.approx(0.90)
    assert check.reason and "0.9" in check.reason


def test_aux_gate_reports_missing_source_as_degraded_not_ok() -> None:
    """행이 아예 없는 것과 커버리지 0%는 같은 사실이다 — 조용히 통과시키지 않는다."""
    ok, check = gate_aux("shorting", {"expected": 2000, "stored": 0, "valid": 0}, T)
    assert ok is False and check.status == "degraded"


def test_aux_gate_without_expected_is_unknown() -> None:
    """분모를 모르면 커버리지를 지어내지 않는다."""
    ok, check = gate_aux("flows", {"expected": 0, "stored": 0, "valid": 0}, T)
    assert ok is False
    assert check.coverage is None and check.status == "unknown"


# ── 게시 판정 ───────────────────────────────────────────────────


def test_decision_publishes_when_everything_passes() -> None:
    bars_ok, _ = gate_bars(counts((900, 900), (1800, 1800)), T, META_OK, ())
    assert publish_decision(bars_ok, aux_ok=True) == "publish"


def test_decision_waits_when_bars_fail() -> None:
    """시장 단위 장애면 게시하지 않는다 — 이전 게시본을 유지한다."""
    bars_ok, _ = gate_bars(counts((900, 500), (1800, 1800)), T, META_OK, ())
    assert bars_ok is False
    assert publish_decision(bars_ok, aux_ok=True) == "wait"


def test_decision_degrades_when_only_aux_fails() -> None:
    """수급이 모자라다고 랭킹 게시를 막지 않는다 — 모자란 것을 공개하고 게시한다."""
    bars_ok, _ = gate_bars(counts((900, 900), (1800, 1800)), T, META_OK, ())
    assert publish_decision(bars_ok, aux_ok=False) == "publish_degraded"


def test_bars_failure_outranks_aux_failure() -> None:
    bars_ok, _ = gate_bars(counts((900, 100), (1800, 1800)), T, META_OK, ())
    assert publish_decision(bars_ok, aux_ok=False) == "wait"


def test_d_threshold_is_the_spec_value() -> None:
    assert pytest.approx(0.99) == D_COVERAGE_MIN


# ── 복구 큐 ────────────────────────────────────────────────────


def test_recovery_targets_are_unpublished_sessions_within_the_window() -> None:
    from scoring.checks import recovery_targets

    sessions = [date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17), T]
    published = {date(2026, 9, 15), T}
    assert recovery_targets(sessions, published, window=5) == [
        date(2026, 9, 14), date(2026, 9, 16), date(2026, 9, 17),
    ]


def test_recovery_window_limits_how_far_back_it_goes() -> None:
    """기본 자동 복구는 최근 5거래일이다. 그 이전은 손으로 지정한다 (SPEC §6.4)."""
    from scoring.checks import recovery_targets

    sessions = [date(2026, 9, d) for d in (7, 8, 9, 10, 11, 14, 15, 16, 17, 18)]
    assert recovery_targets(sessions, published=set(), window=5) == [
        date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17), T,
    ]


def test_nothing_to_recover_when_everything_is_published() -> None:
    from scoring.checks import recovery_targets

    sessions = [date(2026, 9, 17), T]
    assert recovery_targets(sessions, published=set(sessions), window=5) == []


# ── 좀비 실행 정리 · 복구 명령 (M4) ─────────────────────────────


def test_sweep_only_touches_unfinished_runs() -> None:
    """게시된 실행을 나중에 failed로 덮지 않는다 — 끝나지 않은 것만 닫는다 (SPEC §6.4)."""
    from scoring.store import writer

    sql = writer.sweep_stale_runs.__doc__ or ""
    src = writer.sweep_stale_runs.__code__.co_consts
    text = " ".join(str(c) for c in src)
    assert "status in ('created', 'checking', 'computing', 'validating')" in text
    assert "published" not in text.split("where")[-1]
    assert "heartbeat" in sql
