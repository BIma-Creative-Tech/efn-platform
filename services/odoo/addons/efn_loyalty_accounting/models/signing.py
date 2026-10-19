"""Tanda tangan publikasi kebijakan poin Odoo -> BFF (docs/04 §13.1 no. 3, L-61).

Header ``X-EFN-Signature: t=<unix>,v1=<hex HMAC-SHA256(key, "<t>." + body)>``; ``body`` = byte JSON PERSIS yang
dikirim (kanonik: ``sort_keys``, separator ``,``/``:``, ASCII). Replay window BFF 300 detik.

Kunci = isi file ``EFN_LOYALTY_POLICY_SIGNING_KEY_FILE`` (default ``/run/secrets/loyalty_policy_signing_key``),
whitespace di ujung dibuang, dipakai sebagai byte UTF-8 (BFF wajib memperlakukan sama). Minimal 32 byte. Kunci
dibaca setiap kirim (rotasi tanpa restart), tidak pernah disimpan di DB/``ir.config_parameter``, tidak pernah di-log.

Fungsi di modul ini murni (tanpa ORM) agar bisa diuji langsung; ``verify_signature`` = implementasi acuan sisi
penerima (dipakai test, dan sebagai spesifikasi untuk BFF).
"""

import hashlib
import hmac
import json
import logging
import os
import re

_logger = logging.getLogger(__name__)

SIGNING_KEY_FILE_ENV = "EFN_LOYALTY_POLICY_SIGNING_KEY_FILE"
DEFAULT_SIGNING_KEY_FILE = "/run/secrets/loyalty_policy_signing_key"
SIGNATURE_HEADER = "X-EFN-Signature"
MIN_KEY_BYTES = 32
REPLAY_WINDOW_SECONDS = 300
_HEADER_RE = re.compile(r"^t=(\d{1,12}),v1=([0-9a-f]{64})$")


class SigningKeyMissing(Exception):
    """Kunci tidak ada / tidak terbaca / terlalu pendek. ``code`` aman untuk log (tanpa isi kunci)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def canonical_json(payload):
    """Byte JSON kanonik (dikirim apa adanya dan ditandatangani)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def read_signing_key():
    """Baca kunci dari file secret. Gagal -> :class:`SigningKeyMissing` (pemanggil menahan publikasi + log ERROR)."""
    path = os.environ.get(SIGNING_KEY_FILE_ENV) or DEFAULT_SIGNING_KEY_FILE
    try:
        with open(path, "rb") as handle:
            raw = handle.read().strip()
    except OSError:
        raise SigningKeyMissing("signing_key_missing") from None
    if not raw:
        raise SigningKeyMissing("signing_key_missing")
    if len(raw) < MIN_KEY_BYTES:
        raise SigningKeyMissing("signing_key_too_short")
    return raw


def sign(key, timestamp, body):
    """Nilai header ``t=<unix>,v1=<hex>`` untuk ``body`` (bytes)."""
    if not isinstance(body, bytes | bytearray):
        raise TypeError("body harus bytes")
    message = str(int(timestamp)).encode("ascii") + b"." + bytes(body)
    digest = hmac.new(key, message, hashlib.sha256).hexdigest()
    return f"t={int(timestamp)},v1={digest}"


def verify_signature(key, header, body, now, window=REPLAY_WINDOW_SECONDS):
    """Implementasi acuan penerima: format, jendela replay (|now - t| <= window), HMAC waktu-konstan.

    :returns: ``(True, None)`` atau ``(False, alasan)`` dengan alasan ``malformed`` | ``replay_window`` |
        ``bad_signature``.
    """
    match = _HEADER_RE.match(header or "")
    if not match:
        return False, "malformed"
    timestamp = int(match.group(1))
    if abs(int(now) - timestamp) > window:
        return False, "replay_window"
    expected = sign(key, timestamp, body).split("v1=", 1)[1]
    if not hmac.compare_digest(expected, match.group(2)):
        return False, "bad_signature"
    return True, None
