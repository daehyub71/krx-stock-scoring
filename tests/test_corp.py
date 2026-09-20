"""corp — corpCode.xml(zip) → {stock_code: corp_code}. 순수 함수, 네트워크 없음.

표본: tests/fixtures/dart/corpcode_sample.xml — krx-signal-verify@de7aee1에서 테스트와 함께 이식.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from scoring.sources.corp import CorpCodeError, parse_corp_codes

FIXTURES = Path(__file__).parent / "fixtures" / "dart"
SAMPLE_XML = (FIXTURES / "corpcode_sample.xml").read_bytes()
ERROR_800 = (FIXTURES / "corpcode_error_800.xml").read_bytes()


def as_zip(xml: bytes, name: str = "CORPCODE.xml") -> bytes:
    """OpenDART가 주는 형태 — zip 안에 XML 하나."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, xml)
    return buf.getvalue()


@pytest.fixture(scope="module")
def codes() -> dict[str, str]:
    return parse_corp_codes(as_zip(SAMPLE_XML))


# ── 정상 (①~③) ─────────────────────────────────────────────────


def test_listed_company_maps_ticker_to_corp_code(codes: dict[str, str]) -> None:
    assert codes["005930"] == "00126380"


def test_values_are_stripped(codes: dict[str, str]) -> None:
    """값 앞뒤 공백·개행이 섞여 와도 키와 값 모두 깨끗해야 한다."""
    assert codes["222040"] == "01234567"
    assert " 01234567 " not in codes.values()


def test_lettered_ticker_is_kept_as_string(codes: dict[str, str]) -> None:
    """티커는 숫자가 아니다 — 0126Z0."""
    assert codes["0126Z0"] == "02000001"


# ── 비상장 (④~⑥) ───────────────────────────────────────────────


def test_unlisted_entries_are_excluded(codes: dict[str, str]) -> None:
    """공백 한 칸 · 빈 태그 · 태그 없음 — 셋 다 사전에 없어야 한다. KeyError도 안 된다."""
    assert " " not in codes and "" not in codes
    assert "00434003" not in codes.values()
    assert "00434456" not in codes.values()
    assert "00430964" not in codes.values()


def test_only_listed_keys_survive(codes: dict[str, str]) -> None:
    assert set(codes) == {"005930", "222040", "0126Z0", "079940", "417310"}


# ── 중복 (⑦~⑧) ────────────────────────────────────────────────


def test_duplicate_ticker_keeps_latest_modify_date(codes: dict[str, str]) -> None:
    """같은 stock_code에 corp_code가 둘이면 modify_date가 최신인 쪽 (재상장·법인 승계)."""
    assert codes["079940"] == "00222222"


def test_duplicate_ticker_order_independent() -> None:
    """최신이 먼저 오든 나중에 오든 결과가 같아야 한다 — 파일 순서에 기대지 않는다."""
    xml = b"""<?xml version="1.0" encoding="UTF-8"?><result>
      <list><corp_code>B</corp_code><corp_name>x</corp_name><stock_code>111111</stock_code><modify_date>20240301</modify_date></list>
      <list><corp_code>A</corp_code><corp_name>x</corp_name><stock_code>111111</stock_code><modify_date>20190101</modify_date></list>
    </result>"""
    assert parse_corp_codes(as_zip(xml)) == {"111111": "B"}


def test_identical_duplicate_collapses(codes: dict[str, str]) -> None:
    assert codes["417310"] == "00333333"


# ── 오류 본문 · 깨진 입력 ────────────────────────────────────────


def test_maintenance_body_raises_with_status() -> None:
    """점검 중엔 zip이 아니라 XML 오류 본문이 HTTP 200으로 온다 (2026-08-29 실측)."""
    with pytest.raises(CorpCodeError, match="800"):
        parse_corp_codes(ERROR_800)


def test_garbage_bytes_raise() -> None:
    with pytest.raises(CorpCodeError):
        parse_corp_codes(b"<html>nope</html>")


def test_zip_without_xml_entry_raises() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "no xml here")
    with pytest.raises(CorpCodeError):
        parse_corp_codes(buf.getvalue())


def test_empty_result_gives_empty_dict() -> None:
    xml = b'<?xml version="1.0" encoding="UTF-8"?><result></result>'
    assert parse_corp_codes(as_zip(xml)) == {}
