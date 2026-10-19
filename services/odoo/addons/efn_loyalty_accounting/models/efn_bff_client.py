"""Perluasan klien Odoo -> BFF untuk E-44 (docs/04 §13.1 no. 3 & 6, L-61).

Memakai SELURUH aturan transport klien ``efn_core`` (``_get_base_url`` https + CA internal ``_get_verify``, service key
dari file secret ``_read_api_key``, ``_efn_check_path``, timeout, tanpa redirect, tanpa log payload) -- hanya dua hal
yang tidak tersedia di ``_efn_post``/``_efn_get``:

1. ``PUT`` dengan **byte body persis** + header tambahan ``X-EFN-Signature`` (HMAC dihitung atas byte yang sama yang
   dikirim; ``requests.post(json=...)`` menserialisasi ulang sehingga tidak bisa dipakai).
2. ``GET /v1/pos/loyalty/accruals?company_code=&date=YYYY-MM-DD``: penjaga R2 ``_efn_get`` menolak nilai yang tampak
   seperti nomor HP (``^\\+?[\\d\\s\\-().]{8,}$``) sehingga tanggal ISO ``2026-10-18`` ikut ditolak (positif palsu).
   Di sini kunci query dibatasi allowlist ``company_code`` + ``date`` dengan regex ketat (kode PT kontrak & tanggal
   ISO), jadi tidak ada data pribadi yang bisa lewat URL.

SEC-58: tidak ada metode publik (AbstractModel ``efn.*``; gerbang ``efn_core/tests/test_rpc_surface.py``).
"""

import logging
import re
import time

import requests

from odoo import api, models

from odoo.addons.efn_core.models.efn_bff_client import SERVICE_KEY_HEADER, BffResponse, EfnBffError

from .signing import SIGNATURE_HEADER

_logger = logging.getLogger(__name__)

_QUERY_RULES = {
    "company_code": re.compile(r"^[A-Z]{2,8}$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
}
_EXTRA_HEADERS = frozenset({SIGNATURE_HEADER})


class EfnBffClient(models.AbstractModel):
    _inherit = "efn.bff.client"

    @api.model
    def _efn_loyalty_check_params(self, params):
        if params is None:
            return None
        if not isinstance(params, dict):
            raise ValueError("params BFF harus dict")
        for key, value in params.items():
            rule = _QUERY_RULES.get(key)
            if rule is None or not isinstance(value, str) or not rule.match(value):
                raise ValueError("params akrual poin tidak valid")
        return params

    @api.model
    def _efn_loyalty_request(self, method, path, body=None, params=None, extra_headers=None, log_context=None):
        """PUT (body bytes) atau GET (params allowlist) ke BFF.

        :param bytes body: byte JSON yang dikirim apa adanya (PUT saja). Tidak pernah di-log.
        :param dict extra_headers: hanya ``X-EFN-Signature``; service key tidak bisa ditimpa.
        :returns: :class:`BffResponse` untuk 2xx/4xx.
        :raises EfnBffError: konfigurasi salah, TLS, timeout, jaringan, 3xx, 5xx.
        """
        if method not in ("PUT", "GET"):
            raise ValueError("metode HTTP tidak didukung")
        if method == "PUT" and not isinstance(body, bytes | bytearray):
            raise ValueError("body PUT harus bytes")
        if method == "GET" and body is not None:
            raise ValueError("GET tanpa body")
        extra_headers = dict(extra_headers or {})
        if set(extra_headers) - _EXTRA_HEADERS:
            raise ValueError("header tambahan tidak diizinkan")
        params = self._efn_loyalty_check_params(params)
        self._efn_check_path(path)
        base_url = self._get_base_url()
        verify = self._get_verify(base_url)
        url = base_url + path
        headers = {"Accept": "application/json", **extra_headers, SERVICE_KEY_HEADER: self._read_api_key()}
        timeout = self._get_timeout()
        common = {"headers": headers, "timeout": timeout, "allow_redirects": False, "verify": verify}
        ctx = " ".join(f"{k}={v}" for k, v in (log_context or {}).items())
        started = time.monotonic()
        try:
            if method == "PUT":
                headers["Content-Type"] = "application/json"
                response = requests.put(url, data=bytes(body), **common)  # noqa: S113 - timeout di common
            else:
                response = requests.get(url, params=params or None, **common)  # noqa: S113 - timeout di common
        except requests.exceptions.SSLError:
            _logger.error("EFN BFF %s %s gagal verifikasi TLS %s", method, path, ctx)
            raise EfnBffError("bff_tls_error") from None
        except requests.exceptions.Timeout:
            _logger.warning("EFN BFF %s %s timeout setelah %.1fs %s", method, path, timeout, ctx)
            raise EfnBffError("bff_timeout") from None
        except requests.exceptions.RequestException as exc:
            _logger.warning("EFN BFF %s %s gagal (%s) %s", method, path, type(exc).__name__, ctx)
            raise EfnBffError("bff_unreachable") from None
        elapsed_ms = int((time.monotonic() - started) * 1000)
        status = response.status_code
        if status >= 500 or 300 <= status < 400:
            _logger.warning("EFN BFF %s %s -> %s (%d ms) %s", method, path, status, elapsed_ms, ctx)
            raise EfnBffError("bff_server_error")
        try:
            data = response.json()
        except ValueError:
            data = None
        _logger.info("EFN BFF %s %s -> %s (%d ms) %s", method, path, status, elapsed_ms, ctx)
        return BffResponse(status, data)
