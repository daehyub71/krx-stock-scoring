"""OpenDART REST 클라이언트 — 표준 라이브러리 `urllib`만 쓴다.

krx-signal-verify@de7aee1(`verify/dart.py`)의 호출·재시도·마스킹을 이식했다 (SPEC V11).

**키는 URL 쿼리에 실린다** (`crtfc_key=…`). 예외 메시지·로그에 URL이 들어가기 쉬우므로
규율이 아니라 장치로 막는다 — `DartError`를 만드는 순간 키가 가려진다.

| 상태 | 뜻 | 처리 |
|------|-----|------|
| `000` | 정상 | 항목 파싱 |
| `013` | 조회 결과 없음 | 오류가 아니다 — 흔한 정상 상태 |
| `020` | 요청 한도 초과(일 20,000) | 1회 재시도 → `DartRateLimitError` |
| `800` | 시스템 점검 (HTTP 200) | 1회 재시도 → `DartMaintenanceError` |
| 그 밖 | 키 없음·만료 등 | 재시도 없이 `DartError` |
"""

from __future__ import annotations

import json
import urllib.parse
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scoring.config import load_env

BASE = "https://opendart.fss.or.kr/api"
TIMEOUT = 20.0
RETRY_WAIT = 2.0
STATUS_OK, STATUS_NO_DATA = "000", "013"
STATUS_RATE_LIMIT, STATUS_MAINTENANCE = "020", "800"
RETRYABLE = (STATUS_RATE_LIMIT, STATUS_MAINTENANCE)


def _key() -> str:
    return load_env().get("DART_API_KEY", "")


def mask(text: str, secret: str) -> str:
    """문자열에서 비밀값을 가린다. 빈 secret은 아무것도 바꾸지 않는다."""
    return text.replace(secret, "***") if secret else text


class DartError(RuntimeError):
    """OpenDART 호출 실패. **메시지는 생성 시점에 마스킹된다.**"""

    def __init__(self, message: str, status: str | None = None) -> None:
        super().__init__(mask(message, _key()))
        self.status = status


class DartRateLimitError(DartError):
    """`020` — 일 한도 초과."""


class DartMaintenanceError(DartError):
    """`800` — 시스템 점검."""


def _get(path: str, params: dict[str, str], key: str) -> bytes:
    url = f"{BASE}/{path}?" + urllib.parse.urlencode({"crtfc_key": key, **params})
    try:
        with urlopen(Request(url), timeout=TIMEOUT) as resp:
            return bytes(resp.read())
    except HTTPError as exc:
        raise DartError(f"{path} HTTP {exc.code} {exc.reason}") from None
    except URLError as exc:
        raise DartError(f"{path} 연결 실패: {exc.reason}") from None
    except OSError as exc:
        raise DartError(f"{path} {exc}") from None


def _status_of(data: bytes) -> str:
    try:
        payload = json.loads(data)
    except ValueError:
        return ""
    return str(payload.get("status", "")) if isinstance(payload, dict) else ""


def _fetch(path: str, params: dict[str, str]) -> bytes:
    """일시 장애(네트워크·5xx·`020`·`800`)는 1회만 재시도한다."""
    key = _key()
    if not key:
        raise DartError("DART_API_KEY가 비어 있다")
    try:
        data = _get(path, params, key)
    except DartError:
        sleep(RETRY_WAIT)
        return _get(path, params, key)
    if _status_of(data) in RETRYABLE:
        sleep(RETRY_WAIT)
        return _get(path, params, key)
    return data


def get_json(path: str, params: dict[str, str]) -> dict[str, Any]:
    """JSON 끝점 하나. `013`도 그대로 돌려준다 (0건은 오류가 아니다).

    Raises:
        DartRateLimitError · DartMaintenanceError · DartError: 메시지에 키가 없다.
    """
    data = _fetch(path, params)
    try:
        payload = json.loads(data)
    except ValueError:
        raise DartError(f"{path} 응답이 JSON이 아니다: {data[:120]!r}") from None
    if not isinstance(payload, dict):
        raise DartError(f"{path} 응답이 사전이 아니다")
    status, message = str(payload.get("status", "")), str(payload.get("message", ""))
    if status in (STATUS_OK, STATUS_NO_DATA):
        return payload
    if status == STATUS_RATE_LIMIT:
        raise DartRateLimitError(f"{path} status=020 한도 초과: {message}", status)
    if status == STATUS_MAINTENANCE:
        raise DartMaintenanceError(f"{path} status=800 점검 중: {message}", status)
    raise DartError(f"{path} status={status}: {message}", status)


def fetch_corp_codes() -> bytes:
    """`corpCode.xml` 응답 바이트 (zip). 파싱은 `corp.parse_corp_codes()`."""
    return _fetch("corpCode.xml", {})
