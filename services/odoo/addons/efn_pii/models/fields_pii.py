"""Mekanisme M1 (ADR-036 §8.2 + §0.3-§0.5): subclass field Odoo yang mengenkripsi di konversi ke kolom.

Kelas:
- `EfnPiiChar(fields.Char)` / `EfnPiiText(fields.Text)`: nilai terenkripsi AES-256-GCM per record (logika di
  `EfnPiiFieldMixin`). Nama kelas `EfnPiiChar` dipakai modul lain sebagai penanda (mis. efn_online_order).
- `EfnBidxChar(fields.Char)`: kolom deterministik yang untuk record terlindungi berisi blind index (hex HMAC) nilai
  ternormalisasi, bukan plaintext. Dipakai bila kolom itu ikut constraint unik / dicari `=` oleh Odoo standar
  (mis. `res.partner.bank.sanitized_acc_number`, Q-PII9).

Jalur ORM Odoo 19.0 (diverifikasi dari source `odoo/orm/{fields,models,domains}.py` branch 19.0):

- INSERT (`create`, `load`, `copy`, `sudo()`): `_create(data_list)` memanggil
  `field.convert_to_column_insert(stored[fname], self, stored)` dengan `self` = recordset KOSONG
  (id belum ada) -> format `efnpii1:` (AAD tanpa id). `_create` model dibungkus (`pii_model.created`) sehingga id
  baru dicatat di `cr.cache` (SEC-148), lalu setelah `super().create()` kembali kolom ditulis ulang ke `efnpii2:`
  (AAD + id) dengan SQL di transaksi yang sama (`pii_model.bind_new_records`).
- UPDATE flush (`write`, `__set__`, `load` update, flush compute stored): `_flush` ->
  `field.get_column_update(record)` -> `convert_to_column_insert(value, record, validate=False)`
  dengan record ber-id -> langsung `efnpii2:`.
- Baca: `_fetch_query` -> `field._insert_cache(fetched, values)` (nilai mentah DB, setdefault), lalu
  `__get__` -> `convert_to_record` -> dekripsi. `_insert_cache` juga mencatat nilai mentah non-kosong
  TANPA penanda untuk dicek model (alarm `plaintext_in_protected`, SEC-137).
  `efnpii1:` hanya didekripsi untuk id yang di-INSERT di cursor ini (belum diikat) atau dalam mode migrasi
  (env `EFN_ODOO_PII_ACCEPT_V1=true` dan migrasi belum dinyatakan selesai); selain itu nilai pengganti + CRITICAL
  `unbound_ciphertext` (SEC-148).
- Domain: `DomainCondition._to_sql` -> `field.condition_to_sql` setelah optimasi (`=` -> `in`,
  `!=` -> `not in`, `ilike ''` -> TRUE). Record terlindungi: blind index (`in`, `=ilike`, `ilike`
  berisi nilai lengkap), token nama (`name`/`complete_name`), atau subquery partner (`_efn_pii_partner_search`).

Atribut model yang dibaca field ini (didefinisikan di kelas model, bukan parameter field):
`_efn_pii_always` (semua record terlindungi, mis. pos.order), `_efn_pii_bidx`
({field: (kolom blind index, subkunci)}), `_efn_pii_token_fields` (field yang memakai `efn_name_tokens`),
`_efn_pii_partner_search` ({field: field many2one res.partner}: `ilike` nama -> `partner IN (subquery token)`),
`_efn_pii_bidx_self` ({field EfnBidxChar: subkunci}).
"""

import logging
import re
import threading
from collections.abc import Set

from odoo import _, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tools import SQL, email_normalize

from odoo.addons.efn_core.models.efn_bff_client import normalize_member_ref

from ..tools import name_tokens as nt
from . import keyring as kr

_logger = logging.getLogger(__name__)

PROTECTED_FIELD = "efn_pii_protected"
TOKENS_FIELD = "efn_name_tokens"

# Cache dekripsi per cursor (L-39 a): kunci (ciphertext, aad) -> plaintext. Dibatasi agar cron panjang
# tidak menumpuk memori; dikosongkan saat penuh.
DECRYPT_CACHE_KEY = "efn_pii_decrypt_cache"
DECRYPT_CACHE_MAX = 20000

# SEC-148: {tabel: set(id)} record yang di-INSERT di cursor ini dan belum diikat ke efnpii2 (cr.cache, bukan context:
# context bisa diisi klien RPC).
CREATED_KEY = "efn_pii_created_ids"
# Kait (latch) DB: diisi `_efn_pii_migrate` setelah tidak ada lagi efnpii1 di DB. Setelah terisi, efnpii1 SELALU
# ditolak walau env mode migrasi menyala. Mengosongkan parameter ini lewat SQL tidak membuka apa pun tanpa env.
V1_MIGRATED_PARAM = "efn_pii.v1_migration_done"

# Tumpukan pemindaian `_fetch_query` (per thread): field `_insert_cache` menambahkan (fname, ids) nilai
# mentah tanpa penanda; model memeriksanya setelah fetch selesai (pii_model.scan_fetch).
_SCAN = threading.local()

_DIGITS_RE = re.compile(r"\D")
_NON_WORD_RE = re.compile(r"\W+")
DOCUMENT_SUBKEYS = frozenset({"l10n_id_nik", "l10n_id_buyer_document_number", "identification_id", "acc_number"})
MIN_DOCUMENT_LEN = 5


def normalize_phone(value):
    """HP Indonesia -> `62...` (efn_core, sama dengan member_ref); luar negeri -> digit saja."""
    if not isinstance(value, str):
        return None
    return normalize_member_ref(value) or kr.normalize_phone_digits(value)


def normalize_email(value):
    if not isinstance(value, str):
        return None
    return email_normalize(value, strict=False) or None


def normalize_document(value):
    """NIK / nomor dokumen / nomor rekening: buang selain huruf-angka-underscore, huruf besar
    (sama dengan `sanitize_account_number` base 19.0: `re.sub(r'\\W+', '', v).upper()`)."""
    if not isinstance(value, str):
        return None
    return _NON_WORD_RE.sub("", value).upper() or None


NORMALIZERS = {
    "phone": normalize_phone,
    "email": normalize_email,
    "vat": kr.normalize_vat,
    "l10n_id_nik": normalize_document,
    "l10n_id_buyer_document_number": normalize_document,
    "identification_id": normalize_document,
    "acc_number": normalize_document,
}


def bidx_field_name(fname):
    return f"efn_{fname}_bidx"


def keyring_for(env):
    return kr.get_keyring(env.cr.dbname)


def raise_locked(env, ring):
    kr.log_keyring(ring, "locked", env.cr.dbname)
    raise UserError(
        _(
            "Kunci enkripsi data pribadi tidak tersedia (%(code)s). Data pribadi terlindungi tidak bisa "
            "dibuat atau diubah sampai Superadmin memasang kunci.",
            code=ring.error_code or "key_missing",
        )
    )


def reject_marker(value):
    if kr.looks_like_marker(value):
        raise ValidationError(_("Nilai tidak boleh diawali penanda format enkripsi internal."))


def complete_term(subkey, value):
    """Istilah `ilike`/`like` yang berbentuk nilai LENGKAP (ADR-036 §0.6) -> nilai untuk blind index.

    email: ada '@' dan ternormalisasi; HP: >= 9 digit, ternormalisasi; NPWP: 15/16 digit; NIK / nomor dokumen /
    rekening: >= 5 huruf-angka. Wildcard di tepi dibuang; wildcard di tengah -> bukan nilai lengkap.
    """
    if not isinstance(value, str):
        return None
    term = value.strip().strip("%").strip()
    if not term or "%" in term:
        return None
    if subkey == "email":
        return term if "@" in term and normalize_email(term) else None
    if "_" in term and subkey != "acc_number":
        return None
    digits = _DIGITS_RE.sub("", term)
    if subkey == "phone":
        return term if len(digits) >= 9 and normalize_phone(term) else None
    if subkey == "vat":
        return term if len(digits) in (15, 16) else None
    if subkey in DOCUMENT_SUBKEYS:
        normalized = normalize_document(term)
        return term if normalized and len(normalized) >= MIN_DOCUMENT_LEN else None
    return None


# --------------------------------------------------------------------------- blind index (dipakai bersama)
def bidx_hashes(env, subkey, items):
    """Set hex blind index untuk ``items`` (None bila kunci tidak ada / normalizer tidak dikenal)."""
    normalizer = NORMALIZERS.get(subkey)
    if normalizer is None:
        return None
    hashes = set()
    ring = None
    for item in items:
        if not isinstance(item, str):
            continue
        normalized = normalizer(item)
        if not normalized:
            continue
        if ring is None:
            ring = keyring_for(env)
        try:
            hashes.add(ring.blind_index(subkey, normalized))
        except kr.PiiError:
            # kunci blind index tidak ada: pencarian exact atas record terlindungi kosong
            return None
    return hashes


def bidx_condition(column_sql, subkey, operator, value, env):
    """SQL `kolom IN (hash...)` untuk operator yang didukung blind index, atau None."""
    if operator == "in" and isinstance(value, (Set, list, tuple)):
        items = [item for item in value if isinstance(item, str)]
    elif operator == "=ilike" and isinstance(value, str):
        # `=ilike` tanpa % = sama persis tanpa beda huruf: setara blind index ternormalisasi.
        # `_` diperlakukan literal (lazim di email); `%` -> bukan nilai lengkap.
        items = [value] if "%" not in value else []
    elif operator in ("ilike", "like"):
        term = complete_term(subkey, value)
        items = [term] if term else []
    else:
        return None
    if not items:
        return None
    hashes = bidx_hashes(env, subkey, items)
    if not hashes:
        return None
    return SQL("%s IN %s", column_sql, tuple(sorted(hashes)))


def blind_index_value(env, subkey, value):
    """Hex blind index satu nilai (False bila kosong). Mode terkunci -> UserError (fail-closed)."""
    normalized = NORMALIZERS[subkey](value) if value else None
    if not normalized:
        return False
    ring = keyring_for(env)
    if not ring.can_encrypt:
        raise_locked(env, ring)
    return ring.blind_index(subkey, normalized)


def record_name_tokens(ring, name, complete_name, table="res_partner", record_id=None):
    """Isi kolom `efn_name_tokens` (token dipisah spasi) atau False bila nama kosong.

    Padding deterministik per (tabel, id, himpunan token asli) — SEC-149.
    """
    inputs = nt.record_inputs(name, complete_name)
    tokens = {ring.name_token(kind, value) for kind, values in inputs.items() for value in values}
    if not tokens:
        return False
    pad_token, extra_blocks = ring.name_padder(table, record_id, tokens)
    padded = nt.pad_tokens(tokens, pad_token, extra_blocks=extra_blocks)
    return " ".join(padded) or False


# --------------------------------------------------------------------------- SEC-148 (efnpii1)
def mark_created(records):
    """Catat id record yang baru di-INSERT (dipanggil dari `_create` model)."""
    ids = [rid for rid in records._ids if isinstance(rid, int)]
    if ids:
        records.env.cr.cache.setdefault(CREATED_KEY, {}).setdefault(records._table, set()).update(ids)
    return records


def unmark_created(records):
    store = records.env.cr.cache.get(CREATED_KEY)
    if store and records._table in store:
        store[records._table].difference_update(records._ids)


def is_created(env, table, record_id):
    store = env.cr.cache.get(CREATED_KEY) or {}
    return record_id in store.get(table, ())


def v1_migrated(env):
    """True bila migrasi efnpii1 dinyatakan selesai (kait DB). Hanya dibaca saat efnpii1 ditemukan (jarang)."""
    env["ir.config_parameter"].flush_model(["key", "value"])
    env.cr.execute(
        SQL(
            "SELECT 1 FROM ir_config_parameter WHERE key = %s AND COALESCE(value, '') <> ''",
            V1_MIGRATED_PARAM,
        )
    )
    return bool(env.cr.fetchone())


def v1_migration_mode(env):
    """Mode migrasi: env eksplisit DAN kait DB belum terpasang (SEC-148)."""
    return kr.accept_v1_env() and not v1_migrated(env)


def _decrypt_cache(env, ring):
    """Cache per cursor, dikosongkan saat key ring berganti (rotasi/kunci dicabut) atau penuh."""
    store = env.cr.cache
    entry = store.get(DECRYPT_CACHE_KEY)
    if entry is None or entry[0] is not ring or len(entry[1]) >= DECRYPT_CACHE_MAX:
        entry = store[DECRYPT_CACHE_KEY] = (ring, {})
    return entry[1]


class EfnPiiFieldMixin:
    """Logika enkripsi bersama untuk `EfnPiiChar` / `EfnPiiText`. Tanpa parameter field tambahan."""

    # penanda untuk pemeriksaan registry (SEC-137) dan test
    efn_pii_encrypted = True

    # ------------------------------------------------------------------ util
    @staticmethod
    def _efn_always(model):
        return bool(getattr(model, "_efn_pii_always", False))

    def _efn_is_protected(self, record, values):
        if self._efn_always(record):
            return True
        if values is not None:
            # jalur INSERT: `values` = dict kolom tersimpan dari create (termasuk default & precompute)
            return bool(values.get(PROTECTED_FIELD))
        if PROTECTED_FIELD not in record._fields or len(record) != 1:
            return False
        # jalur UPDATE (flush): field ber-groups admin -> baca dengan sudo (cache bersama)
        return bool(record.sudo()[PROTECTED_FIELD])

    @staticmethod
    def _efn_record_id(record):
        if len(record) != 1:
            return None
        rid = record.id
        if not isinstance(rid, int):
            origin = record._origin
            rid = origin.id if origin else None
        return rid if isinstance(rid, int) and rid > 0 else None

    # ------------------------------------------------------------------ tulis
    def convert_to_column_insert(self, value, record, values=None, validate=True):
        value = super().convert_to_column_insert(value, record, values, validate)
        if not isinstance(value, str) or not value:
            return value
        if kr.looks_like_marker(value):
            if validate:
                # create/import dengan nilai berawalan penanda: tidak boleh memalsukan ciphertext
                reject_marker(value)
            # flush dengan validate=False: nilai cache = ciphertext hasil fetch DB, tulis apa adanya
            return value
        if not self._efn_is_protected(record, values):
            return value
        ring = keyring_for(record.env)
        if not ring.can_encrypt:
            # fail-closed: tidak ada fallback plaintext (ADR-036 §4.6)
            raise_locked(record.env, ring)
        rid = None if values is not None else self._efn_record_id(record)
        if rid is None:
            # INSERT: id belum ada -> efnpii1, ditulis ulang ke efnpii2 di create yang sama
            return ring.encrypt_text(value, kr.aad_for(record._table, self.name), kr.PREFIX)
        return ring.encrypt_text(value, kr.aad_for(record._table, self.name, rid), kr.PREFIX_V2)

    # ------------------------------------------------------------------ baca
    def _insert_cache(self, records, values):
        values = list(values)
        super()._insert_cache(records, values)
        stack = getattr(_SCAN, "stack", None)
        if not stack or not self.store:
            return
        ids = [
            rid
            for rid, value in zip(records._ids, values, strict=False)
            if isinstance(value, str) and value and not kr.looks_like_marker(value)
        ]
        if ids:
            stack[-1].append((self.name, ids))

    def convert_to_record(self, value, record):
        value = super().convert_to_record(value, record)
        fmt = kr.format_of(value)
        if fmt is None:
            return value
        env = record.env
        created = False
        try:
            rid = self._efn_record_id(record)
            if fmt == 2:
                if rid is None:
                    raise kr.PiiError("auth_failed")
                aad = kr.aad_for(record._table, self.name, rid)
            else:
                aad = kr.aad_for(record._table, self.name)
        except (kr.PiiError, ValueError):
            return self._efn_decrypt_failed(record, "auth_failed")
        if fmt == 1:
            # SEC-148: efnpii1 hanya sah di dalam transaksi create (id belum diikat) atau mode migrasi.
            created = rid is not None and is_created(env, record._table, rid)
            if not created and not v1_migration_mode(env):
                kr.log_alarm(
                    "decrypt_refused", env.cr.dbname, "unbound_ciphertext", model=record._name, fields=[self.name]
                )
                return kr.PLACEHOLDER
        ring = keyring_for(env)
        cache = _decrypt_cache(env, ring)
        plain = cache.get((value, aad))
        if plain is not None:
            return plain
        try:
            plain = ring.decrypt_text(value, aad)
        except kr.PiiError as exc:
            if exc.code != "auth_failed" and not ring.can_encrypt and not ring.loaded_versions:
                # ring terkunci: sudah dicatat CRITICAL sekali saat dimuat (get_keyring)
                return kr.PLACEHOLDER
            return self._efn_decrypt_failed(record, exc.code)
        if fmt == 1 and not created:
            # mode migrasi: data PoC lama tetap terbaca (tanpa kehilangan data) + alarm sampai dimigrasi.
            kr.log_alarm("decrypt_unbound", env.cr.dbname, "unbound_ciphertext", model=record._name, fields=[self.name])
        cache[(value, aad)] = plain
        return plain

    def _efn_decrypt_failed(self, record, code):
        # nilai pengganti tetap (bukan False) agar form/compute tidak menimpa ciphertext dengan kosong.
        # Log hanya kode (tanpa id record/nilai/ciphertext), ADR-036 §4.6.
        ring = keyring_for(record.env)
        if code == "auth_failed":
            kr.log_keyring(
                ring,
                "decrypt_failure",
                record.env.cr.dbname,
                level=logging.ERROR,
                error_code="auth_failed",
                status="error",
            )
        elif ring.can_encrypt or ring.loaded_versions:
            # ring ok tetapi versi ciphertext tidak dimuat (kunci pensiun dicabut terlalu dini)
            kr.log_keyring(
                ring,
                "decrypt_failure",
                record.env.cr.dbname,
                level=logging.CRITICAL,
                error_code="version_unknown",
                status="error",
            )
        return kr.PLACEHOLDER

    # ------------------------------------------------------------------ cari
    def _efn_token_condition(self, operator, value, model, alias):
        tokens_sql = SQL("string_to_array(%s, ' ')", model._fields[TOKENS_FIELD].to_sql(model, alias))
        if operator == "in" and isinstance(value, (Set, list, tuple)):
            inputs = [nt.exact_input(item) for item in value if isinstance(item, str)]
            kind, mode = "f", "any"
        elif operator == "=ilike" and isinstance(value, str) and not nt.has_like_wildcard(value):
            inputs = [nt.exact_input(value)]
            kind, mode = "f", "any"
        elif operator in ("ilike", "like", "=ilike"):
            inputs = nt.prefix_query_inputs(value)
            if inputs is None:
                return None
            if not inputs:
                # pola tanpa huruf/angka (mis. '%%'): semua record dengan nama
                return SQL("%s IS NOT NULL", self.to_sql(model, alias))
            kind, mode = "p", "all"
        else:
            return None
        inputs = [item for item in inputs if item]
        if not inputs:
            return None
        ring = keyring_for(model.env)
        try:
            tokens = sorted({ring.name_token(kind, item) for item in inputs})
        except kr.PiiError:
            return None
        operator_sql = SQL("&&") if mode == "any" else SQL("@>")
        return SQL("(%s %s %s::text[])", tokens_sql, operator_sql, tokens)

    def _efn_partner_condition(self, partner_fname, operator, value, model, alias):
        """Nama tampilan salinan nama partner (mis. `account.move.invoice_partner_display_name`): `ilike`/`=`
        diterjemahkan ke `partner IN (partner yang complete_name-nya cocok)` (token nama di res.partner)."""
        if operator not in ("in", "=ilike", "ilike", "like") or partner_fname not in model._fields:
            return None
        Partner = model.env["res.partner"].sudo().with_context(active_test=False)
        query = Partner._search([("complete_name", operator, value)])
        return SQL("%s IN %s", model._fields[partner_fname].to_sql(model, alias), query.subselect())

    def _efn_protected_condition(self, operator, value, model, alias):
        if self.name in getattr(model, "_efn_pii_token_fields", ()) and TOKENS_FIELD in model._fields:
            return self._efn_token_condition(operator, value, model, alias)
        spec = (getattr(model, "_efn_pii_bidx", None) or {}).get(self.name)
        if spec:
            bidx_name, subkey = spec
            if bidx_name not in model._fields:
                return None
            return bidx_condition(model._fields[bidx_name].to_sql(model, alias), subkey, operator, value, model.env)
        partner_fname = (getattr(model, "_efn_pii_partner_search", None) or {}).get(self.name)
        if partner_fname:
            return self._efn_partner_condition(partner_fname, operator, value, model, alias)
        return None

    def condition_to_sql(self, field_expr, operator, value, model, alias, query):
        always = self._efn_always(model)
        # salinan field di model `_inherits` (res.users, hr.employee) = related non-stored -> standar
        if (
            field_expr != self.name
            or not self.store
            or (not always and (PROTECTED_FIELD not in model._fields or not model._fields[PROTECTED_FIELD].store))
        ):
            return super().condition_to_sql(field_expr, operator, value, model, alias, query)
        if operator.startswith("not "):
            # negasi semantik dari operator positif: `not in`/`not ilike` = NOT(positif),
            # baris NULL ikut seperti perilaku standar Odoo
            positive = self.condition_to_sql(field_expr, operator[4:], value, model, alias, query)
            return SQL("(NOT COALESCE(%s, FALSE))", positive)
        base = super().condition_to_sql(field_expr, operator, value, model, alias, query)
        column = self.to_sql(model, alias)
        extra = self._efn_protected_condition(operator, value, model, alias)
        if always:
            # semua baris berisi ciphertext: hanya semantik NULL dari kondisi standar + blind index (bila ada)
            standard = SQL("(%s AND %s IS NULL)", base, column)
            return standard if extra is None else SQL("(%s OR %s)", standard, extra)
        protected = SQL("COALESCE(%s, FALSE)", model._fields[PROTECTED_FIELD].to_sql(model, alias))
        # bagian standar hanya berlaku untuk record tidak terlindungi (atau kolom NULL),
        # sehingga kecocokan kebetulan atas teks ciphertext tidak pernah terjadi
        plain = SQL("(%s AND (NOT %s OR %s IS NULL))", base, protected, column)
        if extra is None:
            return plain
        return SQL("(%s OR (%s AND %s))", plain, protected, extra)


class EfnPiiChar(EfnPiiFieldMixin, fields.Char):
    """Char terenkripsi AES-256-GCM per record (ADR-036)."""


class EfnPiiText(EfnPiiFieldMixin, fields.Text):
    """Text terenkripsi (mis. `mail.mail.email_to`), perilaku sama dengan `EfnPiiChar`."""


class EfnBidxChar(fields.Char):
    """Kolom deterministik: record terlindungi menyimpan blind index (hex HMAC-SHA256 64 karakter), bukan plaintext.

    Nilai dihitung oleh compute model (lihat `res.partner.bank`). Domain `=`/`in`/`=ilike`/`ilike` nilai lengkap
    atas record terlindungi diterjemahkan ke blind index (subkunci dari `_efn_pii_bidx_self[field]`); record
    tidak terlindungi memakai SQL standar. Constraint unik atas kolom tetap berlaku (HMAC deterministik).
    """

    efn_pii_bidx_column = True

    def condition_to_sql(self, field_expr, operator, value, model, alias, query):
        subkey = (getattr(model, "_efn_pii_bidx_self", None) or {}).get(self.name)
        if (
            field_expr != self.name
            or not self.store
            or not subkey
            or PROTECTED_FIELD not in model._fields
            or not model._fields[PROTECTED_FIELD].store
        ):
            return super().condition_to_sql(field_expr, operator, value, model, alias, query)
        if operator.startswith("not "):
            positive = self.condition_to_sql(field_expr, operator[4:], value, model, alias, query)
            return SQL("(NOT COALESCE(%s, FALSE))", positive)
        base = super().condition_to_sql(field_expr, operator, value, model, alias, query)
        column = self.to_sql(model, alias)
        protected = SQL("COALESCE(%s, FALSE)", model._fields[PROTECTED_FIELD].to_sql(model, alias))
        plain = SQL("(%s AND (NOT %s OR %s IS NULL))", base, protected, column)
        extra = bidx_condition(column, subkey, operator, value, model.env)
        if extra is None:
            return plain
        return SQL("(%s OR (%s AND %s))", plain, protected, extra)
