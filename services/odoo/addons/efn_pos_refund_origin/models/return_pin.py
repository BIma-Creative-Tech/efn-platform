"""E-41 (docs/08 §11.6, docs/04 §12.4, L-51): utilitas PIN persetujuan retur tanpa struk (bukan model, tanpa RPC).

PIN Store Manager di-hash pbkdf2-sha512 (passlib, pola hash password ``res.users`` Odoo) dan hanya disimpan di
server (``res.users.efn_return_pin_hash``, grup ``base.group_system``). Hash TIDAK pernah dimuat ke data POS di
browser — beda dengan PIN ``pos_hr`` (hash dikirim ke klien, SEC-63). Teks PIN tidak pernah di-log.
"""

import re

from passlib.context import CryptContext

PIN_RE = re.compile(r"^\d{6,8}$")
PIN_MAX_INPUT = 32  # input RPC lebih panjang dari ini ditolak tanpa verifikasi (bukan PIN)
DEFAULT_PIN_KDF_ROUNDS = 600_000  # = MIN_ROUNDS hash password Odoo 19
MIN_PIN_KDF_ROUNDS = 1_000  # hanya untuk test; produksi memakai default (param sistem, base.group_system)
PIN_KDF_ROUNDS_PARAM = "efn.pos.return_pin_kdf_rounds"

_SEQUENCES = ("01234567890123456789", "98765432109876543210")


def pin_crypt_context(rounds):
    rounds = max(int(rounds), MIN_PIN_KDF_ROUNDS)
    return CryptContext(schemes=["pbkdf2_sha512"], pbkdf2_sha512__rounds=rounds)


def pin_kdf_rounds(env):
    raw = env["ir.config_parameter"].sudo().get_param(PIN_KDF_ROUNDS_PARAM, DEFAULT_PIN_KDF_ROUNDS)
    try:
        return max(int(raw), MIN_PIN_KDF_ROUNDS)
    except (TypeError, ValueError):
        return DEFAULT_PIN_KDF_ROUNDS


def pin_is_weak(pin):
    """PIN 6–8 digit yang mudah ditebak: semua digit sama atau deret naik/turun."""
    return len(set(pin)) == 1 or any(pin in seq for seq in _SEQUENCES)


def hash_pin(env, pin):
    return pin_crypt_context(pin_kdf_rounds(env)).hash(pin)


def verify_pin(pin, pin_hash):
    """Verifikasi waktu-konstan (passlib). Hash kosong/rusak -> False (tidak pernah exception ke pemanggil)."""
    if not pin_hash or not isinstance(pin, str):
        return False
    try:
        # Context verifikasi menerima putaran berapa pun (putaran tersimpan di hash).
        return pin_crypt_context(MIN_PIN_KDF_ROUNDS).verify(pin, pin_hash)
    except (ValueError, TypeError):
        return False
