"""G60: parameter per PT (ambang persetujuan pembayaran, jurnal MDR, plan analitik Outlet)."""

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    efn_payment_approval_threshold = fields.Monetary(
        string="EFN: ambang persetujuan pembayaran keluar",
        currency_field="currency_id",
        default=0.0,
        help="Pembayaran keluar (vendor) dengan nilai >= ambang wajib disetujui Accounting Manager lain (4-eyes) "
        "sebelum dikonfirmasi. 0 = nonaktif (Q-G60-1, diisi Finance).",
    )
    efn_mdr_journal_id = fields.Many2one(
        "account.journal",
        string="EFN: jurnal estimasi MDR",
        domain="[('company_id', '=', id), ('type', '=', 'general')]",
        help="Jurnal umum untuk estimasi biaya MDR saat tutup sesi POS. Kosong = jurnal umum pertama PT.",
    )
    efn_outlet_analytic_plan_id = fields.Many2one(
        "account.analytic.plan",
        string="EFN: plan analitik Outlet",
        help="Diisi wizard 'Siapkan Analitik Outlet'. Bila terisi, sesi POS yang gudangnya belum terpetakan ke akun "
        "analitik Outlet diberi peringatan di chatter.",
    )

    def _efn_mdr_journal(self):
        self.ensure_one()
        if self.efn_mdr_journal_id:
            return self.efn_mdr_journal_id
        return self.env["account.journal"].search(
            [("company_id", "=", self.id), ("type", "=", "general")], order="sequence, id", limit=1
        )
