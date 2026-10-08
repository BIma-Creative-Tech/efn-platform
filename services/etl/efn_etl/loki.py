"""Pembaca Loki `query_range` untuk rekonsiliasi audit (Gelombang 14, SEC-51/52). Hanya stdlib.

Transport: `LOKI_URL` (`https://loki:3100` = sidecar ghostunnel `loki-tls`) dengan mTLS — sertifikat klien CN `etl`
(`LOKI_CLIENT_CERT_FILE`/`LOKI_CLIENT_KEY_FILE`, allowlist CN di loki-tls) dan verifikasi server dengan CA internal
(`LOKI_CA_FILE`), hostname diverifikasi, TLS >= 1.2. Tidak ada fallback plaintext (http:// hanya dengan
`EFN_ALLOW_PLAINTEXT` di dev, divalidasi `Settings.from_env`).

Paginasi (logika murni `next_page_start`, diuji): `direction=forward`, `limit` entri per halaman; halaman berikut mulai
dari timestamp TERAKHIR (inklusif) dan entri yang sudah terlihat pada timestamp itu dilewati — entri dengan timestamp
sama di batas halaman tidak hilang. Halaman yang tidak menambah entri baru (> `limit` entri pada satu nanodetik) atau
batas halaman tercapai -> `truncated=True` (dilaporkan sebagai anomali, bukan diam-diam).
"""

import http.client
import json
import ssl
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from efn_etl.watermark import to_utc

QUERY_RANGE_PATH = "/loki/api/v1/query_range"
DEFAULT_PAGE_LIMIT = 5000
DEFAULT_MAX_PAGES = 200
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class LokiError(RuntimeError):
    """Loki tidak terjangkau / respons tidak valid. Pesan tanpa isi baris log."""


def to_ns(value: datetime) -> int:
    utc = to_utc(value)
    return int(utc.timestamp()) * 1_000_000_000 + utc.microsecond * 1000


@dataclass(frozen=True)
class LokiEntry:
    ts_ns: int
    line: str
    labels: tuple[tuple[str, str], ...] = ()


@dataclass
class LokiResult:
    entries: list[LokiEntry]
    pages: int = 0
    truncated: bool = False


def parse_query_range(payload: Any) -> list[LokiEntry]:
    """Ambil entri dari respons `query_range` (resultType streams). Validasi bentuk (fail closed)."""
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise LokiError("respons Loki bukan status success")
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("resultType") != "streams":
        raise LokiError("respons Loki bukan resultType streams")
    out: list[LokiEntry] = []
    for stream in data.get("result") or []:
        if not isinstance(stream, dict):
            raise LokiError("stream Loki tidak valid")
        raw_labels = stream.get("stream") or {}
        labels = tuple(sorted((str(k), str(v)) for k, v in raw_labels.items())) if isinstance(raw_labels, dict) else ()
        for value in stream.get("values") or []:
            if not (isinstance(value, list) and len(value) >= 2 and isinstance(value[1], str)):
                raise LokiError("entri Loki tidak valid")
            try:
                ts = int(value[0])
            except (TypeError, ValueError) as exc:
                raise LokiError("timestamp entri Loki tidak valid") from exc
            out.append(LokiEntry(ts, value[1], labels))
    out.sort(key=lambda e: (e.ts_ns, e.labels, e.line))
    return out


def next_page_start(
    entries: list[LokiEntry], seen_at_boundary: set[tuple[int, tuple[tuple[str, str], ...], str]]
) -> tuple[int, set[tuple[int, tuple[tuple[str, str], ...], str]], list[LokiEntry]]:
    """(start berikut, kunci entri pada timestamp batas, entri baru halaman ini)."""
    fresh = [e for e in entries if (e.ts_ns, e.labels, e.line) not in seen_at_boundary]
    last = entries[-1].ts_ns
    boundary_keys = {(e.ts_ns, e.labels, e.line) for e in entries if e.ts_ns == last}
    if seen_at_boundary and next(iter(seen_at_boundary))[0] == last:
        boundary_keys |= seen_at_boundary
    return last, boundary_keys, fresh


class LokiTransport(Protocol):
    def get_json(self, path: str, params: dict[str, str]) -> Any: ...


def query_range(
    transport: LokiTransport,
    query: str,
    start: datetime,
    end: datetime,
    page_limit: int = DEFAULT_PAGE_LIMIT,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> LokiResult:
    """Semua entri `query` di [start, end) (urut waktu). `truncated` bila tidak semua entri dapat diambil."""
    result = LokiResult(entries=[])
    start_ns, end_ns = to_ns(start), to_ns(end)
    seen: set[tuple[int, tuple[tuple[str, str], ...], str]] = set()
    while start_ns < end_ns:
        if result.pages >= max_pages:
            result.truncated = True
            break
        params = {
            "query": query,
            "start": str(start_ns),
            "end": str(end_ns),
            "limit": str(page_limit),
            "direction": "forward",
        }
        entries = parse_query_range(transport.get_json(QUERY_RANGE_PATH, params))
        result.pages += 1
        if not entries:
            break
        start_ns, seen, fresh = next_page_start(entries, seen)
        result.entries.extend(fresh)
        if len(entries) < page_limit:
            break
        if not fresh:
            # > page_limit entri pada nanodetik yang sama: tidak bisa maju tanpa kehilangan data.
            result.truncated = True
            break
    return result


def iter_lines(result: LokiResult) -> Iterator[str]:
    for entry in result.entries:
        yield entry.line


class HttpsLokiTransport:
    """GET JSON ke Loki lewat mTLS. Satu koneksi per request (frekuensi rendah: harian)."""

    def __init__(
        self,
        url: str,
        ca_file: str | None,
        cert_file: str | None,
        key_file: str | None,
        timeout: float = 60.0,
    ) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise ValueError("LOKI_URL tidak valid")
        self._scheme = parts.scheme
        self._host = parts.hostname
        self._port = parts.port or (443 if parts.scheme == "https" else 80)
        self._base = parts.path.rstrip("/")
        self._timeout = timeout
        self._context: ssl.SSLContext | None = None
        if parts.scheme == "https":
            if not ca_file:
                raise ValueError("LOKI_URL https membutuhkan LOKI_CA_FILE")
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca_file)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            if cert_file and key_file:
                context.load_cert_chain(cert_file, key_file)
            self._context = context

    def get_json(self, path: str, params: dict[str, str]) -> Any:
        target = f"{self._base}{path}?{urlencode(params)}"
        if self._context is not None:
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                self._host, self._port, timeout=self._timeout, context=self._context
            )
        else:
            conn = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
        try:
            conn.request("GET", target, headers={"Accept": "application/json"})
            response = conn.getresponse()
            body = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise LokiError(f"Loki tidak terjangkau ({type(exc).__name__})") from exc
        finally:
            conn.close()
        if response.status != 200:
            raise LokiError(f"Loki HTTP {response.status}")
        if len(body) > MAX_RESPONSE_BYTES:
            raise LokiError("respons Loki terlalu besar")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise LokiError("respons Loki bukan JSON") from exc
