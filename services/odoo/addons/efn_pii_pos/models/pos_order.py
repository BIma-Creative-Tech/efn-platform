"""`pos.order.email` / `pos.order.mobile` terenkripsi untuk SEMUA order (ADR-036 §0.2, 18-31, K-36-9).

Odoo 19.0 (`point_of_sale/models/pos_order.py`): kedua field `Char(compute="_compute_contact_details",
readonly=False, store=True)`, compute `@api.depends('partner_id')` menyalin email/HP partner;
`action_send_receipt(email, ...)` menulis `self.email = email` (email yang diketik kasir untuk struk).
Tabel `pos_order` tidak punya penanda: `_efn_pii_always = True` (semua baris ciphertext).

Mode terkunci: salinan dari partner TIDAK dibuat (email/HP order kosong) agar penjualan tetap jalan;
tulis eksplisit (mis. kirim struk email) ditolak (`UserError`), tanpa fallback plaintext.
"""

from odoo import api, models
from odoo.tools import SQL, split_every

from odoo.addons.efn_pii.models import keyring as kr
from odoo.addons.efn_pii.models import pii_model as pm
from odoo.addons.efn_pii.models.fields_pii import EfnPiiChar, keyring_for

PII_FIELDS = ("email", "mobile")


class PosOrder(models.Model):
    _inherit = "pos.order"

    _efn_pii_always = True
    _efn_pii_fields = PII_FIELDS

    email = EfnPiiChar()
    mobile = EfnPiiChar()

    # ------------------------------------------------------------------ registry
    def _register_hook(self):
        res = super()._register_hook()
        pm.check_registry(self)
        return res

    # ------------------------------------------------------------------ compute
    @api.depends("partner_id")
    def _compute_contact_details(self):
        super()._compute_contact_details()
        ring = keyring_for(self.env)
        for order in self:
            if not ring.can_encrypt or kr.PLACEHOLDER in (order.email, order.mobile):
                # tanpa kunci: jangan salin (fail-closed tanpa memblokir penjualan)
                order.email = False
                order.mobile = False

    # ------------------------------------------------------------------ baca
    def _fetch_query(self, query, fields):
        return pm.scan_fetch(self, lambda: super(PosOrder, self)._fetch_query(query, fields))

    # ------------------------------------------------------------------ create / write
    @api.model
    def _create(self, data_list):
        return pm.created(super()._create(data_list))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            pm.check_markers(self, vals)
        broken = pm.broken_fields(self)
        if broken:
            pm.refuse_broken(self, broken)
        records = super().create(vals_list)
        pm.bind_new_records(records)
        return records

    def write(self, vals):
        pm.check_markers(self, vals)
        broken = pm.broken_fields(self)
        if broken and pm.vals_touch_broken(self, broken, vals):
            pm.refuse_broken(self, broken)
        if any(vals.get(fname) for fname in PII_FIELDS):
            pm.require_keys(self.env)
        return super().write(vals)

    # ------------------------------------------------------------------ migrasi
    @api.model
    def _efn_pii_encrypt_legacy(self, batch=1000):
        """Enkripsi email/HP order lama yang masih plaintext / efnpii1 -> efnpii2. :returns: jumlah."""
        pm.require_keys(self.env)
        self.env.flush_all()
        self.env.cr.execute(
            SQL(
                "SELECT id FROM pos_order WHERE (email <> '' AND email NOT LIKE %s) "
                "OR (mobile <> '' AND mobile NOT LIKE %s) ORDER BY id",
                kr.PREFIX_V2 + "%",
                kr.PREFIX_V2 + "%",
            )
        )
        ids = [row[0] for row in self.env.cr.fetchall()]
        total = 0
        allow_v1 = pm.migration_allows_v1(self.env)  # SEC-148
        for chunk in split_every(batch, ids):
            records = self.sudo().browse(chunk)
            counts = pm.rewrite_rows(records, list(chunk), encrypt_plain=True, allow_v1=allow_v1)
            total += counts["encrypted"] + counts["bound"]
            records.invalidate_recordset(list(PII_FIELDS))
        return total
