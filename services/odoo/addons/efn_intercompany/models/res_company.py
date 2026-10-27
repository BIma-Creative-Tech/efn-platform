"""Pengaturan antar-PT per company (docs/08 §5.2).

Sisi PENJUAL: ``efn_ic_so_from_po`` (PO PT lain -> SO otomatis), ``efn_ic_auto_confirm_so``, gudang SO.
Sisi PEMBELI: ``efn_ic_bill_from_invoice`` (faktur PT penjual diposting -> tagihan vendor DRAFT di PT ini).
Dokumen lawan dibuat sebagai ``efn_ic_user_id`` PT tujuan: user internal, SATU company, grup *User teknis antar-PT*
(hak Sales/Purchase/Billing/Stok). Bukan ``sudo()`` penuh -> ACL & record rule standar PT tujuan tetap berlaku.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    efn_ic_so_from_po = fields.Boolean(
        string="Buat SO dari PO PT lain",
        help="PO yang dikonfirmasi PT lain dengan vendor = PT ini otomatis menjadi Sales Order di PT ini.",
    )
    efn_ic_auto_confirm_so = fields.Boolean(string="Konfirmasi SO antar-PT otomatis")
    efn_ic_bill_from_invoice = fields.Boolean(
        string="Buat tagihan dari faktur PT lain",
        help="Faktur PT lain kepada PT ini yang diposting otomatis menjadi tagihan vendor DRAFT di PT ini, "
        "terhubung ke baris PO asal (3-way match standar).",
    )
    efn_ic_user_id = fields.Many2one("res.users", string="User teknis antar-PT")
    efn_ic_warehouse_id = fields.Many2one(
        "stock.warehouse", string="Gudang SO antar-PT", help="Gudang pengirim SO antar-PT (mis. Central Kitchen)."
    )

    @api.constrains("efn_ic_user_id")
    def _check_efn_ic_user(self):
        group = self.env.ref("efn_intercompany.group_ic_technical", raise_if_not_found=False)
        for company in self.filtered("efn_ic_user_id"):
            user = company.efn_ic_user_id
            if user.share or not user.active:
                raise ValidationError(_("efn_intercompany:ic_user_internal — user teknis harus user internal aktif."))
            if group and group not in user.all_group_ids:
                raise ValidationError(
                    _("efn_intercompany:ic_user_group — user teknis wajib punya grup 'User teknis antar-PT'.")
                )
            if user.company_ids != company:
                raise ValidationError(
                    _(
                        "efn_intercompany:ic_user_company — user teknis %(user)s harus hanya punya akses ke "
                        "%(company)s.",
                        user=user.login,
                        company=company.name,
                    )
                )

    @api.constrains("efn_ic_warehouse_id")
    def _check_efn_ic_warehouse(self):
        for company in self.filtered("efn_ic_warehouse_id"):
            if company.efn_ic_warehouse_id.company_id != company:
                raise ValidationError(_("efn_intercompany:ic_warehouse — gudang SO antar-PT harus milik PT yang sama."))

    @api.model
    def _efn_ic_company_of_partner(self, partner):
        """PT yang partner-nya = commercial partner ``partner`` (kosong bila bukan PT grup)."""
        if not partner:
            return self.browse()
        return self.sudo().search([("partner_id", "=", partner.commercial_partner_id.id)], limit=1)

    def _efn_ic_env(self):
        """Environment user teknis PT ini (hanya company ini aktif)."""
        self.ensure_one()
        user = self.sudo().efn_ic_user_id
        if not user:
            raise ValidationError(
                _(
                    "efn_intercompany:no_ic_user — %(company)s belum punya user teknis antar-PT "
                    "(Settings > Companies > tab Antar-PT EFN).",
                    company=self.name,
                )
            )
        return self.env(user=user, context=dict(self.env.context, allowed_company_ids=[self.id]))
