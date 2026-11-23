"""G60-B: tarif MDR per metode bayar POS (bank: EDC/QRIS), berlaku per tanggal.

Ditulis ulang dari ide `levis.mdr.bin._compute_mdr` (persen + tetap) dan effective dating (odoo-platform
`custom_levis_localization/models/levis_mdr_bin.py`); TIDAK ada pemetaan BIN kartu (kasir EFN memakai EDC berdiri
sendiri, K25-09/ADR-032; tidak menyimpan PAN). Jenis kartu/acquirer dibedakan dengan metode bayar terpisah (Q-G60-5).
Tarif = PLACEHOLDER sampai kontrak bank. G58 (rekonsiliasi) boleh membaca model ini sebagai tarif acuan.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

EXPENSE_TYPES = ("expense", "expense_direct_cost", "expense_other")


class EfnMdrRate(models.Model):
    _name = "efn.mdr.rate"
    _description = "EFN: tarif MDR per metode bayar POS"
    _order = "payment_method_id, date_start desc, id desc"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", string="Company", required=True, index=True, default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    payment_method_id = fields.Many2one(
        "pos.payment.method",
        string="Metode bayar POS",
        required=True,
        check_company=True,
        index=True,
        domain="[('journal_id.type', '=', 'bank')]",
    )
    name = fields.Char(compute="_compute_name", store=True)
    mdr_percent = fields.Float(string="MDR (%)", digits=(16, 4), help="PLACEHOLDER sampai kontrak bank (Q-G60-5).")
    mdr_fixed = fields.Monetary(string="Biaya tetap per transaksi", currency_field="currency_id")
    account_id = fields.Many2one(
        "account.account",
        string="Akun beban MDR",
        required=True,
        check_company=True,
        domain=f"[('account_type', 'in', {list(EXPENSE_TYPES)})]",
    )
    date_start = fields.Date(string="Berlaku mulai", required=True, default=fields.Date.context_today)
    date_end = fields.Date(string="Berlaku sampai")
    active = fields.Boolean(default=True)

    @api.depends("payment_method_id.name", "date_start")
    def _compute_name(self):
        for rate in self:
            rate.name = f"{rate.payment_method_id.name or ''} {rate.date_start or ''}".strip()

    @api.constrains("mdr_percent", "mdr_fixed", "date_start", "date_end")
    def _check_values(self):
        for rate in self:
            if not 0 <= rate.mdr_percent <= 100:
                raise ValidationError(_("MDR (%) harus 0-100."))
            if rate.mdr_fixed < 0:
                raise ValidationError(_("Biaya tetap MDR tidak boleh negatif."))
            if rate.date_end and rate.date_end < rate.date_start:
                raise ValidationError(_("Tanggal akhir tarif MDR sebelum tanggal mulai."))

    @api.constrains("payment_method_id")
    def _check_bank_method(self):
        for rate in self:
            if rate.payment_method_id.type != "bank":
                raise ValidationError(_("Tarif MDR hanya untuk metode bayar bank (EDC/QRIS)."))

    @api.constrains("payment_method_id", "date_start", "date_end", "active")
    def _check_overlap(self):
        for rate in self.filtered("active"):
            domain = [
                ("id", "!=", rate.id),
                ("payment_method_id", "=", rate.payment_method_id.id),
                "|",
                ("date_end", "=", False),
                ("date_end", ">=", rate.date_start),
            ]
            if rate.date_end:
                domain.append(("date_start", "<=", rate.date_end))
            if self.sudo().search_count(domain, limit=1):
                raise ValidationError(
                    _("Periode tarif MDR %s tumpang tindih dengan tarif lain.", rate.payment_method_id.name)
                )

    @api.model
    def _efn_rate_for(self, method, date):
        """Tarif berlaku untuk ``method`` pada ``date`` (difilter company metode; dipanggil sudo dari tutup sesi)."""
        return self.search(
            [
                ("company_id", "=", method.company_id.id),
                ("payment_method_id", "=", method.id),
                ("date_start", "<=", date),
                "|",
                ("date_end", "=", False),
                ("date_end", ">=", date),
            ],
            order="date_start desc, id desc",
            limit=1,
        )

    def _efn_compute(self, amount, count):
        """MDR = amount x % + biaya tetap x jumlah transaksi, dibulatkan presisi mata uang company."""
        self.ensure_one()
        return self.currency_id.round(amount * self.mdr_percent / 100.0 + self.mdr_fixed * count)

    @api.model
    def _efn_load_demo_rates(self):
        """Demo: tarif PLACEHOLDER untuk metode EDC/QRIS manual efn_setup_pos (tanpa dependency ke modul itu)."""
        data = (
            self.env["ir.model.data"]
            .sudo()
            .search(
                [
                    ("module", "=", "efn_setup_pos"),
                    ("model", "=", "pos.payment.method"),
                    "|",
                    ("name", "=like", "pos_payment_method_edc_%"),
                    ("name", "=like", "pos_payment_method_qris_manual_%"),
                ]
            )
        )
        Rate = self.sudo()
        for record in data:
            method = self.env["pos.payment.method"].sudo().browse(record.res_id).exists()
            if not method or method.type != "bank" or Rate.search_count([("payment_method_id", "=", method.id)]):
                continue
            company = method.company_id
            chart = self.env["account.chart.template"].sudo().with_context(allowed_company_ids=company.root_id.ids)
            account = chart.ref("l10n_id_65110020", raise_if_not_found=False)
            if not account:
                continue
            percent = 0.7 if record.name.startswith("pos_payment_method_qris") else 1.5
            Rate.create(
                {
                    "company_id": company.id,
                    "payment_method_id": method.id,
                    "mdr_percent": percent,
                    "account_id": account.id,
                    "date_start": "2026-01-01",
                }
            )
