"""`res.partner`: kelas partner terlindungi + enkripsi phone/email/vat (PoC E-38, ADR-036 §4.1).

Status: PoC - belum untuk produksi. Cakupan PoC hanya `phone`, `email`, `vat`
(street/street2/NIK = tahap penuh E-38).
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL

from . import keyring as kr
from .fields_pii import (
    NORMALIZERS,
    PROTECTED_FIELD,
    EfnPiiChar,
    bidx_field_name,
    keyring_for,
    raise_locked,
    reject_marker,
)

_logger = logging.getLogger(__name__)

PII_FIELDS = ("phone", "email", "vat")


class ResPartner(models.Model):
    _inherit = "res.partner"

    efn_pii_protected = fields.Boolean(
        string="EFN: Data Pribadi Terlindungi",
        default=False,
        index=True,
        copy=False,
        readonly=True,
        groups="base.group_system",
        help="Satu arah (ADR-036 §4.1): bila True, HP/email/NPWP partner disimpan terenkripsi "
        "(AES-256-GCM) dan hanya bisa dicari dengan nilai lengkap (blind index).",
    )

    # Definisi ulang dengan kelas EfnPiiChar. Atribut definisi sebelumnya (base: index/string/
    # help; mail: tracking) tetap digabung oleh Field._get_attrs.
    phone = EfnPiiChar()
    email = EfnPiiChar()
    vat = EfnPiiChar()

    efn_phone_bidx = fields.Char(
        compute="_compute_efn_pii_bidx",
        store=True,
        index=True,
        readonly=True,
        copy=False,
        groups="base.group_system",
    )
    efn_email_bidx = fields.Char(
        compute="_compute_efn_pii_bidx",
        store=True,
        index=True,
        readonly=True,
        copy=False,
        groups="base.group_system",
    )
    efn_vat_bidx = fields.Char(
        compute="_compute_efn_pii_bidx",
        store=True,
        index=True,
        readonly=True,
        copy=False,
        groups="base.group_system",
    )

    # ------------------------------------------------------------------ registry
    def _register_hook(self):
        """R-T18: modul lain yang mendefinisikan ulang field dengan `fields.Char` biasa SETELAH
        modul ini akan menghapus enkripsi diam-diam. Deteksi saat registry dimuat."""
        res = super()._register_hook()
        broken = [f for f in PII_FIELDS if not getattr(self._fields.get(f), "efn_pii_encrypted", False)]
        if broken:
            _logger.critical("%s field PII tanpa enkripsi (didefinisikan ulang modul lain): %s", kr.LOG_TAG, broken)
        self._efn_pii_startup_check()
        return res

    def _efn_pii_startup_check(self):
        """Satu baris EFN_PII_KEYRING saat registry dimuat.

        Terkunci + (ada partner terlindungi ATAU EFN_ODOO_PII_REQUIRED=true) -> CRITICAL.
        Terkunci tanpa data terlindungi (dev/CI tanpa secret) -> WARNING, agar instalasi tidak
        tampak gagal; tulis data terlindungi tetap ditolak. Odoo tidak menolak start (POS jalan).
        """
        dbname = self.env.cr.dbname
        ring = kr.get_keyring(dbname, log=False)
        if ring.can_encrypt:
            kr.log_keyring(ring, "startup_check", dbname)
            return
        self.env.cr.execute(SQL("SELECT EXISTS (SELECT 1 FROM res_partner WHERE efn_pii_protected)"))
        has_protected = self.env.cr.fetchone()[0]
        level = logging.CRITICAL if (has_protected or kr.required()) else logging.WARNING
        kr.log_keyring(ring, "startup_check", dbname, level=level)

    # ------------------------------------------------------------------ compute
    @api.depends("phone", "email", "vat", PROTECTED_FIELD)
    def _compute_efn_pii_bidx(self):
        ring = None
        for partner in self:
            protected = partner.sudo()[PROTECTED_FIELD]
            for fname in PII_FIELDS:
                value = False
                raw = partner[fname] if protected else False
                if raw == kr.PLACEHOLDER:
                    # dekripsi gagal / kunci tidak ada: jangan hitung blind index dari pengganti
                    ring = ring or keyring_for(self.env)
                    raise_locked(self.env, ring)
                normalized = NORMALIZERS[fname](raw) if raw else None
                if normalized:
                    ring = ring or keyring_for(self.env)
                    if not ring.can_encrypt:
                        raise_locked(self.env, ring)
                    value = ring.blind_index(fname, normalized)
                partner[bidx_field_name(fname)] = value

    @api.depends(lambda self: [self._primary_email, PROTECTED_FIELD])
    def _compute_email_normalized(self):
        """`email_normalized` = salinan plaintext ber-stored (mail) -> kosong untuk terlindungi."""
        protected = self.filtered(lambda p: p.sudo()[PROTECTED_FIELD])
        for partner in protected:
            partner.email_normalized = False
        super(ResPartner, self - protected)._compute_email_normalized()

    @api.depends(lambda self: [*(self._phone_get_sanitize_triggers() or []), PROTECTED_FIELD])
    def _compute_phone_sanitized(self):
        """`phone_sanitized` = salinan plaintext ber-index (phone_validation) -> kosong."""
        protected = self.filtered(lambda p: p.sudo()[PROTECTED_FIELD])
        for partner in protected:
            partner.phone_sanitized = False
        super(ResPartner, self - protected)._compute_phone_sanitized()

    # ------------------------------------------------------------------ chatter
    def _mail_track(self, tracked_fields, initial_values):
        """Jangan tulis nilai lama/baru HP/email/NPWP partner terlindungi ke mail.tracking.value."""
        if self.sudo()[PROTECTED_FIELD]:
            tracked_fields = {k: v for k, v in tracked_fields.items() if k not in PII_FIELDS}
        return super()._mail_track(tracked_fields, initial_values)

    # ------------------------------------------------------------------ create / write
    def _efn_pii_check_vals(self, vals, protected):
        has_value = False
        for fname in PII_FIELDS:
            value = vals.get(fname)
            reject_marker(value)
            has_value = has_value or bool(value)
        if protected and has_value:
            ring = keyring_for(self.env)
            if not ring.can_encrypt:
                raise_locked(self.env, ring)

    def copy_data(self, default=None):
        """Duplikat partner terlindungi tetap terlindungi (menyimpang dari `copy=False` murni di
        ADR-036 §4.1: tanpa ini `copy()` menghasilkan salinan HP/email/NPWP plaintext).
        Akibat: user non-admin tidak bisa menduplikasi partner terlindungi (AccessError field).
        `default` dari pemanggil (RPC) TIDAK bisa mematikan penanda: flag satu arah juga berlaku untuk salinan."""
        vals_list = super().copy_data(default=default)
        for partner, vals in zip(self, vals_list, strict=True):
            if partner.sudo()[PROTECTED_FIELD]:
                vals[PROTECTED_FIELD] = True
        return vals_list

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._efn_pii_check_vals(vals, bool(vals.get(PROTECTED_FIELD)))
        return super().create(vals_list)

    def write(self, vals):
        if PROTECTED_FIELD in vals or any(f in vals for f in PII_FIELDS):
            # muat flag ke cache (dipakai juga oleh flush EfnPiiChar)
            protected_before = self.sudo().filtered(PROTECTED_FIELD)
            if PROTECTED_FIELD in vals and not vals[PROTECTED_FIELD] and protected_before:
                raise UserError(_("Penanda data pribadi terlindungi bersifat satu arah dan tidak bisa dicabut."))
            becoming = (self - protected_before) if vals.get(PROTECTED_FIELD) else self.browse()
            self._efn_pii_check_vals(vals, bool(protected_before or becoming))
            if becoming:
                ring = keyring_for(self.env)
                if not ring.can_encrypt:
                    raise_locked(self.env, ring)
        else:
            becoming = self.browse()
        res = super().write(vals)
        if becoming:
            becoming._efn_pii_encrypt_existing()
        return res

    def _efn_pii_encrypt_existing(self):
        """Partner yang baru menjadi terlindungi: enkripsi nilai lama yang sudah ada di kolom.

        Nilai yang tidak berubah tidak di-flush ORM (Field.write membuang nilai sama), jadi
        ditulis langsung: plaintext dibaca lewat SQL, dienkripsi di Python, UPDATE per baris
        (tanpa menaikkan write_date / tracking). Blind index & salinan turunan sudah dihitung
        ulang oleh flush karena bergantung pada `efn_pii_protected`.
        """
        self.flush_recordset()
        ring = keyring_for(self.env)
        if not ring.can_encrypt:
            raise_locked(self.env, ring)
        cr = self.env.cr
        for fname in PII_FIELDS:
            cr.execute(
                SQL(
                    "SELECT id, %s FROM res_partner WHERE id IN %s AND %s IS NOT NULL AND %s NOT LIKE %s",
                    SQL.identifier(fname),
                    tuple(self.ids),
                    SQL.identifier(fname),
                    SQL.identifier(fname),
                    kr.PREFIX + "%",
                )
            )
            for partner_id, plain in cr.fetchall():
                if not plain:
                    continue
                cipher = ring.encrypt_text(plain, kr.aad_for(self._table, fname))
                cr.execute(
                    SQL(
                        "UPDATE res_partner SET %s = %s WHERE id = %s",
                        SQL.identifier(fname),
                        cipher,
                        partner_id,
                    )
                )
        self.invalidate_recordset(list(PII_FIELDS))
