"""Klien HTTP ESB Core untuk prodplan (docs/04 §30.4, §30.9; ADR-051 Proposed).

Salinan yang DIKERASKAN dari `services/esb-extract/efn_esb/http.py` (tidak ada import lintas service):
- Allowlist method + template path (§30.4) ditegakkan SEBELUM socket dibuka (`EsbForbiddenCall`). Endpoint apa pun di
  luar tabel (termasuk `.../authorize`, `.../import`, `.../push`) ditolak.
- POST tulis hanya satu endpoint produksi per proses (`mfg_mode`), dan POST/DELETE tulis hanya bila klien dibuat dengan
  `allow_write=True` (worker outbox + `PRODPLAN_WRITE_ENABLED=true`).
- Base URL hanya dua nilai persis (`config.ESB_BASE_URLS`); https; TLS >= 1.2 + verifikasi hostname (CA publik certifi);
  `http.client` langsung: tanpa proxy env, tanpa redirect.
- §30.17 (amandemen A1): klien membawa token akses ESB MILIK PENGGUNA (sesi memori prodplan). Tidak ada akun service,
  tidak ada login ulang otomatis (prodplan tidak memegang password): 401 -> callback `on_unauthorized` (sesi dibuang)
  + `EsbTokenRejected`. `login()` hanya dipakai `POST /v1/session` (satu kali, tanpa retry); `refreshToken` dibuang.
- GET: retry terbatas pada jaringan/429/5xx. POST/DELETE tulis: TANPA retry otomatis.
  Transport membedakan gagal SEBELUM request terkirim (`EsbNotSent`) dan SETELAH terkirim (`EsbSentUnknown`).
- Nomor dokumen di-URL-encode (`"SM202611240001 - 1"` -> `SM202611240001%20-%201`) setelah lolos regex §30.4.
- Password, token, header Authorization, body, query string tidak pernah masuk log/pesan exception/repr.
"""

import base64
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
from dataclasses import dataclass, field
from typing import Any, Protocol

import certifi

from efn_prodplan import metrics
from efn_prodplan.config import ESB_BASE_URLS, MFG_MODES
from efn_prodplan.logs import log_event

USER_AGENT = "efn-prodplan/0.1"
LOGIN_PATH = "/auth/login"
LOGOUT_PATH = "/auth/logout"
LOGOUT_TIMEOUT_SECONDS = 5.0
LOGIN_TIMEOUT_SECONDS = 8.0  # SEC-236: antrean 5 dtk + login 8 dtk < timeout insight-web 15 dtk
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
        "/branch/list/user",  # cabang pengguna (pembatasan cabang §30.17.6) + nama tampilan
        "/user/access",  # izin pengguna -> peran (§30.17.6)
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
    """Login ditolak / token ditolak."""


class EsbInvalidCredentials(EsbAuthError):
    """`POST /auth/login` ditolak (HTTP 400/401/403 atau envelope gagal) -> `invalid_credentials`."""


class EsbTokenRejected(EsbAuthError):
    """ESB membalas 401 untuk token pengguna -> sesi dibuang (`esb_401`); tidak ada login ulang otomatis."""


class EsbUnavailable(EsbError):
    """Login gagal karena jaringan/timeout/5xx/respons tak terbaca (tidak dihitung lockout)."""


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
    if method == "POST" and path in (LOGIN_PATH, LOGOUT_PATH):
        return path  # sesi pengguna (§30.17.3); diizinkan tanpa allow_write
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
    body: bytes = field(repr=False)  # SEC-241: body login memuat accessToken/refreshToken
    headers: Mapping[str, str] = field(repr=False)


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


def jwt_claims(token: str) -> dict[str, Any]:
    """Payload JWT `accessToken` TANPA verifikasi tanda tangan (token diterima langsung dari ESB lewat TLS; hanya untuk
    penjadwalan `exp` + `userRoleID`). Tak terbaca -> {}. Klaim tidak pernah dilog."""
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        return {}
    segment = parts[1]
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class LoginResult:
    """Hasil `POST /auth/login` yang dipakai prodplan. Field lain (termasuk `refreshToken`) dibuang saat diterima."""

    access_token: str = field(repr=False)
    full_name: str = field(repr=False)
    company_code: str
    flag_active: bool


def parse_login(result: Any) -> LoginResult:
    if not isinstance(result, Mapping):
        raise EsbUnavailable("respons login ESB tidak terbaca")
    token = result.get("accessToken")
    full_name = result.get("fullName")
    company = result.get("companyCode")
    return LoginResult(
        access_token=token if isinstance(token, str) else "",
        full_name=full_name.strip() if isinstance(full_name, str) else "",
        company_code=company.strip() if isinstance(company, str) else "",
        flag_active=result.get("flagActive") in (1, True),  # ESB mengirim int 1 (uji staging 25 Nov 2026)
    )


# ====================================================================== klien
class EsbClient:
    """Klien satu token pengguna (atau tanpa token, khusus `login`). Thread-safe."""

    def __init__(
        self,
        base_url: str,
        token: str | None,
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
        on_unauthorized: Callable[[], None] | None = None,
    ) -> None:
        if base_url not in ESB_BASE_URLS:
            raise EsbForbiddenCall("base URL ESB tidak diizinkan")
        if mfg_mode not in MFG_MODES:
            raise EsbForbiddenCall("mode produksi tidak dikenal")
        self._base = base_url
        self._token = token or None
        self._transport = transport
        self._bucket = bucket
        self.mfg_mode = mfg_mode
        self.allow_write = allow_write
        self._timeout = timeout
        self._max_calls = max_calls
        self._max_retries = max(0, min(3, max_retries))
        self._sleep = sleep
        self._clock = clock
        self._on_unauthorized = on_unauthorized
        self._calls = 0
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"EsbClient(base={self._base!r}, token=***, calls={self._calls})"

    @property
    def calls(self) -> int:
        return self._calls

    # ------------------------------------------------------------------ inti
    def _send(
        self,
        method: str,
        path: str,
        query: str,
        body: bytes | None,
        token: str | None,
        timeout: float | None = None,
    ) -> HttpResponse:
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
            response = self._transport.send(method, url, headers, body, timeout or self._timeout)
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
        """Hanya untuk GET (idempoten)."""
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

    # ------------------------------------------------------------------ sesi pengguna (§30.17.3)
    def login(self, username: str, password: str) -> LoginResult:
        """`POST /auth/login` SATU kali (tanpa retry). 400/401/403/envelope gagal -> `EsbInvalidCredentials`;
        jaringan/timeout/5xx/respons tak terbaca -> `EsbUnavailable`. Password hanya ada di body request ini."""
        body = json.dumps({"username": username, "password": password}).encode("utf-8")
        try:
            response = self._send("POST", LOGIN_PATH, "", body, None, LOGIN_TIMEOUT_SECONDS)
        except (EsbNotSent, EsbSentUnknown, EsbResponseTooLarge) as exc:
            raise EsbUnavailable(f"ESB tidak terjangkau saat login ({type(exc).__name__})") from None
        if response.status in (400, 401, 403):
            raise EsbInvalidCredentials(f"login ESB ditolak (HTTP {response.status})")
        if not 200 <= response.status < 300:
            raise EsbUnavailable(f"login ESB gagal (HTTP {response.status})")
        try:
            result = parse_envelope(response, LOGIN_PATH)
        except EsbRejected:
            raise EsbInvalidCredentials("login ESB ditolak") from None
        except EsbProtocolError:
            raise EsbUnavailable("respons login ESB tidak terbaca") from None
        return parse_login(result)

    def logout(self) -> None:
        """`POST /auth/logout` (body `{}`) dengan token ini, timeout 5 dtk; galat apa pun diabaikan (best-effort)."""
        if not self._token:
            return
        try:
            self._send("POST", LOGOUT_PATH, "", b"{}", self._token, LOGOUT_TIMEOUT_SECONDS)
        except EsbError:
            pass

    def _require_token(self) -> str:
        if not self._token:
            raise EsbTokenRejected("klien ESB tanpa token pengguna")
        return self._token

    def _rejected(self) -> EsbTokenRejected:
        if self._on_unauthorized is not None:
            self._on_unauthorized()
        return EsbTokenRejected("token ESB pengguna ditolak (401)")

    # ------------------------------------------------------------------ baca
    def get_response(self, path: str, params: Mapping[str, Any] | None = None) -> HttpResponse:
        classify_call("GET", path, mfg_mode=self.mfg_mode, allow_write=self.allow_write)
        query = urllib.parse.urlencode([(k, v) for k, v in (params or {}).items() if v is not None])
        response = self._send_retry("GET", path, query, None, self._require_token())
        if response.status == 401:
            raise self._rejected()
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
        response = self._send(method, path, "", body, self._require_token())
        if response.status == 401:
            # Tanpa login ulang (prodplan tidak memegang password): sesi dibuang, baris outbox -> `needs_login`.
            raise self._rejected()
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
