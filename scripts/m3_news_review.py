"""뉴스 사전 검토 목록 — 사전에 걸리지 않아 **중립으로 빠진 제목**을 모아 낸다 (SPEC §5.5 D7).

사전이 놓치는 것은 사전만 봐서는 안 보인다. 게시된 실행이 실제로 읽은 기사 제목을 꺼내
판정별로 묶어 두면, 무엇을 더 넣어야 하는지 눈으로 보인다 — 2026-09-23에 「주가조작 도피 조력」이
중립으로 나온 것도 이렇게 드러났다.

내는 것 둘

| 파일 | 무엇 | 쓰임 |
|---|---|---|
| `docs/m3/news_review_<T>.md` | 중립 제목 · 판정된 제목 · 낱말 분포 | 사람이 읽고 고른다 |
| `docs/m3/news_labels_<T>.toml` | 제목마다 `label = ""` 빈칸 | D7 300건 라벨링 원본 |

라벨을 채운 뒤 사전에 넣을 낱말이 정해지면 `rules/lexicon/news.toml`을 고친다.

사용: venv/bin/python scripts/m3_news_review.py [T] [프로필]
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoring.config import connect_kss_batch, load_env  # noqa: E402
from scoring.domain.lexicon import news_lexicon  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "m3"


def fetch_observations(t: date, profile: str) -> list[tuple[str, str, dict[str, Any]]]:
    """게시된 실행의 뉴스 관측을 읽는다."""
    env = load_env()
    with connect_kss_batch(env) as kss, kss.cursor() as cur:
        cur.execute(
            "select n.ticker, n.status, n.articles from kss_news_observations n "
            "join kss_publications p using (run_id) "
            "where p.data_date = %s and p.profile = %s",
            (t, profile),
        )
        return [(str(r[0]), str(r[1]), dict(r[2]) if isinstance(r[2], dict) else
                 {"items": r[2]}) for r in cur.fetchall()]


def collect(
    rows: list[tuple[str, str, Any]]
) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    """(중립 제목, 판정된 제목) 으로 가른다."""
    neutral: list[tuple[str, str]] = []
    judged: list[tuple[str, str, str]] = []
    for ticker, _status, payload in rows:
        items = payload if isinstance(payload, list) else payload.get("items", [])
        for a in items or []:
            title = str(a.get("title", ""))
            verdict = str(a.get("verdict", ""))
            if verdict == "neutral":
                neutral.append((ticker, title))
            else:
                judged.append((ticker, title, str(a.get("term", ""))))
    return neutral, judged


def write_review(
    t: date, neutral: list[tuple[str, str]], judged: list[tuple[str, str, str]]
) -> Path:
    """사람이 읽는 검토 문서."""
    lex = news_lexicon()
    used = Counter(term for _, _, term in judged)
    lines = [
        f"# 뉴스 사전 검토 — {t.isoformat()}",
        "",
        f"사전 `rules/lexicon/news.toml` · 항목 {len(lex.terms)}개 · 버전 `{lex.version[:16]}`",
        "",
        f"판정된 기사 {len(judged)}건 · **중립으로 빠진 기사 {len(neutral)}건**",
        "",
        "## 중립으로 빠진 제목",
        "",
        "사전에 걸리는 낱말이 없어 0점이 된 기사다. 악재·호재가 섞여 있으면 사전에 낱말을 더한다.",
        "",
    ]
    lines += [f"- `{ticker}` {title}" for ticker, title in neutral] or ["(없음)"]
    lines += ["", "## 걸린 낱말 분포", ""]
    lines += [f"- {term or '(없음)'} — {n}건" for term, n in used.most_common()] or ["(없음)"]
    lines += ["", "## 판정된 제목", ""]
    lines += [f"- `{ticker}` [{term}] {title}" for ticker, title, term in judged] or ["(없음)"]
    lines += [
        "",
        "---",
        "",
        "고치는 곳: `rules/lexicon/news.toml`(뉴스) · `rules/lexicon/disclosure.toml`(공시).",
        "낱말을 더하면 사전 버전이 바뀌고 **다음 실행부터** 적용된다.",
        "이미 게시된 점수는 그대로 남는다.",
        "",
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"news_review_{t.isoformat()}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_labels(t: date, neutral: list[tuple[str, str]]) -> Path:
    """D7 라벨링 원본 — `label`에 positive / negative / neutral 을 채운다."""
    lines = [
        f"# 뉴스 제목 라벨링 — {t.isoformat()} (SPEC D7)",
        "#",
        "# 중립으로 빠진 제목이다. `label`에 positive / negative / neutral 중 하나를 적는다.",
        "# 300건쯤 모이면 정밀도·오분류를 재고, 넣을 낱말과 확정 등급 게시 여부를 정한다.",
        "",
    ]
    for ticker, title in neutral:
        safe = title.replace('"', "'")
        lines += ["[[sample]]", f'ticker = "{ticker}"', f'title = "{safe}"', 'label = ""', ""]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"news_labels_{t.isoformat()}.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    t = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    profile = sys.argv[2] if len(sys.argv) > 2 else "common"
    rows = fetch_observations(t, profile)
    if not rows:
        print(f"{t} {profile} 게시본에 뉴스 관측이 없다")
        return
    neutral, judged = collect(rows)
    review = write_review(t, neutral, judged)
    labels = write_labels(t, neutral)
    print(f"종목 {len(rows)} · 판정 {len(judged)}건 · 중립 {len(neutral)}건")
    for label, path in (("검토 문서", review), ("라벨 원본", labels)):
        print(f"  {label}: {path}")


if __name__ == "__main__":
    main()
