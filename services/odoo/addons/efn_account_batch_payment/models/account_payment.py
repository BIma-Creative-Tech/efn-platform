"""Tautan payment -> batch + aksi "Tambahkan ke Batch" (port ADR-049 dari
`custom_account_batch_payment/models/account_payment.py`).

Perubahan EFN: `efn_batch_payment_id` terkunci bila batch lama/baru bukan draft (SEC-G58-01); aksi recordset
`action_efn_create_batch` dengan cek grup Accounting/Bookkeeper + `check_access('write')`
+ company aktif + persetujuan G60 (bila field ada); rekening penerima ditampilkan ter-mask di list batch.
"""

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.efn_audit.models.audit_policy import mask_identifier

from .efn_account_batch_payment import SYSTEM_CTX, USER_GROUP, _efn_unapproved


class AccountPayment(models.Model):
    _inherit = "account.payment"

    efn_batch_payment_id = fields.Many2one(
        "efn.account.batch.payment",
        string="Batch Pembayaran",
        copy=False,
        index="btree_not_null",
        check_company=True,
    )
    efn_partner_bank_masked = fields.Char(string="Rekening Penerima", compute="_compute_efn_partner_bank_masked")

    @api.depends("partner_bank_id")
    def _compute_efn_partner_bank_masked(self):
        for payment in self:
            number = payment.partner_bank_id.acc_number
            payment.efn_partner_bank_masked = mask_identifier(number) if number else False

    def write(self, vals):
        # SEC-G58-01: payment tidak boleh masuk/keluar batch yang sudah divalidasi (juga lewat o2m batch.payment_ids).
        if "efn_batch_payment_id" in vals and not (self.env.su and self.env.context.get(SYSTEM_CTX)):
            batches = self.efn_batch_payment_id | self.env["efn.account.batch.payment"].browse(
                vals["efn_batch_payment_id"] or []
            )
            if any(batch.sudo().state != "draft" for batch in batches):
                raise UserError(_("Batch pembayaran sudah divalidasi; isinya terkunci. Reset batch ke draft dulu."))
        return super().write(vals)

    def action_efn_create_batch(self):
        """Bungkus payment posted terpilih menjadi batch baru (satu company, jurnal, arah)."""
        if not self.env.user.has_group(USER_GROUP):
            raise AccessError(_("Hanya Accounting / Bookkeeper yang dapat membuat batch pembayaran."))
        self.check_access("write")
        payments = self.filtered(lambda p: p.state in ("in_process", "paid"))
        if not payments:
            raise UserError(_("Pilih pembayaran yang sudah di-post."))
        if payments.company_id - self.env.companies:
            raise AccessError(_("Pembayaran milik company yang tidak aktif di sesi Anda."))
        if len(payments.journal_id) > 1:
            raise UserError(_("Semua pembayaran harus pada satu jurnal."))
        if len(set(payments.mapped("payment_type"))) > 1:
            raise UserError(_("Campuran pembayaran masuk dan keluar — pilih satu arah."))
        already = payments.filtered("efn_batch_payment_id")
        if already:
            raise UserError(
                _(
                    "Sudah ada di batch: %s",
                    ", ".join(f"{p.name} ({p.efn_batch_payment_id.name})" for p in already),
                )
            )
        unapproved = _efn_unapproved(payments)
        if unapproved:
            raise UserError(_("Pembayaran belum disetujui: %s", ", ".join(unapproved.mapped("name"))))
        batch = self.env["efn.account.batch.payment"].create(
            {
                "journal_id": payments.journal_id.id,
                "batch_type": payments[0].payment_type,
                "payment_ids": [Command.set(payments.ids)],
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "efn.account.batch.payment",
            "res_id": batch.id,
            "view_mode": "form",
            "views": [(False, "form")],
        }
