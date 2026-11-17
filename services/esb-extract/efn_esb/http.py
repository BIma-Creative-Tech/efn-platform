"""Klien HTTP ESB Core (docs/04 §25.3, L-106, L-111).

- Hanya `GET <base>/*` dan `POST <base>/auth/login`; method/path lain -> `MethodNotAllowed` SEBELUM koneksi dibuka.
- TLS: CA publik (certifi), TLS >= 1.2, verifikasi hostname; proxy env/.netrc diabaikan (`trust_env=False` setara:
  `ProxyHandler({})`); redirect TIDAK diikuti (header Authorization tidak boleh bocor ke host lain).
- 401 pada GET -> login ulang sekali lalu ulangi; 401 lagi -> `EsbAuthError`. 429/5xx/kegagalan jaringan -> exponential
  backoff + jitter, maks `max_retries` percobaan ulang, menghormati `Retry-After`.
  4xx lain -> `EsbHttpError` (tanpa retry).
- Token bucket per brand + batas panggilan per run (`CallBudgetExceeded`).
- SEC-187: ukuran body respons dibatasi (`ESB_MAX_RESPONSE_BYTES`, default 32 MiB, rentang 1..128 MiB):
  `Content-Length` di atas batas ditolak sebelum membaca; body dibaca maksimal batas+1 byte lalu ditolak bila
  melebihi (`EsbResponseTooLarge`, tanpa retry). Body respons error (non-2xx, tidak pernah di-parse) dipotong 64 KiB.
- Password, token, header Authorization, body login, dan query string TIDAK pernah masuk log/pesan exception.
"""

import json
import random
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import certifi

LOGIN_PATH = "/auth/login"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES_CAP = 5
USER_AGENT = "efn-esb-extract/0.1"
_PATH_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~%/")
# Segmen path yang menandakan endpoint tulis ESB (L-106: tidak pernah dipanggil, walau lewat GET).
_WRITE_SEGMENTS = frozenset({"push", "import", "authorize", "upload", "restore", "delete", "pull", "bulk-supplier-vat"})
# SEC-187: batas ukuran body respons (memori container 512 MB).
MIB = 1024 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 32 * MIB
MIN_MAX_RESPONSE_BYTES = 1 * MIB
MAX_MAX_RESPONSE_BYTES = 128 * MIB
ERROR_BODY_MAX_BYTES = 64 * 1024
_READ_CHUNK = 64 * 1024


class EsbError(RuntimeError):
    """Kesalahan klien ESB. Pesan tidak memuat kredensial, token, maupun query string."""


class EsbHttpError(EsbError):
    def __init__(self, status: int, path: str) -> None:
        super().__init__(f"ESB HTTP {status} pada {path}")
        self.status = status
        self.path = path


class EsbAuthError(EsbError):
    """Login ditolak / token ditolak setelah login ulang -> brand ditandai `auth_failed`."""


class EsbProtocolError(EsbError):
    """Respons bukan JSON envelope ESB yang diharapkan."""


class EsbResponseTooLarge(EsbProtocolError):
    """SEC-187: body respons melebihi `ESB_MAX_RESPONSE_BYTES`. Tidak di-retry; pesan tanpa URL/query."""


class CallBudgetExceeded(EsbError):
    """`ESB_MAX_CALLS_PER_RUN` tercapai -> run berhenti rapi (`extract_run.status='limit'`)."""


class MethodNotAllowed(EsbError):
    """Guard GET-only (L-106)."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class Transport(Protocol):
    def send(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse: ...


def esb_ssl_context(cafile: str | None = None) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile or certifi.where())
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirect tidak diikuti: 30x dikembalikan sebagai status (lalu `EsbHttpError`)."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def content_length(headers: Any) -> int | None:
    """`Content-Length` sebagai int; tidak ada/tidak valid -> None (batas baca tetap berlaku)."""
    if headers is None:
        return None
    raw = headers.get("Content-Length")
    if raw is None:
        return None
    text = str(raw).strip()
    return int(text) if text.isdigit() else None


def read_limited(stream: Any, headers: Any, max_bytes: int, *, strict: bool = True) -> bytes:
    """Baca body maksimal `max_bytes + 1` byte (SEC-187).

    `strict=True`: `Content-Length` > batas ditolak sebelum membaca; body > batas ditolak (`EsbResponseTooLarge`).
    `strict=False` (body respons error yang tidak di-parse): dipotong ke `max_bytes`, tanpa exception.
    """
    if max_bytes < 1:
        raise ValueError("max_bytes harus >= 1")
    length = content_length(headers)
    if strict and length is not None and length > max_bytes:
        raise EsbResponseTooLarge("respons ESB melebihi batas ukuran (ESB_MAX_RESPONSE_BYTES)")
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
            raise EsbResponseTooLarge("respons ESB melebihi batas ukuran (ESB_MAX_RESPONSE_BYTES)")
        return data[:max_bytes]
    return data


class UrllibTransport:
    """Transport stdlib (tanpa dependency HTTP tambahan). Hanya HTTPS; tanpa proxy env, tanpa redirect."""

    def __init__(
        self, context: ssl.SSLContext | None = None, max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    ) -> None:
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes harus >= 1")
        self._max_bytes = int(max_response_bytes)
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPSHandler(context=context or esb_ssl_context()),
        )

    def send(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse:
        if not url.startswith("https://"):
            raise MethodNotAllowed("transport ESB hanya https://")
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)  # noqa: S310
        try:
            with self._opener.open(request, timeout=timeout) as response:
                data = read_limited(response, response.headers, self._max_bytes)
                return HttpResponse(response.status, data, dict(response.headers.items()))
        except urllib.error.HTTPError as exc:
            try:
                data = read_limited(exc, None, min(self._max_bytes, ERROR_BODY_MAX_BYTES), strict=False)
            except OSError:
                data = b""
            headers_out = dict(exc.headers.items()) if exc.headers is not None else {}
            return HttpResponse(exc.code, data, headers_out)


class TokenBucket:
    """Token bucket thread-safe: `rate` token/detik, kapasitas `capacity` (burst). Clock/sleep diinjeksi untuk test."""

    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate harus > 0")
        self._rate = float(rate)
        self._capacity = float(capacity if capacity is not None else max(1.0, rate))
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


def check_request(method: str, path: str) -> None:
    """Guard L-106 (murni): hanya GET path baca, atau POST tepat ke /auth/login."""
    if not path.startswith("/") or ".." in path or "//" in path or any(ch not in _PATH_ALLOWED for ch in path):
        raise MethodNotAllowed("path ESB tidak valid")
    if method == "POST" and path == LOGIN_PATH:
        return
    if method != "GET":
        raise MethodNotAllowed(f"method {method} ke ESB ditolak (GET-only)")
    if path == LOGIN_PATH:
        raise MethodNotAllowed("login hanya lewat POST")
    segments = {segment.lower() for segment in path.split("/") if segment}
    if segments & _WRITE_SEGMENTS:
        raise MethodNotAllowed("endpoint tulis ESB ditolak")


def backoff_delay(attempt: int, retry_after: str | None, base: float, cap: float, jitter: float) -> float:
    """Exponential backoff (base * 2^attempt, maks `cap`) + jitter; `Retry-After` (detik, maks 60) dihormati."""
    delay = min(cap, base * (2**attempt)) + jitter
    hint = (retry_after or "").strip()
    if hint.replace(".", "", 1).isdigit():
        delay = max(delay, min(60.0, float(hint)))
    return delay


class EsbClient:
    """Klien satu brand (satu user API). Thread-safe untuk dipakai worker paralel brand yang sama."""

    def __init__(
        self,
        base_url: str,
        username: str,
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
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
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
        check_request(method, path)
        with self._lock:
            if self._max_calls is not None and self._calls >= self._max_calls:
                raise CallBudgetExceeded("batas panggilan ESB per run tercapai")
            self._calls += 1
        self._bucket.acquire()
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = self._base + path + (f"?{query}" if query else "")
        return self._transport.send(method, url, headers, body, self._timeout)

    def _send_retry(self, method: str, path: str, query: str, body: bytes | None, token: str | None) -> HttpResponse:
        attempt = 0
        while True:
            try:
                response = self._send(method, path, query, body, token)
            except (CallBudgetExceeded, MethodNotAllowed, EsbResponseTooLarge):
                raise  # tidak di-retry (SEC-187: respons besar akan besar lagi)
            except OSError as exc:
                if attempt >= self._max_retries:
                    # `from None`: pesan asli bisa memuat URL lengkap (query) -> tidak dirantai.
                    raise EsbError(f"ESB tidak terjangkau pada {path} ({type(exc).__name__})") from None
                self._sleep(backoff_delay(attempt, None, self._backoff_base, self._backoff_cap, self._jitter()))
                attempt += 1
                continue
            if response.status in RETRY_STATUSES and attempt < self._max_retries:
                retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
                self._sleep(backoff_delay(attempt, retry_after, self._backoff_base, self._backoff_cap, self._jitter()))
                attempt += 1
                continue
            return response

    @staticmethod
    def _envelope(response: HttpResponse, path: str) -> Any:
        try:
            data = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise EsbProtocolError(f"respons ESB bukan JSON pada {path}") from None
        if not isinstance(data, dict):
            raise EsbProtocolError(f"respons ESB bukan objek pada {path}")
        status = data.get("status")
        if status not in (None, "ok"):
            code = str(data.get("code") or "")[:16]
            raise EsbProtocolError(f"ESB status gagal {code} pada {path}")
        return data.get("result")

    # ------------------------------------------------------------------ publik
    def login(self) -> None:
        body = json.dumps({"username": self._username, "password": self._password}).encode("utf-8")
        response = self._send_retry("POST", LOGIN_PATH, "", body, None)
        if response.status in (400, 401, 403):
            raise EsbAuthError(f"login ESB ditolak (HTTP {response.status})")
        if not 200 <= response.status < 300:
            raise EsbHttpError(response.status, LOGIN_PATH)
        result = self._envelope(response, LOGIN_PATH)
        token = None
        if isinstance(result, dict):
            token = result.get("accessToken") or result.get("token") or result.get("access_token")
        if not isinstance(token, str) or not token:
            raise EsbAuthError("respons login ESB tanpa token")
        with self._lock:
            self._token = token

    def _current_token(self) -> str:
        with self._login_lock:
            if self._token is None:
                self.login()
            token = self._token
        if token is None:  # pragma: no cover - login() selalu mengisi atau melempar
            raise EsbAuthError("token ESB kosong")
        return token

    def _invalidate(self, token: str) -> None:
        with self._lock:
            if self._token == token:
                self._token = None

    def request(self, method: str, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """Pintu tunggal permintaan data: method selain GET ditolak sebelum koneksi dibuka."""
        method = method.upper()
        check_request(method, path)
        if method != "GET":  # POST hanya untuk login (lewat `login()`), bukan lewat request().
            raise MethodNotAllowed(f"method {method} ke ESB ditolak (GET-only)")
        return self.get(path, params)

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """GET `<base><path>?<params>` -> `result` envelope ESB."""
        check_request("GET", path)
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
            if not 200 <= response.status < 300:
                raise EsbHttpError(response.status, path)
            return self._envelope(response, path)
