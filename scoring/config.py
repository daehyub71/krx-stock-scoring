"""환경변수와 DB 연결 — 두 연결을 분리한다 (SPEC §10).

- 상위(공유 Supabase): 세션을 `default_transaction_read_only=on`으로 연다.
  쓰기 문장은 서버가 거부한다.
  주소가 트랜잭션 풀러(6543)라 prepared statement를 끈다(`prepare_threshold=None`).
- kss(전용 Supabase): Session pooler URI. 배치 쓰기용.

값은 로그·예외 메시지에 싣지 않는다.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
STATEMENT_TIMEOUT_MS = 180_000


class ConfigError(RuntimeError):
    """필요한 환경변수가 없다."""


def load_env(path: Path | None = None) -> dict[str, str]:
    """`.env`를 읽고 프로세스 환경변수로 덮어쓴 사전을 돌려준다 (CI는 Secrets가 우선)."""
    env: dict[str, str] = {}
    target = path or ROOT / ".env"
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k in env or k.endswith("_URL")})
    return env


def require(env: dict[str, str], key: str) -> str:
    """값이 비어 있으면 이름만 알린다."""
    value = env.get(key, "")
    if not value:
        raise ConfigError(f"{key}가 비어 있다 — .env 또는 Secrets를 채워라")
    return value


def connect_upstream(env: dict[str, str] | None = None) -> psycopg.Connection[tuple[object, ...]]:
    """상위 공유 DB — 읽기 전용 세션."""
    url = require(env or load_env(), "UPSTREAM_DATABASE_URL")
    opts = f"-c default_transaction_read_only=on -c statement_timeout={STATEMENT_TIMEOUT_MS}"
    conn = psycopg.connect(url, options=opts, prepare_threshold=None)
    conn.read_only = True
    return conn


def connect_kss(env: dict[str, str] | None = None) -> psycopg.Connection[tuple[object, ...]]:
    """kss 전용 DB — 배치 쓰기."""
    url = require(env or load_env(), "KSS_DATABASE_URL")
    return psycopg.connect(url, prepare_threshold=None)
