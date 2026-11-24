"""Klien HTTP ESB Core untuk prodplan (docs/04 §30.4, §30.9; ADR-051 Proposed).

Salinan yang DIKERASKAN dari `services/esb-extract/efn_esb/http.py` (tidak ada import lintas service):
- Allowlist method + template path (§30.4) ditegakkan SEBELUM socket dibuka (`EsbForbiddenCall`). Endpoint apa pun di
  luar tabel (termasuk `.../authorize`, `.../import`, `.../push`) ditolak.
- POST tulis hanya satu endpoint produksi per proses (`mfg_mode`), dan POST/DELETE tulis hanya bila klien dibuat dengan
  `allow_write=True` (worker outbox + `PRODPLAN_WRITE_ENABLED=true`).
- Base URL hanya dua nilai persis (`config.ESB_BASE_URLS`); https; TLS >= 1.2 + verifikasi hostname (CA publik certifi);
  `http.client` langsung: tanpa proxy env, tanpa redirect.
- GET: login ulang sekali pada 401; retry terbatas pada jaringan/429/5xx. POST/DELETE tulis: TANPA retry otomatis.
  Transport membedakan gagal SEBELUM request terkirim (`EsbNotSent`) dan SETELAH terkirim (`EsbSentUnknown`).
- Nomor dokumen di-URL-encode (`"SM202611240001 - 1"` -> `SM202611240001%20-%201`) setelah lolos regex §30.4.
- Password, token, header Authorization, body, query string tidak pernah masuk log/pesan exception.
"""

import http.client
import json
import logging
import random
import re
import ssl
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import certifi

from efn_prodplan import metrics
from efn_prodplan.config import ESB_BASE_URLS, MFG_MODES
from efn_prodplan.logs import log_event

USER_AGENT = "efn-prodplan/0.1"
LOGIN_PATH = "/auth/login"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
ERROR_BODY_MAX_BYTES = 64 * 1024
_READ_CHUNK = 64 * 1024
DOC_NUM_RE = re.compile(r"^[A-Za-z0-9 ._/-]{1,40}$")
_PATH_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~%/")
_ENC_NUM = r"[A-Za-z0-9._~%-]{1,120}"

DOC_BASE: Mapping[str, str] = {
    "simple_transfer": "/simple-transfer",
    "goods_transfer_request": "/inventory/goods-transfer-request",
    "simple_manufacturing": "/production/simple-manufacturing",
}
DOC_POST: Mapping[str, str] = {
    "simple_transfer": "/simple-transfer",
    "goods_transfer_request": "/inventory/goods-transfer-request",
}
MFG_POST: Mapping[str, str] = {
    "actual": "/production/simple-manufacturing/assembly-actual",
    "standard": "/production/simple-manufacturing/assembly",
}
DOC_NUMBER_FIELD: Mapping[str, str] = {
    "simple_transfer": "simpleTransferNum",
    "goods_transfer_request": "transferNum",
    "simple_manufacturing": "simpleManufacturingNum",
}
GET_EXACT = frozenset(
    {
        "/product",
        "/product/list",
        "/product/bom",
        "/product/branch-stock",
        "/branch/list/user",  # nama cabang untuk tampilan (G66 putaran 2, keputusan lead)
        "/purchase/purchase-request",
        "/simple-transfer",
        "/inventory/goods-transfer-request",
        "/production/simple-manufacturing",
    }
)
_GET_PATTERNS: tuple[tuple[re.Pattern[str], str, bool], ...] = (
    (re.compile(r"^/product/bom/detail/(\d{1,12})$"), "/product/bom/detail/{bomID}", False),
    # Header BOM (produk hasil; daftar BOM tidak memuat productDetailID). Tambahan G66 atas keputusan lead.
    (re.compile(r"^/product/bom/(\d{1,12})$"), "/product/bom/{bomID}", False),
    (re.compile(r"^/product/(\d{1,12})$"), "/product/{productID}", False),
    (re.compile(r"^/location/branch/(\d{1,12})$"), "/location/branch/{branchID}", False),
    (re.compile(rf"^/purchase/purchase-request/({_ENC_NUM})$"), "/purchase/purchase-request/{num}", True),
    (re.compile(rf"^/simple-transfer/({_ENC_NUM})$"), "/simple-transfer/{num}", True),
    (
        re.compile(rf"^/inventory/goods-transfer-request/({_ENC_NUM})$"),
        "/inventory/goods-transfer-request/{num}",
        True,
    ),
    (
        re.compile(rf"^/production/simple-manufacturing/({_ENC_NUM})$"),
        "/production/simple-manufacturing/{num}",
        True,
    ),
)
_DELETE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(rf"^/simple-transfer/({_ENC_NUM})$"), "/simple-transfer/{num}"),
    (re.compile(rf"^/inventory/goods-transfer-request/({_ENC_NUM})$"), "/inventory/goods-transfer-request/{num}"),
    (re.compile(rf"^/production/simple-manufacturing/({_ENC_NUM})$"), "/production/simple-manufacturing/{num}"),
)


# ====================================================================== galat
class EsbError(RuntimeError):
    """Kesalahan klien ESB. Pesan tidak memuat kredensial, token, query string, maupun body."""


class EsbForbiddenCall(EsbError):
    """Method/path/host di luar allowlist §30.4 (ditolak sebelum socket dibuka)."""


class EsbAuthError(EsbError):
    """Login ditolak / token ditolak setelah login ulang."""


class EsbHttpError(EsbError):
    def __init__(self, status: int, template: str) -> None:
        super().__init__(f"ESB HTTP {status} pada {template}")
        self.status = status
        self.template = template


class EsbProtocolError(EsbError):
    """Respons bukan envelope JSON ESB yang diharapkan."""


class EsbResponseTooLarge(EsbProtocolError):
    """Body respons melebihi `PRODPLAN_ESB_MAX_RESPONSE_BYTES`."""


class CallBudgetExceeded(EsbError):
    """`PRODPLAN_JOB_MAX_CALLS` tercapai."""


class EsbNotSent(EsbError):
    """Gagal SEBELUM request terkirim (DNS/connect/TLS handshake) -> aman dicoba lagi."""


class EsbSentUnknown(EsbError):
    """Gagal SETELAH request terkirim (timeout/putus/5xx) -> hasil tidak diketahui, wajib rekonsiliasi."""


class EsbRejected(EsbError):
    """ESB menolak tulis (4xx validasi / envelope `fail`). Tidak dikirim ulang otomatis."""

    def __init__(self, status: int, code: str = "") -> None:
        super().__init__(f"ESB menolak permintaan (HTTP {status})")
        self.status = status
        self.code = code[:16]


# ====================================================================== allowlist (murni)
def encode_doc_number(num: str) -> str:
    """Validasi regex §30.4 lalu URL-encode satu segmen (`/` dan spasi ikut di-encode)."""
    if not isinstance(num, str) or not DOC_NUM_RE.fullmatch(num) or not any(ch.isdigit() for ch in num):
        raise EsbForbiddenCall("nomor dokumen ESB tidak valid")
    return urllib.parse.quote(num, safe="")


def _valid_encoded_num(encoded: str) -> bool:
    try:
        decoded = urllib.parse.unquote(encoded, errors="strict")
    except UnicodeDecodeError:
        return False
    if not DOC_NUM_RE.fullmatch(decoded) or not any(ch.isdigit() for ch in decoded):
        return False
    return urllib.parse.quote(decoded, safe="") == encoded  # bentuk kanonik saja


def classify_call(method: str, path: str, *, mfg_mode: str, allow_write: bool) -> str:
    """Kembalikan template path bila (method, path) ada di allowlist §30.4; selain itu `EsbForbiddenCall`."""
    if mfg_mode not in MFG_MODES:
        raise EsbForbiddenCall("mode produksi tidak dikenal")
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or ".." in path
        or "//" in path
        or any(ch not in _PATH_CHARS for ch in path)
        or len(path) > 200
    ):
        raise EsbForbiddenCall("path ESB tidak valid")
    if method == "POST" and path == LOGIN_PATH:
        return LOGIN_PATH
    if method == "GET":
        if path in GET_EXACT:
            return path
        for pattern, template, is_num in _GET_PATTERNS:
            match = pattern.fullmatch(path)
            if match and (not is_num or _valid_encoded_num(match.group(1))):
                return template
        raise EsbForbiddenCall("GET ke path ESB ini tidak diizinkan")
    if method == "POST":
        allowed = set(DOC_POST.values()) | {MFG_POST[mfg_mode]}
        if path not in allowed:
            raise EsbForbiddenCall("POST ke path ESB ini tidak diizinkan")
        if not allow_write:
            raise EsbForbiddenCall("tulis ke ESB tidak diaktifkan untuk klien ini")
        return path
    if method == "DELETE":
        for pattern, template in _DELETE_PATTERNS:
            match = pattern.fullmatch(path)
            if match and _valid_encoded_num(match.group(1)):
                if not allow_write:
                    raise EsbForbiddenCall("tulis ke ESB tidak diaktifkan untuk klien ini")
                return template
        raise EsbForbiddenCall("DELETE ke path ESB ini tidak diizinkan")
    raise EsbForbiddenCall("method ke ESB tidak diizinkan")


def validate_esb_url(url: str, base_url: str) -> None:
    """URL akhir wajib diawali base yang diizinkan (host + prefix path persis)."""
    if base_url not in ESB_BASE_URLS:
        raise EsbForbiddenCall("base URL ESB tidak diizinkan")
    if not url.startswith(base_url + "/"):
        raise EsbForbiddenCall("host ESB tidak diizinkan")


# ====================================================================== transport
@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class Transport(Protocol):
    def send(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse: ...


def esb_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=certifi.where())
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def read_limited(stream: Any, headers: Any, max_bytes: int, *, strict: bool = True) -> bytes:
    length_raw = headers.get("Content-Length") if headers is not None else None
    length = int(length_raw) if isinstance(length_raw, str) and length_raw.strip().isdigit() else None
    if strict and length is not None and length > max_bytes:
        raise EsbResponseTooLarge("respons ESB melebihi batas ukuran")
    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        chunk = stream.read(min(_READ_CHUNK, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    data = b"".join(chunks)
    if total > max_bytes:
        if strict:
            raise EsbResponseTooLarge("respons ESB melebihi batas ukuran")
        return data[:max_bytes]
    return data


class HttpsTransport:
    """`http.client` langsung: connect() dipisah dari request() agar gagal-sebelum-kirim bisa dibedakan."""

    def __init__(self, max_response_bytes: int, context: ssl.SSLContext | None = None) -> None:
        self._max = int(max_response_bytes)
        self._ctx = context or esb_ssl_context()

    def send(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise EsbForbiddenCall("transport ESB hanya https://")
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=timeout, context=self._ctx)
        try:
            try:
                conn.connect()
            except (OSError, ssl.SSLError) as exc:
                raise EsbNotSent(f"ESB tidak terjangkau ({type(exc).__name__})") from None
            try:
                conn.request(method, target, body=body, headers=dict(headers))
                response = conn.getresponse()
                if 200 <= response.status < 300:
                    data = read_limited(response, response.headers, self._max)
                else:
                    data = read_limited(response, None, min(self._max, ERROR_BODY_MAX_BYTES), strict=False)
                return HttpResponse(response.status, data, dict(response.headers.items()))
            except EsbResponseTooLarge:
                raise
            except (OSError, http.client.HTTPException) as exc:
                raise EsbSentUnknown(f"koneksi ESB terputus setelah request ({type(exc).__name__})") from None
        finally:
            conn.close()


class TokenBucket:
    """Token bucket thread-safe (per brand). Clock/sleep diinjeksi untuk test."""

    def __init__(
        self,
        rate: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate harus > 0")
        self._rate = float(rate)
        self._capacity = max(1.0, float(rate))
        self._tokens = self._capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            self._sleep(wait)


# ====================================================================== envelope (murni)
def parse_envelope(response: HttpResponse, template: str) -> Any:
    try:
        data = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EsbProtocolError(f"respons ESB bukan JSON pada {template}") from None
    if not isinstance(data, dict):
        raise EsbProtocolError(f"respons ESB bukan objek pada {template}")
    status = data.get("status")
    if status not in (None, "ok"):
        raise EsbRejected(response.status, str(data.get("code") or ""))
    return data.get("result")


def page_rows(result: Any) -> list[dict[str, Any]]:
    """Baris dari `result` list atau `{page, limit, count, data}` (`data` boleh null)."""
    if isinstance(result, list):
        data: Any = result
    elif isinstance(result, Mapping):
        data = result.get("data") or []
    else:
        return []
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def doc_number(kind: str, result: Any) -> str | None:
    if not isinstance(result, Mapping):
        return None
    value = result.get(DOC_NUMBER_FIELD[kind])
    if isinstance(value, str) and DOC_NUM_RE.fullmatch(value.strip()):
        return value.strip()
    return None


# ====================================================================== klien
class EsbClient:
    """Klien satu brand. Thread-safe (job worker dan outbox worker boleh berbagi)."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        transport: Transport,
        bucket: TokenBucket,
        *,
        mfg_mode: str,
        allow_write: bool,
        timeout: float = 30.0,
        max_calls: int | None = None,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if base_url not in ESB_BASE_URLS:
            raise EsbForbiddenCall("base URL ESB tidak diizinkan")
        if mfg_mode not in MFG_MODES:
            raise EsbForbiddenCall("mode produksi tidak dikenal")
        self._base = base_url
        self._username = username
        self._password = password
        self._transport = transport
        self._bucket = bucket
        self.mfg_mode = mfg_mode
        self.allow_write = allow_write
        self._timeout = timeout
        self._max_calls = max_calls
        self._max_retries = max(0, min(3, max_retries))
        self._sleep = sleep
        self._clock = clock
        self._token: str | None = None
        self._calls = 0
        self._lock = threading.Lock()
        self._login_lock = threading.Lock()

    def __repr__(self) -> str:
        return f"EsbClient(base={self._base!r}, user=***, calls={self._calls})"

    @property
    def calls(self) -> int:
        return self._calls

    def reset_budget(self, max_calls: int | None) -> None:
        with self._lock:
            self._calls = 0
            self._max_calls = max_calls

    # ------------------------------------------------------------------ inti
    def _send(self, method: str, path: str, query: str, body: bytes | None, token: str | None) -> HttpResponse:
        template = classify_call(method, path, mfg_mode=self.mfg_mode, allow_write=self.allow_write)
        url = self._base + path + (f"?{query}" if query else "")
        validate_esb_url(url, self._base)
        with self._lock:
            if self._max_calls is not None and self._calls >= self._max_calls:
                raise CallBudgetExceeded("batas panggilan ESB per job tercapai")
            self._calls += 1
        self._bucket.acquire()
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        started = self._clock()
        status_label = "error"
        try:
            response = self._transport.send(method, url, headers, body, self._timeout)
            status_label = str(response.status)
            return response
        except EsbNotSent:
            status_label = "not_sent"
            raise
        except EsbSentUnknown:
            status_label = "unknown"
            raise
        finally:
            elapsed = max(0.0, self._clock() - started)
            metrics.ESB_REQUESTS.inc(method, template, status_label)
            metrics.ESB_SECONDS.observe(elapsed)
            log_event(
                "esb_call",
                logging.INFO,
                esb_method=method,
                esb_path_template=template,
                http_status=status_label,
                duration_ms=int(elapsed * 1000),
            )

    def _send_retry(self, method: str, path: str, query: str, body: bytes | None, token: str | None) -> HttpResponse:
        """Hanya untuk GET dan login (idempoten)."""
        attempt = 0
        while True:
            try:
                response = self._send(method, path, query, body, token)
            except (EsbNotSent, EsbSentUnknown) as exc:
                if attempt >= self._max_retries:
                    raise EsbError(f"ESB tidak terjangkau ({type(exc).__name__})") from None
                self._sleep(_backoff(attempt))
                attempt += 1
                continue
            if response.status in RETRY_STATUSES and attempt < self._max_retries:
                self._sleep(_backoff(attempt))
                attempt += 1
                continue
            return response

    def login(self) -> None:
        body = json.dumps({"username": self._username, "password": self._password}).encode("utf-8")
        response = self._send_retry("POST", LOGIN_PATH, "", body, None)
        if response.status in (400, 401, 403):
            raise EsbAuthError(f"login ESB ditolak (HTTP {response.status})")
        if not 200 <= response.status < 300:
            raise EsbHttpError(response.status, LOGIN_PATH)
        try:
            result = parse_envelope(response, LOGIN_PATH)
        except EsbRejected:
            raise EsbAuthError("login ESB ditolak") from None
        token = result.get("accessToken") if isinstance(result, Mapping) else None
        if not isinstance(token, str) or not token:
            raise EsbAuthError("respons login ESB tanpa token")
        with self._lock:
            self._token = token

    def _current_token(self) -> str:
        with self._login_lock:
            if self._token is None:
                self.login()
            token = self._token
        if token is None:  # pragma: no cover
            raise EsbAuthError("token ESB kosong")
        return token

    def _invalidate(self, token: str) -> None:
        with self._lock:
            if self._token == token:
                self._token = None

    # ------------------------------------------------------------------ baca
    def get_response(self, path: str, params: Mapping[str, Any] | None = None) -> HttpResponse:
        classify_call("GET", path, mfg_mode=self.mfg_mode, allow_write=self.allow_write)
        query = urllib.parse.urlencode([(k, v) for k, v in (params or {}).items() if v is not None])
        relogged = False
        while True:
            token = self._current_token()
            response = self._send_retry("GET", path, query, None, token)
            if response.status == 401:
                self._invalidate(token)
                if relogged:
                    raise EsbAuthError("token ESB ditolak setelah login ulang")
                relogged = True
                continue
            return response

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        template = classify_call("GET", path, mfg_mode=self.mfg_mode, allow_write=self.allow_write)
        response = self.get_response(path, params)
        if not 200 <= response.status < 300:
            raise EsbHttpError(response.status, template)
        try:
            return parse_envelope(response, template)
        except EsbRejected:
            raise EsbProtocolError(f"ESB status gagal pada {template}") from None

    def get_all(
        self, path: str, params: Mapping[str, Any] | None = None, *, limit: int = 200, max_pages: int = 100
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            query = dict(params or {})
            query["page"] = page
            query["limit"] = limit
            result = self.get(path, query)
            data = page_rows(result)
            rows.extend(data)
            if isinstance(result, list) or not data:
                return rows
            count = result.get("count") if isinstance(result, Mapping) else None
            if isinstance(count, int) and not isinstance(count, bool):
                if len(rows) >= count:
                    return rows
            elif len(data) < limit:
                return rows
            page += 1
            if page > max_pages:
                raise EsbProtocolError(f"{path}: melebihi {max_pages} halaman")

    def get_doc(self, kind: str, num: str) -> dict[str, Any] | None:
        """Detail dokumen; `None` bila ESB membalas 404 (dokumen tidak ada)."""
        path = f"{DOC_BASE[kind]}/{encode_doc_number(num)}"
        template = classify_call("GET", path, mfg_mode=self.mfg_mode, allow_write=self.allow_write)
        response = self.get_response(path)
        if response.status == 404:
            return None
        if not 200 <= response.status < 300:
            raise EsbHttpError(response.status, template)
        try:
            result = parse_envelope(response, template)
        except EsbRejected:
            return None  # envelope `fail` pada detail = dokumen tidak ditemukan (toleran)
        return result if isinstance(result, dict) else None

    # ------------------------------------------------------------------ tulis (tanpa retry otomatis)
    def _write(self, method: str, path: str, body: bytes | None) -> HttpResponse:
        classify_call(method, path, mfg_mode=self.mfg_mode, allow_write=self.allow_write)
        relogged = False
        while True:
            try:
                token = self._current_token()
            except EsbAuthError:
                raise
            except EsbError as exc:
                # Login gagal = dokumen belum terkirim.
                raise EsbNotSent(f"login ESB gagal sebelum tulis ({type(exc).__name__})") from None
            response = self._send(method, path, "", body, token)
            if response.status == 401 and not relogged:
                # 401 = ditolak autentikasi, permintaan tidak diproses -> aman diulang SEKALI setelah login ulang.
                self._invalidate(token)
                relogged = True
                continue
            return response

    def post_doc(self, kind: str, body: Mapping[str, Any]) -> str:
        """POST dokumen; kembalikan nomor dokumen. `EsbRejected` / `EsbNotSent` / `EsbSentUnknown` sesuai §30.9."""
        path = DOC_POST.get(kind) if kind != "simple_manufacturing" else MFG_POST[self.mfg_mode]
        if path is None:
            raise EsbForbiddenCall("jenis dokumen tidak dikenal")
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        response = self._write("POST", path, raw)
        if response.status >= 500:
            raise EsbSentUnknown(f"ESB HTTP {response.status} setelah request")
        if response.status == 401:
            raise EsbAuthError("token ESB ditolak setelah login ulang")
        if not 200 <= response.status < 300:
            raise EsbRejected(response.status, _envelope_code(response))
        try:
            result = parse_envelope(response, path)
        except EsbProtocolError:
            raise EsbSentUnknown("respons tulis ESB tidak terbaca") from None
        number = doc_number(kind, result)
        if number is None:
            raise EsbSentUnknown("respons tulis ESB tanpa nomor dokumen")
        return number

    def delete_doc(self, kind: str, num: str) -> str:
        """DELETE dokumen -> `deleted` atau `not_found`. 4xx lain -> `EsbRejected`."""
        path = f"{DOC_BASE[kind]}/{encode_doc_number(num)}"
        response = self._write("DELETE", path, None)
        if response.status >= 500:
            raise EsbSentUnknown(f"ESB HTTP {response.status} setelah request")
        if response.status == 404:
            return "not_found"
        if response.status == 401:
            raise EsbAuthError("token ESB ditolak setelah login ulang")
        if not 200 <= response.status < 300:
            raise EsbRejected(response.status, _envelope_code(response))
        try:
            parse_envelope(response, path)
        except EsbProtocolError:
            pass  # 2xx tanpa envelope dianggap berhasil; diverifikasi GET oleh worker
        return "deleted"


def _backoff(attempt: int) -> float:
    # Jitter backoff, bukan kriptografi.
    return min(10.0, 1.0 * (2**attempt)) + random.uniform(0.0, 0.5)  # noqa: S311


def _envelope_code(response: HttpResponse) -> str:
    try:
        data = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    return str(data.get("code") or "")[:16] if isinstance(data, dict) else ""
