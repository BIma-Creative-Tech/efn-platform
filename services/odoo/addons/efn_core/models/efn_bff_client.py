"""Klien HTTP server-to-server Odoo -> EFN BFF (NestJS).

Kontrak: docs/04_SERVICE_CONTRACTS.md §2 (header `X-EFN-Service-Key`, secret file
`odoo_bff_api_key` via env `EFN_BFF_API_KEY_FILE`) dan §6 (config parameter).

Aturan keamanan (CLAUDE.md, docs/04 §6-§7 v2):
- **TLS wajib**: base URL harus `https://` dan sertifikat BFF diverifikasi terhadap CA
  internal di env `EFN_CA_FILE` (default `/run/secrets/efn_ca_crt`) lewat
  ``requests.post/get(..., verify=<CA>)`` (`post` dan `get` berbagi `_efn_request`).
  CA tidak terbaca -> `bff_ca_missing` (tidak ada fallback ke verifikasi CA sistem atau
  `verify=False`). `http://` hanya diterima bila `EFN_ALLOW_PLAINTEXT=true` DAN `APP_ENV`
  eksplisit non-produksi.
- URL dari env `EFN_BFF_URL` (dikendalikan deployment) atau `ir.config_parameter`
  `efn.bff_url`. Verifikasi CA internal mencegah admin yang mengubah URL ke host luar
  menerima service key (host luar tidak punya sertifikat dari CA internal).
- API key TIDAK pernah disimpan di ir.config_parameter, tidak pernah di-log.
- Payload (nomor HP, kode OTP, discount_token) TIDAK pernah di-log; hanya path, status,
  latensi, dan konteks yang sudah di-mask (lihat `mask_phone`).
- Redirect tidak diikuti agar header service key tidak bocor ke host lain.
- R2 (docs/04 §8.13): nomor HP, token, dan identitas tidak pernah dikirim lewat URL (query string
  atau segmen path) karena URL tercatat di access log; `get()` menolak kunci query terlarang
  (`PII_QUERY_KEYS`) dan nilai/segmen yang tampak seperti nomor HP. Data tsb lewat body `post()`
  (mis. `POST /v1/pos/loyalty/balance {member_ref}`).
"""

import logging
import os
import re
import time

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)

DEFAULT_API_KEY_FILE = "/run/secrets/odoo_bff_api_key"
API_KEY_FILE_ENV = "EFN_BFF_API_KEY_FILE"
CA_FILE_ENV = "EFN_CA_FILE"
DEFAULT_CA_FILE = "/run/secrets/efn_ca_crt"
BFF_URL_ENV = "EFN_BFF_URL"
ALLOW_PLAINTEXT_ENV = "EFN_ALLOW_PLAINTEXT"
APP_ENV_ENV = "APP_ENV"
NON_PRODUCTION_ENVS = frozenset({"development", "dev", "local", "test", "ci"})
SERVICE_KEY_HEADER = "X-EFN-Service-Key"
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 30.0

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_MEMBER_REF_RE = re.compile(r"^62\d{8,13}$")
# R2: kunci query yang dilarang + nilai yang tampak seperti nomor HP (8+ digit, boleh +/spasi/-).
PII_QUERY_KEYS = frozenset(
    {
        "member_ref",
        "member_hash",
        "phone",
        "mobile",
        "msisdn",
        "email",
        "token",
        "discount_token",
        "otp",
        "code",
        "challenge_id",
        "nik",
        "npwp",
    }
)
_PHONE_LIKE_RE = re.compile(r"^\+?[\d\s\-().]{8,}$")


def plaintext_allowed():
    """True hanya bila `EFN_ALLOW_PLAINTEXT=true` dan `APP_ENV` eksplisit non-produksi.

    Fail-closed: APP_ENV kosong/tidak dikenal diperlakukan sebagai produksi (docs/04 §7.1).
    """
    flag = (os.environ.get(ALLOW_PLAINTEXT_ENV) or "").strip().lower() in _TRUE_VALUES
    app_env = (os.environ.get(APP_ENV_ENV) or "").strip().lower()
    return flag and app_env in NON_PRODUCTION_ENVS


class EfnBffError(Exception):
    """BFF tidak dapat dipakai: belum dikonfigurasi, timeout, tidak terjangkau, atau 5xx.

    `code` berbentuk snake_case dan aman untuk ditampilkan/di-log (tanpa data pribadi).
    """

    def __init__(self, code, message=None):
        super().__init__(message or code)
        self.code = code


class BffResponse:
    """Respons BFF yang sudah di-parse.

    - `data` selalu dict (kosong bila body bukan JSON object) — dipakai endpoint objek.
    - `body` = JSON mentah hasil parse (dict, list, atau None) — dipakai endpoint yang
      mengembalikan array (mis. `GET /v1/pos/privacy/erasures`, docs/04 §8.4).
    """

    __slots__ = ("status", "data", "body")

    def __init__(self, status, data=None):
        self.status = status
        self.body = data
        self.data = data if isinstance(data, dict) else {}

    def __repr__(self):  # jangan pernah mencetak isi data (bisa berisi token)
        return f"<BffResponse status={self.status}>"


def mask_phone(value):
    """Mask nomor HP untuk log/tampilan: `6281234567890` -> `62812****890` (UU PDP)."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) < 8:
        return "****"
    return f"{digits[:5]}****{digits[-3:]}"


def parse_member_ref(value):
    """Normalisasi nomor HP ke `member_ref` kontrak (`^62[0-9]{8,13}$`, docs/04 §1 & §3.1 v2).

    :returns: tuple ``(member_ref, None)`` bila valid, atau ``(None, error_code)`` dengan
        ``error_code`` ∈ ``no_phone`` (kosong), ``foreign_phone`` (kode negara selain 62),
        ``invalid_phone`` (format salah).

    Menerima `0812-3456-7890`, `+62 812 3456 7890`, `0062812...`, `6281234567890`, `812345678`.
    """
    if not isinstance(value, str) or not value.strip():
        return None, "no_phone"
    digits = re.sub(r"[\s\-().]", "", value.strip())
    international = False
    if digits.startswith("+"):
        digits, international = digits[1:], True
    elif digits.startswith("00"):
        digits, international = digits[2:], True
    if not digits.isdigit():
        return None, "invalid_phone"
    if international:
        if not digits.startswith("62"):
            return None, "foreign_phone"
    elif digits.startswith("0"):
        digits = "62" + digits[1:]
    elif digits.startswith("8"):
        digits = "62" + digits
    elif not digits.startswith("62"):
        return None, "invalid_phone"
    if not _MEMBER_REF_RE.match(digits):
        return None, "invalid_phone"
    return digits, None


def normalize_member_ref(value):
    """Seperti :func:`parse_member_ref` tetapi hanya mengembalikan member_ref atau None."""
    return parse_member_ref(value)[0]


def partner_member_ref(partner):
    """Ambil `member_ref` dari nomor HP partner (data master server, bukan input kasir).

    Odoo 19 `res.partner` hanya punya `phone` (field `mobile` dihapus di base); `mobile`
    dipakai juga bila ada modul yang menambahkannya. Nomor Indonesia valid pertama menang.

    :returns: ``(member_ref, None)`` atau ``(None, error_code)`` dengan error_code
        ``partner_missing`` | ``no_phone`` | ``foreign_phone`` | ``invalid_phone``.
    """
    if not partner:
        return None, "partner_missing"
    candidates = [partner.phone]
    if "mobile" in partner._fields:
        candidates.append(partner.mobile)
    first_error = None
    for raw in candidates:
        if not raw:
            continue
        member, error = parse_member_ref(raw)
        if member:
            return member, None
        first_error = first_error or error
    return None, first_error or "no_phone"


class EfnBffClient(models.AbstractModel):
    _name = "efn.bff.client"
    _description = "EFN BFF HTTP Client"

    # ------------------------------------------------------------------ config
    @api.model
    def _efn_get_param(self, key, default=None):
        return self.env["ir.config_parameter"].sudo().get_param(key, default)

    @api.model
    def _efn_get_bool_param(self, key, default=False):
        raw = self._efn_get_param(key)
        if raw is None or raw is False or str(raw).strip() == "":
            return default
        return str(raw).strip().lower() in _TRUE_VALUES

    @api.model
    def _get_base_url(self):
        raw = os.environ.get(BFF_URL_ENV) or self._efn_get_param("efn.bff_url") or ""
        url = raw.strip().rstrip("/")
        lowered = url.lower()
        if lowered.startswith("https://"):
            return url
        if lowered.startswith("http://"):
            if plaintext_allowed():
                _logger.warning("EFN BFF: memakai HTTP plaintext (EFN_ALLOW_PLAINTEXT, non-produksi)")
                return url
            _logger.error(
                "EFN BFF: URL http:// ditolak (wajib https; plaintext hanya dengan %s=true di %s non-produksi)",
                ALLOW_PLAINTEXT_ENV,
                APP_ENV_ENV,
            )
            raise EfnBffError("bff_insecure_url")
        _logger.error("EFN BFF: %s / efn.bff_url kosong atau bukan https", BFF_URL_ENV)
        raise EfnBffError("bff_not_configured")

    @api.model
    def _get_verify(self, url):
        """Nilai `verify` untuk requests: path CA internal (https) — tidak pernah False."""
        if not url.lower().startswith("https://"):
            return True  # plaintext dev-only; verify tidak relevan
        path = os.environ.get(CA_FILE_ENV) or DEFAULT_CA_FILE
        if not os.path.isfile(path) or not os.access(path, os.R_OK):
            _logger.error("EFN BFF: CA internal tidak terbaca (cek env %s)", CA_FILE_ENV)
            raise EfnBffError("bff_ca_missing")
        return path

    @api.model
    def _get_timeout(self):
        raw = self._efn_get_param("efn.pos.otp_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        try:
            timeout = float(raw)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_SECONDS
        if timeout <= 0:
            timeout = DEFAULT_TIMEOUT_SECONDS
        return min(timeout, MAX_TIMEOUT_SECONDS)

    @api.model
    def _read_api_key(self):
        """Baca service key dari file secret. Dibaca setiap panggilan agar rotasi secret
        tidak memerlukan restart (file kecil, overhead diabaikan)."""
        path = os.environ.get(API_KEY_FILE_ENV) or DEFAULT_API_KEY_FILE
        try:
            with open(path, encoding="utf-8") as handle:
                key = handle.read().strip()
        except OSError:
            # Jangan log path lengkap isi; path sendiri bukan rahasia tapi cukup nama env.
            _logger.error("EFN BFF: file service key tidak dapat dibaca (cek env %s)", API_KEY_FILE_ENV)
            raise EfnBffError("bff_key_missing") from None
        if not key:
            _logger.error("EFN BFF: file service key kosong (cek env %s)", API_KEY_FILE_ENV)
            raise EfnBffError("bff_key_missing")
        return key

    # -------------------------------------------------------------------- HTTP
    @api.model
    def post(self, path, payload, log_context=None):
        """POST JSON ke BFF.

        :param str path: path absolut kontrak, mis. ``/v1/pos/otp/request``.
        :param dict payload: body JSON. Tidak pernah di-log.
        :param dict log_context: konteks AMAN untuk log (sudah di-mask), mis.
            ``{"member": "62812****890", "order_ref": "..."}``.
        :returns: :class:`BffResponse` untuk status 2xx/4xx.
        :raises EfnBffError: konfigurasi salah, timeout, koneksi gagal, atau 5xx.
        """
        return self._efn_request("POST", path, payload=payload, log_context=log_context)

    @api.model
    def get(self, path, params=None, log_context=None):
        """GET ke BFF dengan aturan yang sama dengan :meth:`post` (docs/04 §8.8).

        HTTPS + CA internal, service key dari file secret, timeout, tanpa redirect, tanpa log
        payload/parameter.

        :param str path: path absolut kontrak, mis. ``/v1/pos/payments/<id>``. Segmen id
            wajib sudah divalidasi pemanggil (path dengan ``?``/``#``/``..`` ditolak).
        :param dict params: query string (nilai str/int/float/bool). Tidak pernah di-log.
        :returns: :class:`BffResponse` (``body`` bisa berupa list untuk endpoint array).
        :raises EfnBffError: seperti :meth:`post`.
        """
        if params is not None:
            if not isinstance(params, dict):
                raise ValueError("params BFF harus dict")
            for key, value in params.items():
                if not isinstance(key, str) or not isinstance(value, (str, int, float, bool)):
                    raise ValueError("params BFF hanya boleh berisi nilai skalar")
                # R2 (docs/04 §8.13): nomor HP, token, identitas TIDAK boleh lewat URL/query
                # (tercatat di access log). Data seperti itu wajib dikirim lewat body POST.
                text = value.strip() if isinstance(value, str) else (str(value) if type(value) is int else "")
                if key.lower() in PII_QUERY_KEYS or _PHONE_LIKE_RE.match(text):
                    raise ValueError("params BFF tidak boleh memuat data pribadi/token; pakai POST body")
        return self._efn_request("GET", path, params=params, log_context=log_context)

    @api.model
    def _efn_check_path(self, path):
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("path BFF harus diawali '/'")
        # Cegah path traversal / injeksi query lewat segmen id yang tidak divalidasi.
        if any(token in path for token in ("?", "#", "..", "//", "\\")) or any(ord(char) < 0x21 for char in path):
            raise ValueError("path BFF tidak valid")
        # R2: segmen path yang tampak seperti nomor HP (mis. /balance/6281...) juga dilarang.
        if any(_PHONE_LIKE_RE.match(segment) for segment in path.split("/") if segment):
            raise ValueError("path BFF tidak boleh memuat data pribadi")

    @api.model
    def _efn_request(self, method, path, payload=None, params=None, log_context=None):
        self._efn_check_path(path)
        base_url = self._get_base_url()
        verify = self._get_verify(base_url)
        url = base_url + path
        headers = {
            "Accept": "application/json",
            SERVICE_KEY_HEADER: self._read_api_key(),
        }
        timeout = self._get_timeout()
        common = {
            "headers": headers,
            "timeout": timeout,
            "allow_redirects": False,
            "verify": verify,
        }
        ctx = " ".join(f"{k}={v}" for k, v in (log_context or {}).items())
        started = time.monotonic()
        try:
            # Panggil requests.post/requests.get langsung (bukan requests.request) agar
            # titik mock di test tetap stabil.
            if method == "POST":
                headers["Content-Type"] = "application/json"
                response = requests.post(url, json=payload, **common)  # noqa: S113 - timeout ada di common
            elif method == "GET":
                response = requests.get(url, params=params or None, **common)  # noqa: S113 - timeout ada di common
            else:
                raise ValueError("metode HTTP tidak didukung")
        except requests.exceptions.SSLError:
            # Sertifikat BFF tidak cocok dengan CA internal / hostname: JANGAN retry tanpa verify.
            _logger.error("EFN BFF %s %s gagal verifikasi TLS %s", method, path, ctx)
            raise EfnBffError("bff_tls_error") from None
        except requests.exceptions.Timeout:
            _logger.warning("EFN BFF %s %s timeout setelah %.1fs %s", method, path, timeout, ctx)
            raise EfnBffError("bff_timeout") from None
        except requests.exceptions.RequestException as exc:
            # Nama kelas exception saja; pesan bisa memuat URL internal.
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
