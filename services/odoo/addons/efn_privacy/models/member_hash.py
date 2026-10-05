"""Rumus `member_hash` (docs/04_SERVICE_CONTRACTS.md §1) — HARUS identik dengan BFF & ETL.

    member_hash = hex(sha256(pepper_hex + member_ref))

- `member_ref` = nomor HP ternormalisasi `^62[0-9]{8,13}$` (lihat
  `efn_core.models.efn_bff_client.parse_member_ref`).
- `pepper_hex` = hex(HKDF-SHA256(ikm=otp_pepper, salt "", info "efn:member_hash:v1", 32 byte)),
  64 karakter hex huruf kecil. Odoo **tidak** menerima `otp_pepper`; deploy menurunkan
  `pepper_hex` dan memasangnya sebagai secret `member_hash_pepper` (docs/04 §8.4, §8.6),
  dibaca dari file yang ditunjuk env `EFN_MEMBER_HASH_PEPPER_FILE`
  (default `/run/secrets/member_hash_pepper`).

Pepper tidak pernah di-log, tidak disimpan di DB / ir.config_parameter. Cache per proses
dikunci (path, mtime, size) sehingga rotasi file terbaca tanpa restart.

Test vector bersama (services/api-gateway/src/config/app-config.spec.ts, otp_pepper
`test-otp-pepper-v1`): pepper_hex `46df3d2f…061f`, member_ref `6281234567890` ->
`8930b421…6653`.
"""

import hashlib
import os
import re
import threading

PEPPER_FILE_ENV = "EFN_MEMBER_HASH_PEPPER_FILE"
DEFAULT_PEPPER_FILE = "/run/secrets/member_hash_pepper"

_PEPPER_RE = re.compile(r"^[0-9a-f]{64}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_MEMBER_REF_RE = re.compile(r"^62\d{8,13}$")

_lock = threading.Lock()
_cache = {}


class PepperUnavailable(Exception):
    """Pepper tidak terbaca / formatnya salah. `code` aman untuk log (tanpa isi secret)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def read_pepper():
    """Kembalikan `pepper_hex` (64 hex lower) dari file secret.

    :raises PepperUnavailable: ``pepper_missing`` (file tidak ada/tidak terbaca) atau
        ``pepper_invalid`` (bukan 64 karakter hex).
    """
    path = os.environ.get(PEPPER_FILE_ENV) or DEFAULT_PEPPER_FILE
    try:
        stat = os.stat(path)
    except OSError:
        raise PepperUnavailable("pepper_missing") from None
    key = (path, stat.st_mtime_ns, stat.st_size)
    with _lock:
        cached = _cache.get(key)
    if cached:
        return cached
    try:
        with open(path, encoding="ascii") as handle:
            value = handle.read().strip().lower()
    except (OSError, UnicodeDecodeError):
        raise PepperUnavailable("pepper_missing") from None
    if not _PEPPER_RE.match(value):
        raise PepperUnavailable("pepper_invalid")
    with _lock:
        _cache.clear()
        _cache[key] = value
    return value


def member_hash(pepper_hex, member_ref):
    """`hex(sha256(pepper_hex + member_ref))` — konkatenasi string ASCII, sama dengan BFF."""
    if not _PEPPER_RE.match(pepper_hex or ""):
        raise ValueError("pepper_hex tidak valid")
    if not _MEMBER_REF_RE.match(member_ref or ""):
        raise ValueError("member_ref tidak valid")
    return hashlib.sha256((pepper_hex + member_ref).encode("ascii")).hexdigest()
