"""공시 수집 — OpenDART 공시검색을 **날짜축으로** 훑는다 (SPEC §5.5 · F4).

종목축(회사별 조회)이면 상장사 수만큼 요청이 나간다. 날짜축은 하루치를 한 번에 받는다 —
M0 실측으로 **하루 4~5회**(시장 2 × 페이지 2~3)면 전 종목이 덮인다.

계약 셋을 지킨다.

1. `corp_cls=Y`(유가)·`K`(코스닥)를 따로 부른다. 전체를 받아 걸러내면 상장사가 아닌 공시까지 센다.
2. **`last_reprt_at=N`** — 최종본만 받으면 정정 전 사건을 잃는다. 재현하려면 원본이 필요하다.
3. `total_page` 끝까지 읽는다. 페이지당 100건 · 회사 미지정 검색기간 최대 3개월이 API 계약이다.

`013`(조회 결과 없음)은 오류가 아니다 — 휴일에는 정상적으로 0건이 온다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

from scoring.domain.flags import normalize
from scoring.sources.dart import STATUS_NO_DATA, get_json

PAGE_COUNT = 100
MARKETS = ("Y", "K")


@dataclass(frozen=True, slots=True)
class DisclosureRecord:
    """`kss_disclosures` 한 행이 될 값."""

    rcept_no: str
    corp_code: str
    stock_code: str
    corp_name: str
    corp_cls: str
    report_nm: str
    rcept_dt: date
    flr_nm: str
    rm: str
    norm_name: str
    corrected: bool
    note: str


def _to_record(item: dict[str, Any]) -> DisclosureRecord | None:
    """응답 한 건을 행으로. 접수일이 없거나 형식이 틀리면 버린다."""
    raw_dt = str(item.get("rcept_dt", "")).strip()
    if len(raw_dt) != 8 or not raw_dt.isdigit():
        return None
    n = normalize(str(item.get("report_nm", "")))
    return DisclosureRecord(
        rcept_no=str(item.get("rcept_no", "")).strip(),
        corp_code=str(item.get("corp_code", "")).strip(),
        stock_code=str(item.get("stock_code", "")).strip(),
        corp_name=str(item.get("corp_name", "")).strip(),
        corp_cls=str(item.get("corp_cls", "")).strip(),
        report_nm=str(item.get("report_nm", "")).strip(),
        rcept_dt=date(int(raw_dt[:4]), int(raw_dt[4:6]), int(raw_dt[6:])),
        flr_nm=str(item.get("flr_nm", "")).strip(),
        rm=str(item.get("rm", "")).strip(),
        norm_name=n.name,
        corrected=n.corrected,
        note=n.note,
    )


def fetch_range(start: date, end: date, corp_cls: str) -> Iterator[DisclosureRecord]:
    """한 시장의 기간 공시를 페이지 끝까지 낸다.

    Args:
        start: 시작일(포함).
        end: 종료일(포함).
        corp_cls: `Y`(유가) 또는 `K`(코스닥).

    Yields:
        공시 행. 상장 종목코드가 없는 공시(비상장 계열사 등)도 그대로 낸다 — 거르는 쪽은 채점이다.
    """
    page = 1
    while True:
        payload = get_json("list.json", {
            "bgn_de": start.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "corp_cls": corp_cls,
            "page_no": str(page),
            "page_count": str(PAGE_COUNT),
            "last_reprt_at": "N",          # 정정 전 원본을 보존한다
        })
        if str(payload.get("status")) == STATUS_NO_DATA:
            return
        for item in payload.get("list") or []:
            if (rec := _to_record(item)) is not None:
                yield rec
        total_page = int(payload.get("total_page") or 1)
        if page >= total_page:
            return
        page += 1


def fetch_days(
    start: date, end: date, markets: tuple[str, ...] = MARKETS
) -> list[DisclosureRecord]:
    """두 시장의 기간 공시를 모은다. 접수번호가 같으면 한 번만 담는다."""
    seen: set[str] = set()
    out: list[DisclosureRecord] = []
    for cls in markets:
        for rec in fetch_range(start, end, cls):
            if rec.rcept_no in seen:
                continue
            seen.add(rec.rcept_no)
            out.append(rec)
    return out
