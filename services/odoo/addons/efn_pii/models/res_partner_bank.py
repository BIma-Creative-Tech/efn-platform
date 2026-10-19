"""E-38b / Q-PII9 (tahap 1b): rekening bank orang (vendor perorangan, karyawan, pelanggan) terenkripsi.

Odoo 19.0 (`base/models/res_bank.py`):
- `acc_number = fields.Char(required=True, search='_search_acc_number')`; `_search_acc_number` mengubah domain menjadi
  `('sanitized_acc_number', operator, sanitize(value))`.
- `sanitized_acc_number = fields.Char(compute='_compute_sanitized_acc_number', readonly=True, store=True)` +
  `models.Constraint('unique(sanitized_acc_number, partner_id)')`; `create`/`write` memanggil `_sanitize_vals(vals)`
  yang MENGISI `vals['sanitized_acc_number']` (plaintext) langsung.
- `acc_holder_name = fields.Char(compute='_compute_account_holder_name', readonly=False, store=True)` (= nama partner).
- `account` mendefinisikan ulang `acc_number`/`acc_holder_name` (`tracking=True`) -> kelas field dipulihkan
  `efn_pii_account` (R-T18).

Perlakuan untuk rekening terlindungi (`efn_pii_protected`, satu arah):
- `acc_number`, `acc_holder_name` = E (`efnpii2:`);
- `sanitized_acc_number` = **blind index** (hex HMAC subkunci `acc_number`, normalisasi = `sanitize_account_number`)
  -> constraint unik (nomor, partner) tetap berlaku, pencarian `acc_number =`/`in`/nomor lengkap tetap jalan.
Aturan: terlindungi bila partner pemegang terlindungi ATAU partner bukan perusahaan (termasuk work contact karyawan
& partner user internal yang K-a: nama tetap plaintext, rekeningnya data pribadi K). Rekening perusahaan (6 entitas
EFN, vendor berbadan usaha) tetap plaintext (jurnal bank, pembayaran).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL

from . import keyring as kr
from . import pii_model as pm
from .fields_pii import PROTECTED_FIELD, EfnBidxChar, EfnPiiChar, blind_index_value, keyring_for, raise_locked

PII_FIELDS = ("acc_number", "acc_holder_name")
TRACKING_FIELDS = (*PII_FIELDS, "sanitized_acc_number")


class ResPartnerBank(models.Model):
    _inherit = "res.partner.bank"

    _efn_pii_fields = PII_FIELDS
    _efn_pii_bidx_self = {"sanitized_acc_number": "acc_number"}

    efn_pii_protected = fields.Boolean(
        string="EFN: Rekening Terlindungi",
        readonly=True,
        copy=False,
        index=True,
        help="Satu arah (ADR-036 Q-PII9): nomor & nama pemegang rekening disimpan terenkripsi; nomor tersanitasi "
        "disimpan sebagai blind index.",
    )
    acc_number = EfnPiiChar()
    acc_holder_name = EfnPiiChar()
    sanitized_acc_number = EfnBidxChar()

    # ------------------------------------------------------------------ registry / baca
    def _register_hook(self):
        res = super()._register_hook()
        pm.check_registry(self)
        return res

    def _fetch_query(self, query, fields):
        return pm.scan_fetch(self, lambda: super(ResPartnerBank, self)._fetch_query(query, fields))

    # ------------------------------------------------------------------ aturan
    @api.model
    def _efn_pii_partner_rule(self, partner):
        partner = partner.sudo()
        if not partner:
            return False
        return bool(partner[PROTECTED_FIELD]) or not partner.is_company

    @api.model
    def _efn_pii_vals_protected(self, vals):
        if vals.get(PROTECTED_FIELD):
            return True
        partner_id = vals.get("partner_id", self.env.context.get("default_partner_id"))
        if not isinstance(partner_id, int) or not partner_id:
            return False
        return self._efn_pii_partner_rule(self.env["res.partner"].browse(partner_id).exists())

    # ------------------------------------------------------------------ sanitized = blind index
    def _sanitize_vals(self, vals):
        super()._sanitize_vals(vals)
        if not vals.get("sanitized_acc_number"):
            return
        protected = all(self.sudo().mapped(PROTECTED_FIELD)) if self else bool(vals.get(PROTECTED_FIELD))
        if protected:
            vals["sanitized_acc_number"] = blind_index_value(self.env, "acc_number", vals.get("acc_number") or "")

    @api.depends("acc_number", PROTECTED_FIELD)
    def _compute_sanitized_acc_number(self):
        protected = self.filtered(lambda bank: bank.sudo()[PROTECTED_FIELD])
        super(ResPartnerBank, self - protected)._compute_sanitized_acc_number()
        for bank in protected:
            value = bank.acc_number
            if value == kr.PLACEHOLDER:
                raise_locked(self.env, keyring_for(self.env))
            bank.sanitized_acc_number = blind_index_value(self.env, "acc_number", value or "")

    # ------------------------------------------------------------------ create / write
    @api.model
    def _create(self, data_list):
        return pm.created(super()._create(data_list))

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        any_protected = False
        for vals in vals_list:
            pm.check_markers(self, vals)
            if self._efn_pii_vals_protected(vals):
                vals[PROTECTED_FIELD] = True
                any_protected = True
            else:
                vals.pop(PROTECTED_FIELD, None)
        if any_protected:
            broken = pm.broken_fields(self)
            if broken:
                pm.refuse_broken(self, broken)
            pm.require_keys(self.env)
        records = super().create(vals_list)
        pm.bind_new_records(records)
        return records

    def write(self, vals):
        if not self:
            return super().write(vals)
        pm.check_markers(self, vals)
        flags = self.sudo().mapped(PROTECTED_FIELD)
        if PROTECTED_FIELD in vals and not vals[PROTECTED_FIELD] and any(flags):
            raise UserError(_("Penanda rekening terlindungi bersifat satu arah."))
        becoming = self.browse()
        if "partner_id" in vals and vals["partner_id"]:
            partner = self.env["res.partner"].browse(vals["partner_id"]).exists()
            if self._efn_pii_partner_rule(partner):
                becoming = self.browse([bank.id for bank, flag in zip(self, flags, strict=True) if not flag])
        if any(flags) or becoming:
            broken = pm.broken_fields(self)
            if broken and (becoming or pm.vals_touch_broken(self, broken, vals)):
                pm.refuse_broken(self, broken)
        if becoming:
            becoming._efn_pii_protect()
            flags = self.sudo().mapped(PROTECTED_FIELD)
        if any(name in vals for name in ("acc_number", "sanitized_acc_number")) and any(flags) and not all(flags):
            # nilai tersanitasi berbeda per kelas (plaintext vs blind index): tulis per kelompok
            protected = self.browse([bank.id for bank, flag in zip(self, flags, strict=True) if flag])
            super(ResPartnerBank, protected).write(dict(vals))
            return super(ResPartnerBank, self - protected).write(dict(vals))
        return super().write(vals)

    def _efn_pii_protect(self):
        """Jadikan terlindungi: nilai lama dienkripsi di SQL dulu, tracking diredaksi, lalu penanda (compute
        `sanitized_acc_number` menjadi blind index)."""
        todo = self.sudo().filtered(lambda bank: not bank[PROTECTED_FIELD])
        if not todo:
            return
        pm.require_keys(self.env)
        pm.encrypt_existing(todo)
        pm.redact_tracking(self.env, self._name, todo.ids, TRACKING_FIELDS)
        todo.env.cr.execute(SQL("UPDATE res_partner_bank SET efn_pii_protected = TRUE WHERE id IN %s", tuple(todo.ids)))
        todo.invalidate_recordset([PROTECTED_FIELD])
        self.env.add_to_compute(self._fields["sanitized_acc_number"], todo)
        todo.flush_recordset(["sanitized_acc_number"])

    @api.model
    def _efn_pii_protect_for_partners(self, partners):
        if not partners:
            return
        banks = (
            self.sudo()
            .with_context(active_test=False)
            .search([("partner_id", "in", partners.ids), (PROTECTED_FIELD, "=", False)])
        )
        banks._efn_pii_protect()

    @api.model
    def _efn_pii_migrate(self):
        """DB lama (rekening dibuat sebelum 19.0.3): tandai & enkripsi rekening orang. Shell Superadmin:
        `env['res.partner.bank'].sudo()._efn_pii_migrate(); env.cr.commit()`. :returns: jumlah."""
        banks = self.sudo().with_context(active_test=False).search([(PROTECTED_FIELD, "=", False)])
        todo = banks.filtered(lambda bank: self._efn_pii_partner_rule(bank.partner_id))
        todo._efn_pii_protect()
        return len(todo)
