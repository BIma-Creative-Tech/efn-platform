"""Key ring AES-256-GCM + blind index HMAC + token nama untuk kolom PII Odoo (E-38a, ADR-036 §0.3-§0.5).

Format (tata letak blob identik dengan BFF `services/api-gateway/src/common/pii/crypto.ts`, docs/04 §7.2)::

    blob  = versi_kunci (1 byte) || IV 12 byte acak || ciphertext || tag 16 byte
    teks  = "efnpii2:" + base64 standar (blob)          # kolom Odoo bertipe varchar
    AAD 2 = "efn:odoo:<tabel>.<kolom>:<id>:v2"          # terikat id record (SEC-138, ADR-036 §0.3)
    AAD 1 = "efn:odoo:<tabel>.<kolom>:v1"               # format lama: hanya di dalam transaksi create

`efnpiiN:` adalah penanda FORMAT, bukan versi kunci (versi kunci = byte pertama blob).
`efnpii1:` ditulis INSERT (id belum diketahui), lalu ditulis ulang ke `efnpii2:` di transaksi yang sama.
`efnpii1:` yang terbaca di luar itu tetap didekripsi (migrasi PoC) dan dicatat `unbound_ciphertext`.

Blind index (ADR-036 §4.4)::

    k_f  = HKDF-SHA256(ikm = odoo_pii_bidx_key, salt = kosong, info = "efn:odoo:bidx:<field>:v1", 32 byte)
    bidx = hex(HMAC-SHA256(k_f, normalize_f(plaintext)))

Token nama (ADR-036 §0.5)::

    k_t  = HKDF-SHA256(ikm = odoo_pii_name_key, salt = kosong, info = "efn:odoo:nametok:<f|w|p>:v1", 32 byte)
    tok  = base64url_tanpa_padding(HMAC-SHA256(k_t, N(teks))[:16])      # 22 karakter

Secret (tidak pernah di DB, ir.config_parameter, log, image, atau repo):

| Env | Default | Isi |
|---|---|---|
| `EFN_ODOO_PII_KEY_DIR` | `/run/secrets` | folder `odoo_pii_key_v<n>`, `odoo_pii_bidx_key`, `odoo_pii_name_key` |
| `EFN_ODOO_PII_KEY_V<n>_FILE` | `<dir>/odoo_pii_key_v<n>` | override path per versi |
| `EFN_ODOO_PII_BIDX_KEY_FILE` | `<dir>/odoo_pii_bidx_key` | override path kunci blind index |
| `EFN_ODOO_PII_NAME_KEY_FILE` | `<dir>/odoo_pii_name_key` | override path kunci token nama |
| `EFN_ODOO_PII_ACTIVE_KEY_VERSION` | `1` | versi kunci untuk enkripsi baru (non-rahasia) |

Isi file kunci = 32 byte acak dalam base64. Pesan error/log hanya berisi KODE
(`key_missing`, `key_invalid`, `version_unknown`, `auth_failed`, `unbound_ciphertext`,
`plaintext_in_protected`, `field_not_encrypted`), tidak pernah isi kunci, plaintext, atau ciphertext.

Modul ini murni Python (tanpa import Odoo) agar bisa diuji tanpa registry dan dipakai
`tools/gen_shared_vectors.py`.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_logger = logging.getLogger(__name__)

PREFIX = "efnpii1:"  # format 1 (AAD tanpa id)
PREFIX_V2 = "efnpii2:"  # format 2 (AAD terikat id record)
PREFIXES = (PREFIX, PREFIX_V2)
PREFIX_LEN = len(PREFIX)
_MARKER_RE = re.compile(r"^efnpii[0-9]+:")
NAME_TOKEN_BYTES = 16
NAME_TOKEN_KINDS = ("f", "w", "p")
IV_BYTES = 12
TAG_BYTES = 16
KEY_BYTES = 32
MIN_BLOB_BYTES = 1 + IV_BYTES + TAG_BYTES
PLACEHOLDER = "•••• (kunci PII tidak tersedia)"

ENV_KEY_DIR = "EFN_ODOO_PII_KEY_DIR"
DEFAULT_KEY_DIR = "/run/secrets"
ENV_ACTIVE_VERSION = "EFN_ODOO_PII_ACTIVE_KEY_VERSION"
ENV_BIDX_FILE = "EFN_ODOO_PII_BIDX_KEY_FILE"
ENV_NAME_FILE = "EFN_ODOO_PII_NAME_KEY_FILE"
ENV_REQUIRED = "EFN_ODOO_PII_REQUIRED"
KEY_FILE_RE = re.compile(r"^odoo_pii_key_v([1-9][0-9]{0,2})$")
KEY_ENV_RE = re.compile(r"^EFN_ODOO_PII_KEY_V([1-9][0-9]{0,2})_FILE$")
BIDX_FILE_NAME = "odoo_pii_bidx_key"
NAME_FILE_NAME = "odoo_pii_name_key"
LOG_TAG = "EFN_PII_KEYRING"

# Jeda minimum antar-pemeriksaan ulang file secret (stat). Rotasi file terbaca tanpa restart
# paling lambat setelah jeda ini; test memanggil reset_keyring_cache().
RECHECK_SECONDS = 10.0

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


class PiiError(Exception):
    """Kesalahan PII. `code` aman untuk log (tanpa isi secret/plaintext)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def aad_for(table, column, record_id=None):
    """AAD kontrak Odoo (ADR-036 §0.3).

    ``record_id`` None -> format 1 `efn:odoo:<tabel>.<kolom>:v1`;
    int > 0 -> format 2 `efn:odoo:<tabel>.<kolom>:<id>:v2`.
    """
    if not _IDENT_RE.match(table or "") or not _IDENT_RE.match(column or ""):
        raise ValueError("nama tabel/kolom PII tidak valid")
    if record_id is None:
        return f"efn:odoo:{table}.{column}:v1"
    if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id <= 0:
        raise ValueError("id record PII tidak valid")
    return f"efn:odoo:{table}.{column}:{record_id}:v2"


def seal(key, version, iv, plaintext, aad):
    """Primitif deterministik (dipakai test vector): blob = versi || iv || ct || tag.

    :param bytes key: 32 byte; :param int version: 1..255; :param bytes iv: 12 byte;
    :param bytes plaintext: data; :param bytes aad: associated data.
    """
    if len(key) != KEY_BYTES:
        raise PiiError("key_invalid")
    if not 1 <= version <= 255 or len(iv) != IV_BYTES:
        raise ValueError("versi/IV tidak valid")
    return bytes([version]) + iv + AESGCM(key).encrypt(iv, plaintext, aad)


def open_blob(keys, blob, aad):
    """Kebalikan :func:`seal`. ``keys`` = dict versi -> kunci 32 byte (atau objek AESGCM).

    :raises PiiError: ``version_unknown`` | ``auth_failed``.
    """
    if len(blob) < MIN_BLOB_BYTES:
        raise PiiError("auth_failed")
    key = keys.get(blob[0])
    if key is None:
        raise PiiError("version_unknown")
    aead = key if isinstance(key, AESGCM) else AESGCM(key)
    try:
        return aead.decrypt(blob[1 : 1 + IV_BYTES], blob[1 + IV_BYTES :], aad)
    except InvalidTag:
        raise PiiError("auth_failed") from None


def wrap(blob, prefix=PREFIX):
    if prefix not in PREFIXES:
        raise ValueError("penanda format tidak dikenal")
    return prefix + base64.b64encode(blob).decode("ascii")


def unwrap(text):
    try:
        return base64.b64decode(text[PREFIX_LEN:], validate=True)
    except (binascii.Error, ValueError):
        raise PiiError("auth_failed") from None


def is_ciphertext(value):
    """True untuk format yang dikenal (`efnpii1:`/`efnpii2:`)."""
    return isinstance(value, str) and value.startswith(PREFIXES)


def looks_like_marker(value):
    """True untuk SEMUA penanda `efnpii<n>:` (termasuk format yang belum dikenal)."""
    return isinstance(value, str) and bool(_MARKER_RE.match(value))


def format_of(text):
    """1 / 2 untuk ciphertext yang dikenal, None selain itu."""
    if not isinstance(text, str):
        return None
    if text.startswith(PREFIX_V2):
        return 2
    if text.startswith(PREFIX):
        return 1
    return None


def b64url_nopad(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def random_name_token():
    """Token padding (ADR-036 §0.5): format sama dengan token asli, tidak pernah cocok kueri."""
    return b64url_nopad(os.urandom(NAME_TOKEN_BYTES))


def derive_name_subkey(name_key, kind):
    if kind not in NAME_TOKEN_KINDS:
        raise ValueError("jenis token nama tidak dikenal")
    return hkdf_sha256(name_key, f"efn:odoo:nametok:{kind}:v1".encode("ascii"))


def name_token_b64(subkey, normalized):
    return b64url_nopad(hmac.new(subkey, normalized.encode("utf-8"), hashlib.sha256).digest()[:NAME_TOKEN_BYTES])


def version_of(text):
    """Versi kunci ciphertext tanpa dekripsi (rotasi/laporan), None bila bukan ciphertext."""
    if not is_ciphertext(text):
        return None
    try:
        blob = unwrap(text)
    except PiiError:
        return None
    return blob[0] if blob else None


def hkdf_sha256(ikm, info, length=32):
    """HKDF-SHA256 (RFC 5869) dengan salt kosong (== HashLen byte nol)."""
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(ikm)


def derive_bidx_subkey(bidx_key, field_name):
    return hkdf_sha256(bidx_key, f"efn:odoo:bidx:{field_name}:v1".encode("ascii"))


def blind_index_hex(subkey, normalized):
    return hmac.new(subkey, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


class KeyRing:
    """Snapshot key ring yang sudah dimuat. Immutable setelah dibuat."""

    def __init__(self, keys, active_version, bidx_key, error_code=None, name_key=None):
        self._aead = {v: AESGCM(k) for v, k in keys.items()}
        self.active_version = active_version if active_version in keys else None
        self._bidx_key = bidx_key
        self._name_key = name_key
        self._subkeys = {}
        self._name_subkeys = {}
        self._sub_lock = threading.Lock()
        self.error_code = error_code

    @property
    def loaded_versions(self):
        return sorted(self._aead)

    @property
    def has_name_key(self):
        return self._name_key is not None

    @property
    def can_encrypt(self):
        """Tulis data terlindungi hanya bila kunci aktif, kunci blind index, DAN kunci token nama ada
        (kalau tidak: mode terkunci, ADR-036 §4.6 + §0.4)."""
        return self.active_version is not None and self._bidx_key is not None and self._name_key is not None

    @property
    def status(self):
        return "ok" if self.can_encrypt else "locked"

    def encrypt_text(self, plaintext, aad, prefix=PREFIX):
        """Enkripsi dengan kunci aktif. Gerbang mode terkunci (`can_encrypt`) diperiksa pemanggil ORM."""
        if self.active_version is None:
            raise PiiError(self.error_code or "key_missing")
        blob = bytes([self.active_version]) + self._encrypt_raw(plaintext, aad)
        return wrap(blob, prefix)

    def name_token(self, kind, normalized):
        """Token nama 128 bit base64url (ADR-036 §0.5). :raises PiiError: kunci token nama tidak ada."""
        if self._name_key is None:
            raise PiiError(self.error_code or "key_missing")
        subkey = self._name_subkeys.get(kind)
        if subkey is None:
            with self._sub_lock:
                subkey = self._name_subkeys.get(kind)
                if subkey is None:
                    subkey = derive_name_subkey(self._name_key, kind)
                    self._name_subkeys[kind] = subkey
        return name_token_b64(subkey, normalized)

    def _encrypt_raw(self, plaintext, aad):
        iv = os.urandom(IV_BYTES)
        return iv + self._aead[self.active_version].encrypt(iv, plaintext.encode("utf-8"), aad.encode("utf-8"))

    def decrypt_text(self, text, aad):
        """:raises PiiError: ``version_unknown`` (kunci versi itu tidak dimuat) | ``auth_failed``."""
        blob = unwrap(text)
        return open_blob(self._aead, blob, aad.encode("utf-8")).decode("utf-8")

    def blind_index(self, field_name, normalized):
        if self._bidx_key is None:
            raise PiiError(self.error_code or "key_missing")
        subkey = self._subkeys.get(field_name)
        if subkey is None:
            with self._sub_lock:
                subkey = self._subkeys.get(field_name)
                if subkey is None:
                    subkey = derive_bidx_subkey(self._bidx_key, field_name)
                    self._subkeys[field_name] = subkey
        return blind_index_hex(subkey, normalized)


def _read_key_file(path):
    """32 byte dari file base64. :raises PiiError: key_missing | key_invalid."""
    try:
        with open(path, encoding="ascii") as handle:
            raw = handle.read().strip()
    except (OSError, UnicodeDecodeError):
        raise PiiError("key_missing") from None
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise PiiError("key_invalid") from None
    if len(key) != KEY_BYTES:
        raise PiiError("key_invalid")
    return key


def _candidate_paths():
    """Kembalikan (dict versi -> path, path kunci blind index, versi aktif mentah, path kunci token nama)."""
    key_dir = os.environ.get(ENV_KEY_DIR) or DEFAULT_KEY_DIR
    paths = {}
    try:
        names = os.listdir(key_dir)
    except OSError:
        names = []
    for name in names:
        match = KEY_FILE_RE.match(name)
        if match:
            paths[int(match.group(1))] = os.path.join(key_dir, name)
    for env_name, env_value in os.environ.items():
        match = KEY_ENV_RE.match(env_name)
        if match and env_value:
            paths[int(match.group(1))] = env_value
    bidx_path = os.environ.get(ENV_BIDX_FILE) or os.path.join(key_dir, BIDX_FILE_NAME)
    name_path = os.environ.get(ENV_NAME_FILE) or os.path.join(key_dir, NAME_FILE_NAME)
    return paths, bidx_path, os.environ.get(ENV_ACTIVE_VERSION) or "1", name_path


def _stat_sig(path):
    try:
        st = os.stat(path)
    except OSError:
        return (path, None, None)
    return (path, st.st_mtime_ns, st.st_size)


def _fingerprint(paths, bidx_path, active_raw, name_path=None):
    return (
        tuple(sorted((v, _stat_sig(p)) for v, p in paths.items())),
        _stat_sig(bidx_path),
        active_raw,
        _stat_sig(name_path) if name_path else None,
    )


def _build(paths, bidx_path, active_raw, name_path=None):
    error_code = None
    keys = {}
    for version, path in paths.items():
        try:
            keys[version] = _read_key_file(path)
        except PiiError as exc:
            error_code = error_code or exc.code
    try:
        active = int(active_raw)
    except ValueError:
        active = None
        error_code = error_code or "version_unknown"
    if active is not None and active not in keys:
        error_code = error_code or ("key_invalid" if active in paths else "key_missing")
    try:
        bidx_key = _read_key_file(bidx_path)
    except PiiError as exc:
        bidx_key = None
        error_code = error_code or exc.code
    name_key = None
    if name_path:
        try:
            name_key = _read_key_file(name_path)
        except PiiError as exc:
            error_code = error_code or exc.code
    else:
        error_code = error_code or "key_missing"
    return KeyRing(keys, active, bidx_key, error_code=error_code, name_key=name_key)


_state_lock = threading.Lock()
_state = {"ring": None, "fp": None, "checked": 0.0}


def reset_keyring_cache():
    """Paksa pemuatan ulang pada panggilan berikut (test / setelah rotasi)."""
    with _state_lock:
        _state.update(ring=None, fp=None, checked=0.0)


def snapshot_keyring_cache():
    """Untuk test: simpan status cache agar bisa dipulihkan (test modul lain tidak terdampak)."""
    with _state_lock:
        return dict(_state)


def restore_keyring_cache(snapshot):
    with _state_lock:
        _state.clear()
        _state.update(snapshot)


def log_keyring(ring, event, dbname, level=None, error_code=None, status=None):
    """Satu baris log terstruktur (skema ADR-036 §4.15). Tanpa nilai/kunci/id partner."""
    payload = {
        "schema_version": 1,
        "event": event,
        "db": (dbname or "")[:64],
        "active_version": ring.active_version,
        "loaded_versions": ring.loaded_versions,
        "status": status or ring.status,
        "error_code": error_code or (ring.error_code if ring.status != "ok" else None),
    }
    if level is None:
        level = logging.INFO if ring.status == "ok" else logging.CRITICAL
    _logger.log(level, "%s %s", LOG_TAG, json.dumps(payload, sort_keys=True))


_SAFE_NAME_RE = re.compile(r"^[a-z0-9_.]{1,64}$")


def log_alarm(event, dbname, error_code, model=None, fields=None, count=None, level=logging.CRITICAL):
    """Alarm integritas PII (K-36-8): `unbound_ciphertext`, `plaintext_in_protected`, `field_not_encrypted`.

    Hanya kode, nama model/field (bukan data pribadi), dan jumlah. Tanpa id record, nilai, ciphertext.
    """
    payload = {
        "schema_version": 1,
        "event": event,
        "db": (dbname or "")[:64],
        "status": "error",
        "error_code": error_code,
    }
    if model and _SAFE_NAME_RE.match(model):
        payload["model"] = model
    if fields:
        payload["fields"] = sorted(f for f in fields if isinstance(f, str) and _SAFE_NAME_RE.match(f))
    if count is not None:
        payload["count"] = int(count)
    _logger.log(level, "%s %s", LOG_TAG, json.dumps(payload, sort_keys=True))


def required():
    return (os.environ.get(ENV_REQUIRED) or "").strip().lower() in {"1", "true", "yes", "on"}


def get_keyring(dbname=None, log=True):
    """Key ring terkini (cache per proses, dicek ulang tiap RECHECK_SECONDS via stat).

    Perubahan status/file dicatat sekali: INFO bila ok; bila terkunci CRITICAL saat kunci yang
    tadinya ada hilang atau EFN_ODOO_PII_REQUIRED=true, selain itu WARNING (dev/CI tanpa secret).
    Tulis data terlindungi yang ditolak selalu CRITICAL (raise_locked). ``log=False`` dipakai
    pemeriksaan start yang menentukan levelnya sendiri (ada/tidaknya partner terlindungi).
    """
    now = time.monotonic()
    with _state_lock:
        ring = _state["ring"]
        if ring is not None and now - _state["checked"] < RECHECK_SECONDS:
            return ring
    candidates = _candidate_paths()
    fp = _fingerprint(*candidates)
    with _state_lock:
        if _state["ring"] is not None and _state["fp"] == fp:
            _state["checked"] = now
            return _state["ring"]
    ring = _build(*candidates)
    with _state_lock:
        previous = _state["ring"]
        _state.update(ring=ring, fp=fp, checked=now)
    if log:
        if ring.can_encrypt:
            log_keyring(ring, "startup_check", dbname)
        else:
            lost = previous is not None and previous.can_encrypt
            level = logging.CRITICAL if (lost or required()) else logging.WARNING
            log_keyring(ring, "locked", dbname, level=level)
    return ring


# ---------------------------------------------------------------------------------------
# Normalisasi blind index (ADR-036 §4.4) - harus sama saat tulis dan saat cari.
# ---------------------------------------------------------------------------------------
_DIGITS_RE = re.compile(r"\D")


def normalize_vat(value):
    digits = _DIGITS_RE.sub("", value or "")
    return digits or None


def normalize_phone_digits(value):
    """Fallback nomor luar negeri: digit saja (dengan kode negara) bila diawali + / 00."""
    raw = (value or "").strip()
    if raw.startswith("+"):
        digits = _DIGITS_RE.sub("", raw)
    elif raw.startswith("00"):
        digits = _DIGITS_RE.sub("", raw)[2:]
    else:
        return None
    return digits if 6 <= len(digits) <= 15 else None
