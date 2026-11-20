"""Transport HTTPS + mTLS bersama untuk klien BFF dan Odoo (pola services/forecast-worker/efn_forecast/odoo.py).

- `requests` dengan `verify=<CA internal>`, sertifikat klien CN `segment-worker`, `trust_env=False` (tanpa proxy env),
  tanpa redirect, respons dibatasi 1 MB.
- Retry HANYA untuk jaringan/timeout/429/5xx (backoff eksponensial + jitter). Aman karena setiap panggilan idempoten di
  sisi penerima (`(run_id, segment_code, chunk_no)` di BFF, `(run_id, segment_code)` di Odoo).
- Log hanya target, status HTTP, percobaan, kode error — tidak pernah body, Authorization, atau isi respons mentah.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 1024 * 1024
BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)


class HttpError(RuntimeError):
    """kind: auth | company_mismatch | rejected | not_found | transport | invalid_response."""

    def __init__(self, kind: str, code: str = "", status: int = 0) -> None:
        super().__init__(f"{kind}:{code}" if code else kind)
        self.kind = kind
        self.code = code
        self.status = status


class Transport(Protocol):
    def post(self, path: str, body: bytes, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]: ...


class RequestsTransport:
    def __init__(self, base_url: str, ca_file: str | None, cert_file: str | None, key_file: str | None) -> None:
        import requests

        self._base = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.verify = ca_file if ca_file else True
        if cert_file and key_file:
            self._session.cert = (cert_file, key_file)

    def post(self, path: str, body: bytes, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
        resp = self._session.post(
            f"{self._base}{path}",
            data=body,
            headers=dict(headers),
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        try:
            content = resp.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
        finally:
            resp.close()
        if len(content) > MAX_RESPONSE_BYTES:
            raise HttpError("invalid_response", "too_large", resp.status_code)
        return resp.status_code, content


def error_code(body: bytes, prefix: str = "") -> str:
    """Kode aman dari body error (`{error|code|message}` atau bentuk json2 bersarang). Teks lain dibuang."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    candidates: list[Any] = [parsed.get("error"), parsed.get("code"), parsed.get("message")]
    err = parsed.get("error")
    if isinstance(err, dict):
        candidates.append(err.get("code"))
        candidates.append(err.get("message"))
        data = err.get("data")
        if isinstance(data, dict):
            candidates.append(data.get("message"))
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        text = candidate.strip()
        if prefix and text.startswith(prefix):
            text = text[len(prefix) :]
        if text.isidentifier() and len(text) <= 64:
            return text
    return ""


def parse_object(content: bytes) -> dict[str, Any]:
    if not content:
        return {}
    try:
        parsed = json.loads(content)
    except ValueError:
        raise HttpError("invalid_response", "not_json") from None
    if not isinstance(parsed, dict):
        raise HttpError("invalid_response", "not_object")
    return parsed


def classify(status: int, content: bytes, prefix: str = "") -> HttpError | None:
    if 200 <= status < 300:
        return None
    code = error_code(content, prefix)
    if status == 429 or status >= 500 or status == 0:
        return HttpError("transport", code or f"http_{status}", status)
    if code == "company_mismatch":
        return HttpError("company_mismatch", code, status)
    if status in (401, 403):
        return HttpError("auth", code, status)
    if status == 404:
        return HttpError("not_found", code, status)
    return HttpError("rejected", code, status)


@dataclass
class RetryPolicy:
    timeout_seconds: float = 25.0
    max_retries: int = 6
    sleep: Callable[[float], None] = time.sleep


def post_json(
    transport: Transport,
    path: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    policy: RetryPolicy,
    label: str,
    prefix: str = "",
    accept: Callable[[int, bytes], bool] | None = None,
) -> tuple[int, bytes]:
    """POST JSON dengan retry transport. `accept(status, body)` = respons non-2xx yang dianggap sukses (duplikat)."""
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    all_headers = {"Content-Type": "application/json", "Accept": "application/json", **headers}
    attempt = 0
    while True:
        try:
            status, content = transport.post(path, body, all_headers, policy.timeout_seconds)
        except HttpError:
            raise
        except Exception as exc:  # jaringan/timeout/TLS -> transport (diulang)
            status, content, err = 0, b"", HttpError("transport", type(exc).__name__)
        else:
            if accept is not None and accept(status, content):
                return status, content
            err = classify(status, content, prefix)
            if err is None:
                return status, content
        if err.kind != "transport" or attempt >= policy.max_retries:
            log.warning("%s gagal: %s status=%s percobaan=%d", label, err.kind, status, attempt + 1)
            raise err
        delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)] + secrets.randbelow(500) / 1000
        log.info("%s %s status=%s, ulang dalam %.1f s", label, err.code or err.kind, status, delay)
        policy.sleep(delay)
        attempt += 1
