"""네이버 검색 API — 뉴스 조회 (SPEC §5.5, v2.9). 표준 라이브러리 `urllib`만 쓴다.

**키는 헤더에 실린다**(`X-Naver-Client-Id` / `-Secret`). DART와 달리 URL에는 없지만,
예외 메시지에 헤더가 섞여 나오는 일이 없도록 오류 문구를 우리가 만든다.

하루 25,000회 한도에 전 종목 약 2,585회다(10%). 2026-09-23 실측 호출당 90~135ms.

정렬은 `sim`(정확도)이 아니라 **`date`(최신)** 다. 점수는 "최근 7일에 무슨 일이 있었나"를 보므로
관련성은 제목 필터(`domain/news.is_related`)로 따로 거른다 — 실측에서 최신순 결과에
시황 기사가 섞여 들어오는 것을 확인했다.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime
from email.utils import parsedate_to_datetime
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scoring.config import load_env
from scoring.domain.news import Article

ENDPOINT = "https://openapi.naver.com/v1/search/news.json"
TIMEOUT = 10.0

# 지속 호출이면 일시 차단이 온다 — 30회 연속은 멀쩡했는데 2,585회를 붙여 돌리자
# 815종목(29%)이 실패했고, 그 종목들을 곧바로 다시 부르면 정상이었다 (2026-09-23 실측).
# 그래서 실패를 바로 결측으로 굳히지 않고 잠깐 쉬었다 다시 부른다.
RETRY_CODES = frozenset({429, 500, 502, 503, 504})
RETRIES = 2
BACKOFF = 1.5
DISPLAY = 20          # 관련성 필터에서 대부분 떨어지므로 넉넉히 받는다
SORT = "date"

_TAG = re.compile(r"<[^>]+>")
_ENTITIES = {"&quot;": '"', "&amp;": "&", "&lt;": "<", "&gt;": ">", "&#39;": "'", "&apos;": "'"}


class NaverError(RuntimeError):
    """네이버 검색 호출 실패. **메시지에 키를 넣지 않는다.**"""


def strip_tags(text: str) -> str:
    """제목의 `<b>` 강조 태그와 HTML 엔티티를 지운다."""
    out = _TAG.sub("", text)
    for entity, char in _ENTITIES.items():
        out = out.replace(entity, char)
    return out.strip()


def parse_pub_date(raw: str) -> datetime | None:
    """RFC 2822 형식(`Wed, 23 Sep 2026 21:41:00 +0900`)을 시각으로. 못 읽으면 None."""
    try:
        return parsedate_to_datetime(raw).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def to_articles(payload: dict[str, Any]) -> list[Article]:
    """응답을 기사 목록으로. 시각을 못 읽은 항목은 버린다 — 창 판정을 할 수 없다."""
    out: list[Article] = []
    for item in payload.get("items") or []:
        when = parse_pub_date(str(item.get("pubDate", "")))
        if when is None:
            continue
        out.append(Article(
            title=strip_tags(str(item.get("title", ""))),
            link=str(item.get("originallink") or item.get("link") or ""),
            published_at=when,
        ))
    return out


def _fetch(req: Request) -> Any:
    """요청 하나. 일시 차단·5xx는 물러섰다 다시 부른다.

    Raises:
        NaverError: 재시도를 다 쓰고도 실패했다. 메시지에 키는 없다.
    """
    last = ""
    for attempt in range(RETRIES + 1):
        try:
            with urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read())
        except HTTPError as exc:
            last = f"HTTP {exc.code} {exc.reason}"
            if exc.code not in RETRY_CODES:
                raise NaverError(f"검색 {last}") from None
        except URLError as exc:
            last = f"연결 실패: {exc.reason}"
        except (OSError, ValueError) as exc:
            last = f"응답을 읽지 못했다: {type(exc).__name__}"
        if attempt < RETRIES:
            sleep(BACKOFF * (2**attempt))
    raise NaverError(f"검색 실패({RETRIES + 1}회 시도): {last}")


def search_news(
    query: str, display: int = DISPLAY, env: dict[str, str] | None = None
) -> list[Article]:
    """종목 하나의 최신 기사를 받는다.

    Args:
        query: 검색어 — 보통 종목명.
        display: 받을 건수 (최대 100).
        env: 환경변수 (테스트 주입용).

    Returns:
        최신순 기사 목록.

    Raises:
        NaverError: 키가 없거나 요청이 실패했다. 메시지에 키는 없다.
    """
    conf = env if env is not None else load_env()
    cid, secret = conf.get("NAVER_CLIENT_ID", ""), conf.get("NAVER_CLIENT_SECRET", "")
    if not cid or not secret:
        raise NaverError("NAVER_CLIENT_ID/SECRET이 비어 있다")

    url = f"{ENDPOINT}?" + urllib.parse.urlencode(
        {"query": query, "display": str(display), "sort": SORT})
    req = Request(url, headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret})
    payload = _fetch(req)
    if not isinstance(payload, dict):
        raise NaverError("검색 응답이 사전이 아니다")
    return to_articles(payload)
