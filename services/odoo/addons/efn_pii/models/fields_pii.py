"""Mekanisme M1 (ADR-036 §8.2): subclass `fields.Char` yang mengenkripsi di konversi ke kolom.

Jalur ORM Odoo 19.0 yang dilewati (diverifikasi dari source `odoo/orm/{fields,models,domains}.py`
branch 19.0, 18 Okt 2026):

- INSERT (`create`, `load` -> `_load_records_create` -> `create`, `sudo()`):
  hook `_create` -> `field.convert_to_column_insert(stored[fname], self, stored)`;
  enkripsi bila `stored["efn_pii_protected"]`.
- UPDATE flush (`write`, `__set__`, `load` -> `_load_records_write` -> `write`, flush compute):
  hook `_flush` -> `field.get_column_update(record)` -> `convert_to_column_insert(value, record, validate=False)`;
  enkripsi bila record terlindungi.
- Baca (`__get__`, `read`, `web_read`, `search_read`, `mapped`, `filtered`, ekspor, QWeb):
  cache berisi nilai mentah DB (`_fetch_query` -> `_insert_cache` tanpa konversi), lalu `convert_to_record`;
  dekripsi `efnpii1:` -> plaintext; kunci tak tersedia -> PLACEHOLDER.
- Domain (`search`, `name_search`, `_search`):
  `DomainCondition._to_sql` -> `field.condition_to_sql` (setelah optimasi `=` -> `in`);
  `in`/`not in` ditulis ulang ke blind index; operator lain tidak cocok untuk partner terlindungi.

Tidak ada monkeypatch: field ini didefinisikan ulang di `_inherit = "res.partner"` (atribut
definisi sebelumnya - `tracking`, `index`, `string`, `help` - digabung oleh `Field._get_attrs`
karena kelasnya turunan `Char`). `Field.__init_subclass__` mendaftarkan tipe dengan
`setdefault`, sehingga `Field._by_type__["char"]` tetap `fields.Char`.
"""

import logging
from collections.abc import Set

from odoo import _, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tools import SQL, email_normalize

from odoo.addons.efn_core.models.efn_bff_client import normalize_member_ref

from . import keyring as kr

_logger = logging.getLogger(__name__)

PROTECTED_FIELD = "efn_pii_protected"

_POSITIVE_LIKE = {"like", "ilike", "=like", "=ilike"}


def normalize_phone(value):
    """HP Indonesia -> `62...` (efn_core, sama dengan member_ref); luar negeri -> digit saja."""
    if not isinstance(value, str):
        return None
    return normalize_member_ref(value) or kr.normalize_phone_digits(value)


def normalize_email(value):
    if not isinstance(value, str):
        return None
    return email_normalize(value, strict=False) or None


NORMALIZERS = {
    "phone": normalize_phone,
    "email": normalize_email,
    "vat": kr.normalize_vat,
}


def bidx_field_name(fname):
    return f"efn_{fname}_bidx"


def keyring_for(env):
    return kr.get_keyring(env.cr.dbname)


def raise_locked(env, ring):
    kr.log_keyring(ring, "locked", env.cr.dbname)
    raise UserError(
        _(
            "Kunci enkripsi data pribadi tidak tersedia (%(code)s). Data kontak pelanggan "
            "terlindungi tidak bisa diubah sampai Superadmin memasang kunci.",
            code=ring.error_code or "key_missing",
        )
    )


def reject_marker(value):
    if kr.is_ciphertext(value):
        raise ValidationError(_("Nilai tidak boleh diawali penanda format enkripsi internal."))


class EfnPiiChar(fields.Char):
    """Char terenkripsi AES-256-GCM per record untuk partner `efn_pii_protected` (ADR-036).

    Tidak menambah parameter field (tanpa `_valid_field_parameter`): AAD diturunkan dari
    tabel+nama kolom, kolom blind index = `efn_<nama>_bidx`, normalisasi dari NORMALIZERS.
    """

    # penanda untuk pemeriksaan registry (_register_hook) dan test
    efn_pii_encrypted = True

    # ------------------------------------------------------------------ tulis
    def _efn_is_protected(self, record, values):
        if values is not None:
            # jalur INSERT: `values` = dict kolom tersimpan dari create (termasuk default)
            return bool(values.get(PROTECTED_FIELD))
        if PROTECTED_FIELD not in record._fields or len(record) != 1:
            return False
        # jalur UPDATE (flush): field ber-groups admin -> baca dengan sudo (cache bersama)
        return bool(record.sudo()[PROTECTED_FIELD])

    def convert_to_column_insert(self, value, record, values=None, validate=True):
        value = super().convert_to_column_insert(value, record, values, validate)
        if not isinstance(value, str) or not value:
            return value
        if kr.is_ciphertext(value):
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
        return ring.encrypt_text(value, kr.aad_for(record._table, self.name))

    # ------------------------------------------------------------------ baca
    def convert_to_record(self, value, record):
        value = super().convert_to_record(value, record)
        if not kr.is_ciphertext(value):
            return value
        ring = keyring_for(record.env)
        try:
            return ring.decrypt_text(value, kr.aad_for(record._table, self.name))
        except kr.PiiError as exc:
            # nilai pengganti tetap (bukan False) agar form/compute tidak menimpa ciphertext
            # dengan kosong. Log hanya kode (tanpa id partner/nilai/ciphertext), ADR-036 §4.6.
            if exc.code == "auth_failed":
                kr.log_keyring(
                    ring,
                    "decrypt_failure",
                    record.env.cr.dbname,
                    level=logging.ERROR,
                    error_code="auth_failed",
                    status="error",
                )
            elif ring.can_encrypt:
                # ring ok tetapi versi ciphertext tidak dimuat (kunci pensiun dicabut terlalu dini)
                kr.log_keyring(
                    ring,
                    "decrypt_failure",
                    record.env.cr.dbname,
                    level=logging.CRITICAL,
                    error_code="version_unknown",
                    status="error",
                )
            # ring terkunci: sudah dicatat CRITICAL sekali saat dimuat (get_keyring)
            return kr.PLACEHOLDER

    # ------------------------------------------------------------------ cari
    def _efn_bidx_hashes(self, model, value):
        normalizer = NORMALIZERS.get(self.name)
        if normalizer is None or bidx_field_name(self.name) not in model._fields:
            return None
        hashes = set()
        ring = None
        for item in value:
            if not isinstance(item, str):
                continue
            normalized = normalizer(item)
            if not normalized:
                continue
            if ring is None:
                ring = keyring_for(model.env)
            try:
                hashes.add(ring.blind_index(self.name, normalized))
            except kr.PiiError:
                # kunci blind index tidak ada: pencarian exact atas partner terlindungi kosong
                return None
        return hashes

    def condition_to_sql(self, field_expr, operator, value, model, alias, query):
        # salinan field di model `_inherits` (res.users) = related non-stored -> standar
        if (
            field_expr != self.name
            or not self.store
            or PROTECTED_FIELD not in model._fields
            or not model._fields[PROTECTED_FIELD].store
        ):
            return super().condition_to_sql(field_expr, operator, value, model, alias, query)
        if operator.startswith("not "):
            # negasi semantik dari operator positif: `not in`/`not ilike` = NOT(positif),
            # baris NULL ikut seperti perilaku standar Odoo
            positive = self.condition_to_sql(field_expr, operator[4:], value, model, alias, query)
            return SQL("(NOT COALESCE(%s, FALSE))", positive)
        base = super().condition_to_sql(field_expr, operator, value, model, alias, query)
        protected = SQL("COALESCE(%s, FALSE)", model._fields[PROTECTED_FIELD].to_sql(model, alias))
        column = self.to_sql(model, alias)
        # bagian standar hanya berlaku untuk partner tidak terlindungi (atau kolom NULL),
        # sehingga kecocokan kebetulan atas teks ciphertext tidak pernah terjadi
        plain = SQL("(%s AND (NOT %s OR %s IS NULL))", base, protected, column)
        if operator != "in" or not isinstance(value, (Set, list, tuple)):
            # like/ilike/=like/=ilike/ketidaksamaan/subquery: tidak didukung untuk terlindungi
            return plain
        hashes = self._efn_bidx_hashes(model, value)
        if not hashes:
            return plain
        bidx = model._fields[bidx_field_name(self.name)].to_sql(model, alias)
        return SQL("(%s OR (%s AND %s IN %s))", plain, protected, bidx, tuple(sorted(hashes)))
