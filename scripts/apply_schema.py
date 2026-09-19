"""kss 전용 DB에 스키마를 적용한다 (멱등). 롤 비밀번호는 환경변수가 있을 때만 설정한다.

사용:
    venv/bin/python scripts/apply_schema.py            # 스키마만
    KSS_BATCH_PASSWORD=… KSS_READER_PASSWORD=… venv/bin/python scripts/apply_schema.py

값은 출력하지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoring.config import connect_kss, load_env  # noqa: E402

ROLE_PASSWORD_KEYS = (
    ("kss_batch", "KSS_BATCH_PASSWORD"),
    ("kss_reader", "KSS_READER_PASSWORD"),
)
SCHEMA = Path(__file__).resolve().parents[1] / "scoring" / "store" / "schema.sql"


def main() -> None:
    env = load_env()
    with connect_kss(env) as conn, conn.cursor() as cur:
        cur.execute(SCHEMA.read_text(encoding="utf-8").encode("utf-8"))
        for role, key in ROLE_PASSWORD_KEYS:
            password = env.get(key, "")
            if password:
                cur.execute(
                    sql.SQL("alter role {} password {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
                print(f"{role}: 비밀번호 설정")
        conn.commit()
    print("스키마 적용 완료")


if __name__ == "__main__":
    main()
