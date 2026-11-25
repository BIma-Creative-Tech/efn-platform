"""Klien HTTP ESB OMS (G69, docs/04 §33, amandemen ADR-040 §6c — Proposed). Terpisah dari klien Core (`http.py`).

Allowlist persis (dicek SEBELUM koneksi dibuka; selain ini -> `MethodNotAllowed`):
- host persis `esbcore.co.id`, port 443, skema https; base URL tanpa path/kredensial/query;
- method POST saja (endpoint baca OMS memakai POST + body filter JSON);
- path persis `OMS_PATHS` (5 path baca). `push/*`, `sales-data`, `create-*`, `update-*`, path lain -> ditolak;
- query hanya `page` (int 1..`MAX_PAGE`) dan hanya untuk `sales-head`;
- body = objek JSON dengan kunci filter yang dikenal per path (`BODY_KEYS`); nilai tanggal `YYYY-MM-DD`, kode cabang
  `[A-Za-z0-9._-]{1,32}`.
Redirect tidak diikuti (transport `UrllibTransport`/`NoRedirect` yang sama dengan Core): 30x -> `EsbHttpError`.

Auth: `Authorization: Basic base64(user:password)` dari user + password ESB Core brand yang SAMA (sudah dimuat
`EsbSettings`; tanpa login JWT sehingga sesi Core tidak terputus). Header dibangun sekali di konstruktor; user/password
tidak disimpan terpisah, dan header tidak pernah masuk repr/log/pesan exception. User-Agent eksplisit (`USER_AGENT`):
default `Python-urllib/*` ditolak Cloudflare (403, error 1010).
Retry 429/5xx/jaringan dengan backoff + jitter (pola Core), token bucket + batas panggilan per run terpisah dari Core.
401 -> `EsbAuthError` (tanpa percobaan ulang: Basic auth gagal berulang bisa mengunci akun ESB yang dipakai kasir).
"""

import base64
import json
import random
import re
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping
from typing import Any

from efn_esb.http import (
    MAX_RETRIES_CAP,
    RETRY_STATUSES,
    USER_AGENT,
    CallBudgetExceeded,
    EsbAuthError,
    EsbError,
    EsbHttpError,
    EsbProtocolError,
    EsbResponseTooLarge,
    HttpResponse,
    MethodNotAllowed,
    TokenBucket,
    Transport,
    backoff_delay,
)

OMS_HOST = "esbcore.co.id"
OMS_BASE_URL = f"https://{OMS_HOST}"
P_BRANCH = "/external/general/get-branch"
P_VISIT_PURPOSE = "/external/general/get-visit-purpose"
P_PAYMENT_METHOD = "/external/general/get-payment-method"
P_BRANCH_SUMMARY = "/external/general/sales-branch-summary"
P_SALES_HEAD = "/external/general/sales-head"
OMS_PATHS = frozenset({P_BRANCH, P_VISIT_PURPOSE, P_PAYMENT_METHOD, P_BRANCH_SUMMARY, P_SALES_HEAD})
PAGED_PATHS = frozenset({P_SALES_HEAD})
DATE_FROM = "filterSalesDateFrom"
DATE_TO = "filterSalesDateTo"
BRANCH_CODE = "filterBranchCode"
BODY_KEYS: dict[str, frozenset[str]] = {
    P_BRANCH: frozenset(),
    P_VISIT_PURPOSE: frozenset(),
    P_PAYMENT_METHOD: frozenset(),
    P_BRANCH_SUMMARY: frozenset({DATE_FROM, DATE_TO}),
    P_SALES_HEAD: frozenset({DATE_FROM, DATE_TO, BRANCH_CODE}),
}
MAX_PAGE = 10_000
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


def check_oms_base(base_url: str) -> str:
    """Base URL OMS persis `https://esbcore.co.id` (port 443 boleh eksplisit). Selain itu -> `MethodNotAllowed`."""
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme != "https" or (parts.hostname or "").lower() != OMS_HOST:
        raise MethodNotAllowed("host ESB OMS tidak diizinkan")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise MethodNotAllowed("base URL ESB OMS tidak diizinkan")
    try:
        port = parts.port
    except ValueError:
        raise MethodNotAllowed("port ESB OMS tidak valid") from None
    if port not in (None, 443):
        raise MethodNotAllowed("port ESB OMS wajib 443")
    return OMS_BASE_URL


def check_oms_request(method: str, path: str, query: Mapping[str, Any], body: Any) -> None:
    """Guard allowlist OMS (murni). Pesan tanpa nilai body/query."""
    if method != "POST":
        raise MethodNotAllowed(f"method {method} ke ESB OMS ditolak (POST allowlist)")
    if path not in OMS_PATHS:
        raise MethodNotAllowed("path ESB OMS di luar allowlist")
    for key, value in query.items():
        if key != "page" or path not in PAGED_PATHS:
            raise MethodNotAllowed("query ESB OMS di luar allowlist")
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PAGE:
            raise MethodNotAllowed("query page ESB OMS tidak valid")
    if not isinstance(body, Mapping):
        raise MethodNotAllowed("body ESB OMS wajib objek")
    if path in PAGED_PATHS and ("page" not in query or BRANCH_CODE not in body):
        raise MethodNotAllowed("sales-head wajib page dan filterBranchCode (§33.2)")
    allowed = BODY_KEYS[path]
    for key, value in body.items():
        if key not in allowed:
            raise MethodNotAllowed("kunci body ESB OMS di luar allowlist")
        pattern = _BRANCH_RE if key == BRANCH_CODE else _DATE_RE
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise MethodNotAllowed("nilai body ESB OMS tidak valid")


def basic_auth_header(user: str, password: str) -> str:
    if not user or not password or ":" in user:
        raise EsbAuthError("kredensial ESB OMS tidak valid")
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


class OmsClient:
    """Klien OMS satu brand. Thread-safe (lock untuk hitungan panggilan)."""

    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,
        transport: Transport,
        bucket: TokenBucket,
        *,
        max_retries: int = 5,
        timeout: float = 60.0,
        max_calls: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        backoff_base: float = 1.0,
        backoff_cap: float = 30.0,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        self._base = check_oms_base(base_url)
        self._auth = basic_auth_header(user, password)
        self._transport = transport
        self._bucket = bucket
        self._max_retries = max(0, min(MAX_RETRIES_CAP, int(max_retries)))
        self._timeout = timeout
        self._max_calls = max_calls
        self._sleep = sleep
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        # Jitter backoff (bukan kriptografi).
        self._jitter = jitter or (lambda: random.uniform(0.0, 0.5))  # noqa: S311
        self._calls = 0
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"OmsClient(base={self._base!r}, auth=***, calls={self._calls})"

    @property
    def calls(self) -> int:
        return self._calls

    def reset_budget(self, max_calls: int | None) -> None:
        with self._lock:
            self._calls = 0
            self._max_calls = max_calls

    def _send(self, path: str, query: str, body: bytes) -> HttpResponse:
        with self._lock:
            if self._max_calls is not None and self._calls >= self._max_calls:
                raise CallBudgetExceeded("batas panggilan ESB OMS per run tercapai")
            self._calls += 1
        self._bucket.acquire()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Authorization": self._auth,
        }
        url = self._base + path + (f"?{query}" if query else "")
        return self._transport.send("POST", url, headers, body, self._timeout)

    def _send_retry(self, path: str, query: str, body: bytes) -> HttpResponse:
        attempt = 0
        while True:
            try:
                response = self._send(path, query, body)
            except (CallBudgetExceeded, MethodNotAllowed, EsbResponseTooLarge):
                raise
            except OSError as exc:
                if attempt >= self._max_retries:
                    raise EsbError(f"ESB OMS tidak terjangkau pada {path} ({type(exc).__name__})") from None
                self._sleep(backoff_delay(attempt, None, self._backoff_base, self._backoff_cap, self._jitter()))
                attempt += 1
                continue
            if response.status in RETRY_STATUSES and attempt < self._max_retries:
                retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
                self._sleep(backoff_delay(attempt, retry_after, self._backoff_base, self._backoff_cap, self._jitter()))
                attempt += 1
                continue
            return response

    def post(self, path: str, body: Mapping[str, Any] | None = None, page: int | None = None) -> Any:
        """POST `<base><path>[?page=N]` dengan body filter JSON -> JSON hasil parse (dict/list)."""
        payload = dict(body or {})
        query = {} if page is None else {"page": page}
        check_oms_request("POST", path, query, payload)
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        response = self._send_retry(path, urllib.parse.urlencode(query), raw)
        if response.status == 401:
            raise EsbAuthError("ESB OMS menolak kredensial (HTTP 401)")
        if not 200 <= response.status < 300:
            raise EsbHttpError(response.status, path)
        try:
            data = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise EsbProtocolError(f"respons ESB OMS bukan JSON pada {path}") from None
        failed = isinstance(data, Mapping) and (
            data.get("status") is False or data.get("status") in ("error", "failed") or data.get("success") is False
        )
        if failed:
            raise EsbProtocolError(f"ESB OMS status gagal pada {path}")
        return data
