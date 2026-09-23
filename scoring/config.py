"""환경변수와 DB 연결 — 두 연결을 분리한다 (SPEC §10).

- 상위(공유 Supabase): 세션을 `default_transaction_read_only=on`으로 연다.
  쓰기 문장은 서버가 거부한다.
  주소가 트랜잭션 풀러(6543)라 prepared statement를 끈다(`prepare_threshold=None`).
- kss(전용 Supabase): Session pooler URI. 배치 쓰기용.

값은 로그·예외 메시지에 싣지 않는다.
"""

from __future__ import annotations

import os
import urllib.parse
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
STATEMENT_TIMEOUT_MS = 180_000


class ConfigError(RuntimeError):
    """필요한 환경변수가 없다."""


# 이 배치가 쓰는 환경변수 전부. **CI에는 `.env`가 없으므로 이 목록이 유일한 출처다** —
# 예전에는 「.env에 이미 있는 키 또는 `_URL`로 끝나는 키」만 환경에서 받아, Actions에서
# `KSS_BATCH_PASSWORD`가 비어 실행이 죽었다 (2026-09-23 첫 Actions 실행).
KNOWN_KEYS = (
    "UPSTREAM_DATABASE_URL",
    "KSS_DATABASE_URL",
    "KSS_BATCH_PASSWORD",
    "KSS_READER_PASSWORD",
    "DART_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "KRX_ID",
    "KRX_PW",
)


def load_env(path: Path | None = None) -> dict[str, str]:
    """`.env`를 읽고 프로세스 환경변수로 덮어쓴 사전을 돌려준다 (CI는 Secrets가 우선).

    환경변수는 **`KNOWN_KEYS`에 있거나 `.env`에 이미 있거나 `_URL`로 끝나는** 것을 받는다.
    `.env`가 없는 러너에서도 Secrets만으로 전부 채워져야 한다.
    """
    env: dict[str, str] = {}
    target = path or ROOT / ".env"
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    env.update({
        k: v for k, v in os.environ.items()
        if (k in env or k in KNOWN_KEYS or k.endswith("_URL")) and v
    })
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
    """kss 전용 DB — 소유자(postgres) 연결. 스키마 적용·관리 전용."""
    url = require(env or load_env(), "KSS_DATABASE_URL")
    return psycopg.connect(url, prepare_threshold=None)


def connect_kss_batch(env: dict[str, str] | None = None) -> psycopg.Connection[tuple[object, ...]]:
    """kss 전용 DB — 배치 롤(`kss_batch`). 필요한 쓰기만 가능하다 (SPEC N11).

    Session pooler는 사용자명을 `<롤>.<프로젝트 ref>`로 받으므로 소유자 URI에서 호스트만 빌린다.
    """
    env = env or load_env()
    base = urllib.parse.urlsplit(require(env, "KSS_DATABASE_URL"))
    ref = (base.username or "").split(".", 1)[1] if "." in (base.username or "") else ""
    password = urllib.parse.quote(require(env, "KSS_BATCH_PASSWORD"), safe="")
    user = f"kss_batch.{ref}" if ref else "kss_batch"
    netloc = f"{user}:{password}@{base.hostname}:{base.port or 5432}"
    url = urllib.parse.urlunsplit((base.scheme, netloc, base.path, "", ""))
    return psycopg.connect(url, prepare_threshold=None)
