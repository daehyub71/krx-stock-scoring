"""M3 수집기 — DART 날짜축 공시 · 네이버 검색. 네트워크 없이 응답을 주입해 검증한다."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from scoring.sources import disclosure as dsrc
from scoring.sources import naver

# ── 공시 수집 ───────────────────────────────────────────────────


def page(items: list[dict[str, Any]], total_page: int = 1, status: str = "000") -> dict[str, Any]:
    return {"status": status, "message": "정상", "total_page": total_page, "list": items}


def item(rcept_no: str, report_nm: str = "주요사항보고서(유상증자결정)", cls: str = "Y",
         dt: str = "20260918", stock: str = "005930") -> dict[str, Any]:
    return {
        "rcept_no": rcept_no, "corp_code": "00126380", "stock_code": stock,
        "corp_name": "삼성전자", "corp_cls": cls, "report_nm": report_nm,
        "rcept_dt": dt, "flr_nm": "삼성전자", "rm": "",
    }


def test_fetch_range_reads_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def fake(path: str, params: dict[str, str]) -> dict[str, Any]:
        calls.append(params)
        n = int(params["page_no"])
        return page([item(f"2026091800000{n}")], total_page=3)

    monkeypatch.setattr(dsrc, "get_json", fake)
    rows = list(dsrc.fetch_range(date(2026, 9, 18), date(2026, 9, 18), "Y"))
    assert len(rows) == 3
    assert [c["page_no"] for c in calls] == ["1", "2", "3"]


def test_fetch_range_asks_for_original_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    """`last_reprt_at=N` — 최종본만 받으면 정정 전 사건을 잃는다 (SPEC §5.5)."""
    seen: dict[str, str] = {}

    def fake(path: str, params: dict[str, str]) -> dict[str, Any]:
        seen.update(params)
        return page([])

    monkeypatch.setattr(dsrc, "get_json", fake)
    list(dsrc.fetch_range(date(2026, 9, 1), date(2026, 9, 18), "K"))
    assert seen["last_reprt_at"] == "N"
    assert seen["corp_cls"] == "K"
    assert seen["page_count"] == "100"
    assert (seen["bgn_de"], seen["end_de"]) == ("20260901", "20260918")


def test_no_data_status_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """휴일에는 0건이 정상이다."""
    monkeypatch.setattr(dsrc, "get_json", lambda p, q: page([], status="013"))
    assert list(dsrc.fetch_range(date(2026, 9, 20), date(2026, 9, 20), "Y")) == []


def test_record_carries_normalized_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dsrc, "get_json", lambda p, q: page(
        [item("20260918000001", "[기재정정]주요사항보고서(유상증자결정)")]))
    rec = next(iter(dsrc.fetch_range(date(2026, 9, 18), date(2026, 9, 18), "Y")))
    assert rec.norm_name == "주요사항보고서(유상증자결정)"
    assert rec.corrected is True
    assert rec.rcept_dt == date(2026, 9, 18)


def test_malformed_date_row_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dsrc, "get_json", lambda p, q: page(
        [item("20260918000001", dt="2026-09-18"), item("20260918000002")]))
    rows = list(dsrc.fetch_range(date(2026, 9, 18), date(2026, 9, 18), "Y"))
    assert [r.rcept_no for r in rows] == ["20260918000002"]


def test_fetch_days_covers_both_markets_without_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(path: str, params: dict[str, str]) -> dict[str, Any]:
        cls = params["corp_cls"]
        shared = item("20260918000009", cls=cls)          # 두 시장 응답에 같은 접수번호
        return page([item(f"2026091800000{cls}", cls=cls), shared])

    monkeypatch.setattr(dsrc, "get_json", fake)
    rows = dsrc.fetch_days(date(2026, 9, 18), date(2026, 9, 18))
    assert len(rows) == 3
    assert len({r.rcept_no for r in rows}) == 3


# ── 네이버 검색 ─────────────────────────────────────────────────


def test_strip_tags_removes_markup_and_entities() -> None:
    raw = "<b>삼성전자</b> &quot;테스트&quot; 배포 &amp; 수리"
    assert naver.strip_tags(raw) == '삼성전자 "테스트" 배포 & 수리'


def test_parse_pub_date_reads_rfc2822() -> None:
    when = naver.parse_pub_date("Wed, 23 Sep 2026 21:41:00 +0900")
    assert when == datetime(2026, 9, 23, 21, 41)


def test_parse_pub_date_returns_none_on_garbage() -> None:
    assert naver.parse_pub_date("어제") is None


def test_to_articles_drops_items_without_a_usable_time() -> None:
    payload = {"items": [
        {"title": "<b>삼성전자</b> 흑자전환", "originallink": "https://a",
         "pubDate": "Wed, 23 Sep 2026 21:41:00 +0900"},
        {"title": "시각 없는 기사", "link": "https://b", "pubDate": ""},
    ]}
    arts = naver.to_articles(payload)
    assert len(arts) == 1
    assert arts[0].title == "삼성전자 흑자전환"
    assert arts[0].link == "https://a"


def test_missing_keys_raise_without_leaking() -> None:
    with pytest.raises(naver.NaverError) as exc:
        naver.search_news("삼성전자", env={})
    assert "NAVER_CLIENT_ID" in str(exc.value)


def test_error_message_never_contains_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """예외에 키가 섞여 나가지 않는다 — 공개 Actions 로그 대비."""
    secret = "super-secret-value"

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError(f"연결 실패 {secret}")

    monkeypatch.setattr(naver, "urlopen", boom)
    with pytest.raises(naver.NaverError) as exc:
        naver.search_news("삼성전자", env={"NAVER_CLIENT_ID": "id", "NAVER_CLIENT_SECRET": secret})
    assert secret not in str(exc.value)


# ── 일시 차단 재시도 (2026-09-23 실측) ──────────────────────────
#
# 30회 연속은 멀쩡했는데 2,585회를 붙여 돌리자 815종목(29%)이 실패했고,
# 그 종목들을 곧바로 다시 부르면 정상이었다. 실패를 바로 결측으로 굳히지 않는다.


KEYS = {"NAVER_CLIENT_ID": "i", "NAVER_CLIENT_SECRET": "s"}


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: Any) -> None:
        return None


def test_rate_limit_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.error import HTTPError

    calls: list[int] = []

    def flaky(req: Any, timeout: float = 0) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise HTTPError("https://x", 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]
        return _Resp(b'{"items": []}')

    monkeypatch.setattr(naver, "urlopen", flaky)
    monkeypatch.setattr(naver, "sleep", lambda s: None)
    assert naver.search_news("삼성전자", env=KEYS) == []
    assert len(calls) == 2


def test_client_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """키가 틀린 401을 세 번 부를 이유가 없다."""
    from urllib.error import HTTPError

    calls: list[int] = []

    def bad(req: Any, timeout: float = 0) -> Any:
        calls.append(1)
        raise HTTPError("https://x", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(naver, "urlopen", bad)
    monkeypatch.setattr(naver, "sleep", lambda s: None)
    with pytest.raises(naver.NaverError):
        naver.search_news("삼성전자", env=KEYS)
    assert len(calls) == 1


def test_gives_up_after_the_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.error import HTTPError

    calls: list[int] = []

    def always(req: Any, timeout: float = 0) -> Any:
        calls.append(1)
        raise HTTPError("https://x", 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(naver, "urlopen", always)
    monkeypatch.setattr(naver, "sleep", lambda s: None)
    with pytest.raises(naver.NaverError) as exc:
        naver.search_news("삼성전자", env=KEYS)
    assert len(calls) == naver.RETRIES + 1
    assert "429" in str(exc.value)
