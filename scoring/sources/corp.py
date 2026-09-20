"""OpenDART `corpCode.xml` 파서 — zip 바이트 → {stock_code: corp_code}.

krx-signal-verify@de7aee1(`verify/corp.py`)에서 **테스트와 함께 이식**했다 (SPEC V11).

순수 함수다. 네트워크를 모른다 — 바이트를 받아 사전을 돌려줄 뿐이다.
바이트를 받아 오는 일은 `dart.py`가 한다.

**입력이 zip이 아닐 수 있다.** 점검 중이거나 키가 틀리면 OpenDART는 HTTP 200으로
`<result><status>800</status>…</result>` 같은 XML 오류 본문을 준다 (2026-08-29 실측).
`PK` 매직 바이트로 먼저 가려낸다.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile

ZIP_MAGIC = b"PK"

_STATUS = re.compile(rb"<status>\s*(\w+)\s*</status>")
_MESSAGE = re.compile(rb"<message>\s*(.*?)\s*</message>", re.S)


class CorpCodeError(ValueError):
    """corpCode.xml을 사전으로 만들 수 없을 때 — 오류 본문·깨진 zip·XML 없음."""


def _error_from_body(data: bytes) -> CorpCodeError:
    """zip이 아닌 본문에서 status·message를 뽑아 예외로 만든다."""
    status = _STATUS.search(data)
    message = _MESSAGE.search(data)
    if status:
        msg = message.group(1).decode("utf-8", "replace") if message else ""
        return CorpCodeError(f"corpCode.xml 오류 본문 status={status.group(1).decode()}: {msg}")
    head = data[:80].decode("utf-8", "replace")
    return CorpCodeError(f"corpCode.xml이 zip이 아니다: {head!r}")


def _xml_from_zip(data: bytes) -> bytes:
    """zip에서 XML 항목 하나를 꺼낸다."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".xml")]
            if not names:
                raise CorpCodeError(f"zip 안에 XML이 없다: {z.namelist()}")
            return z.read(names[0])
    except zipfile.BadZipFile as exc:
        raise CorpCodeError(f"corpCode.xml zip 손상: {exc}") from exc


def parse_corp_codes(data: bytes) -> dict[str, str]:
    """corpCode.xml zip 바이트를 `{stock_code: corp_code}`로 만든다.

    Args:
        data: `GET /api/corpCode.xml` 응답 본문. 정상이면 zip.

    Returns:
        상장사만 담긴 사전. 티커는 문자열이다 (`0126Z0`처럼 문자가 섞인 6자리가 실재).

    Raises:
        CorpCodeError: zip이 아닌 오류 본문(점검 `800`, 키 오류 `010`/`011`), 깨진 zip, XML 없음.

    Note:
        - `stock_code`가 비어 있으면(공백 한 칸 · 빈 태그 · 태그 없음) 비상장 — 제외한다.
        - 같은 `stock_code`에 `corp_code`가 둘이면 `modify_date`가 최신인 쪽을 쓴다.
          파일 순서에 기대지 않는다.
    """
    if not data.startswith(ZIP_MAGIC):
        raise _error_from_body(data)

    xml = _xml_from_zip(data)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise CorpCodeError(f"CORPCODE.xml 파싱 실패: {exc}") from exc

    latest: dict[str, tuple[str, str]] = {}  # stock_code → (modify_date, corp_code)
    for item in root.iter("list"):
        stock = (item.findtext("stock_code") or "").strip()
        corp = (item.findtext("corp_code") or "").strip()
        if not stock or not corp:
            continue
        modified = (item.findtext("modify_date") or "").strip()
        if stock not in latest or modified > latest[stock][0]:
            latest[stock] = (modified, corp)
    return {stock: corp for stock, (_, corp) in latest.items()}
