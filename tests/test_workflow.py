"""score.yml — 운영 워크플로의 계약 (SPEC §6.1 · §6.4 · N24).

워크플로는 실행해 볼 수 없으므로 **약속한 것이 파일에 있는지**만 본다.
여기 걸린 것들은 전부 앞선 사고에서 나온 규칙이다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "score.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")


def test_workflow_exists() -> None:
    assert WORKFLOW.exists()


def test_three_entry_points() -> None:
    """이벤트·예비 예약·복구 예약 (SPEC §6.1)."""
    assert "repository_dispatch:" in TEXT
    assert "types: [upstream_collected]" in TEXT
    assert 'cron: "37 14 * * 1-5"' in TEXT   # 평일 23:37 KST
    assert 'cron: "17 22 * * *"' in TEXT     # 매일 07:17 KST


def test_permissions_are_read_only() -> None:
    assert "permissions:\n  contents: read" in TEXT


def test_concurrency_does_not_cancel_running_jobs() -> None:
    """진행 중인 정상 실행을 새 이벤트로 취소하지 않는다 (SPEC §6.4)."""
    assert "cancel-in-progress: false" in TEXT


def test_python_runs_unbuffered() -> None:
    """버퍼링 때문에 잘린 실행의 로그가 통째로 빈 적이 있다 (charts 2026-08-31)."""
    assert "python -u -m scoring.run" in TEXT


def test_timeout_is_longer_than_the_target() -> None:
    """30분은 목표이지 상한이 아니다 (SPEC §6.4)."""
    assert "timeout-minutes: 60" in TEXT


@pytest.mark.parametrize(
    "secret",
    ["UPSTREAM_DATABASE_URL", "KSS_DATABASE_URL", "KSS_BATCH_PASSWORD", "DART_API_KEY",
     "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "KRX_ID", "KRX_PW"],
)
def test_credentials_come_from_secrets(secret: str) -> None:
    assert f"{secret}: ${{{{ secrets.{secret} }}}}" in TEXT


def test_no_literal_credentials() -> None:
    """값이 파일에 박히지 않았는지 — 공개 리포다."""
    for marker in ("postgres://", "postgresql://", "eyJ"):
        assert marker not in TEXT


# ── 환경변수 적재 (2026-09-23 첫 Actions 실행에서 드러남) ───────
#
# `.env`가 없는 러너에서는 Secrets가 유일한 출처다. 예전 규칙(「.env에 이미 있는 키 또는
# `_URL`로 끝나는 키」)으로는 `KSS_BATCH_PASSWORD`가 비어 실행이 죽었다.


def test_workflow_secrets_are_all_known_keys() -> None:
    """워크플로가 주입하는 이름이 `load_env`가 받는 목록에 전부 있어야 한다."""
    import re

    from scoring.config import KNOWN_KEYS

    injected = set(re.findall(r"(\w+): \$\{\{ secrets\.(\w+) \}\}", TEXT))
    names = {a for a, _ in injected}
    assert names, "워크플로에 주입되는 비밀값이 없다"
    assert names <= set(KNOWN_KEYS), f"load_env가 못 받는 이름: {names - set(KNOWN_KEYS)}"


def test_env_loads_known_keys_without_a_dotenv_file(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scoring.config import load_env

    for key in ("KSS_BATCH_PASSWORD", "DART_API_KEY", "KRX_ID"):
        monkeypatch.setenv(key, f"value-of-{key}")
    env = load_env(path=tmp_path / "none.env")     # .env가 없는 러너를 흉내 낸다
    assert env["KSS_BATCH_PASSWORD"] == "value-of-KSS_BATCH_PASSWORD"
    assert env["DART_API_KEY"] == "value-of-DART_API_KEY"
    assert env["KRX_ID"] == "value-of-KRX_ID"


def test_empty_environment_value_does_not_shadow_dotenv(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """빈 환경변수가 `.env` 값을 덮으면 안 된다 — Actions는 미설정 Secret을 빈 문자열로 준다."""
    from scoring.config import load_env

    dotenv = tmp_path / ".env"
    dotenv.write_text("DART_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("DART_API_KEY", "")
    assert load_env(path=dotenv)["DART_API_KEY"] == "from-dotenv"
