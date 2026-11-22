"""Jenis kas kecil / uang muka per PT: peta akun, jurnal, dan plafon.

Port dari odoo-platform `custom_petty_cash/models/petty_cash_type.py` (ADR-049). Perubahan: rantai fallback ke
`res.company` dibuang (K-PC4); resolusi akun/jurnal terpusat di sini dan **fail-closed** (UserError jelas bila belum
dikonfigurasi — akun uang muka adalah placeholder yang wajib dibuat Finance, Q-PC1).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

FORBIDDEN_ADVANCE_TYPES = ("asset_cash", "liability_credit_card")


class EfnPettyCashType(models.Model):
    _name = "efn.petty.cash.type"
    _description = "EFN Jenis Kas Kecil / Uang Muka"
    _order = "company_id, sequence, id"
    _check_company_auto = True

    name = fields.Char(string="Nama", required=True, translate=True)
    code = fields.Char(string="Kode", required=True, help="Kode singkat, mis. KK (kas kecil) / UM (uang muka).")
    kind = fields.Selection(
        [
            ("petty_cash", "Kas Kecil Outlet (imprest)"),
            ("cash_advance", "Uang Muka Karyawan"),
            ("other", "Lainnya"),
        ],
        string="Jenis",
        required=True,
        default="cash_advance",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, index=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")

    advance_account_id = fields.Many2one(
        "account.account",
        string="Akun Uang Muka",
        check_company=True,
        domain="[('reconcile', '=', True), ('account_type', 'not in', ('asset_cash', 'liability_credit_card'))]",
        help="Akun aset lancar reconcilable (placeholder: dibuat Finance, Q-PC1). Didebit saat pencairan, "
        "dikredit saat realisasi/pengembalian. Bukan akun kas.",
    )
    bank_out_journal_id = fields.Many2one(
        "account.journal",
        string="Jurnal Bank Keluar",
        check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
    )
    payment_journal_id = fields.Many2one(
        "account.journal",
        string="Jurnal Pelunasan Tagihan",
        check_company=True,
        domain="[('type', '=', 'general')]",
        help="Jurnal umum untuk entri pelunasan tagihan vendor dari uang muka (Dr Utang Usaha / Cr Uang Muka). "
        "Wajib bila ada realisasi pihak ketiga.",
    )
    expense_journal_id = fields.Many2one(
        "account.journal",
        string="Jurnal Beban",
        check_company=True,
        domain="[('type', '=', 'general')]",
    )
    sequence_id = fields.Many2one(
        "ir.sequence",
        string="Penomoran Dokumen",
        copy=False,
        help="Kosong = sequence umum efn.petty.cash.request.",
    )

    limit_enforcement = fields.Selection(
        [("off", "Mati"), ("warn", "Peringatan"), ("block", "Blokir")],
        string="Penegakan Plafon",
        required=True,
        default="off",
    )
    limit_per_request = fields.Monetary(string="Plafon per Pengajuan", currency_field="currency_id")
    limit_outstanding = fields.Monetary(
        string="Plafon Outstanding per Pemegang",
        currency_field="currency_id",
        help="Total uang muka terbuka per pemegang untuk jenis ini. 0 = tanpa batas.",
    )
    max_open_requests = fields.Integer(string="Maks. Pengajuan Terbuka", help="0 = tanpa batas.")
    block_when_overdue = fields.Boolean(string="Blokir bila Ada yang Lewat Tenggat")
    realization_days = fields.Integer(
        string="Tenggat Realisasi (hari)",
        help="0 = parameter efn_petty_cash.realization_days (default 14).",
    )
    allow_third_party = fields.Boolean(string="Izinkan Realisasi Pihak Ketiga", default=True)
    is_default = fields.Boolean(string="Default")
    request_count = fields.Integer(compute="_compute_request_count")

    _code_company_uniq = models.Constraint("unique(code, company_id)", "Kode jenis harus unik per company.")

    @api.depends("code", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"[{rec.code}] {rec.name}" if rec.code else rec.name

    def _compute_request_count(self):
        counts = dict(
            self.env["efn.petty.cash.request"]._read_group(
                [("advance_type_id", "in", self.ids)],
                groupby=["advance_type_id"],
                aggregates=["__count"],
            )
        )
        for rec in self:
            rec.request_count = counts.get(rec, 0)

    @api.constrains("is_default", "company_id")
    def _check_single_default(self):
        for rec in self.filtered("is_default"):
            domain = [("id", "!=", rec.id), ("company_id", "=", rec.company_id.id), ("is_default", "=", True)]
            if self.search_count(domain, limit=1):
                raise UserError(_("Sudah ada jenis default untuk company %s.", rec.company_id.name))

    # ------------------------------------------------------------ resolusi (fail-closed)
    def _efn_advance_account(self, override=None):
        self.ensure_one()
        account = override or self.advance_account_id
        if not account:
            raise UserError(
                _(
                    "Akun uang muka untuk jenis %(type)s (%(company)s) belum dikonfigurasi. Finance wajib "
                    "membuat akun aset lancar yang dapat direkonsiliasi (mis. 'Uang Muka Kas Kecil' / "
                    "'Uang Muka Karyawan') lalu mengisinya di Kas Kecil > Konfigurasi > Jenis.",
                    type=self.display_name,
                    company=self.company_id.name,
                )
            )
        if not account.reconcile or account.account_type in FORBIDDEN_ADVANCE_TYPES:
            raise UserError(
                _(
                    "Akun %s tidak bisa dipakai sebagai akun uang muka: harus dapat direkonsiliasi dan "
                    "bukan akun kas/bank.",
                    account.display_name,
                )
            )
        return account

    def _efn_bank_journal(self, currency, override=None):
        self.ensure_one()
        journal = override or self.bank_out_journal_id
        if not journal:
            raise UserError(_("Jurnal bank keluar untuk jenis %s belum dikonfigurasi.", self.display_name))
        if not journal.default_account_id:
            raise UserError(_("Jurnal %s belum punya akun bank/kas.", journal.name))
        if journal.currency_id and journal.currency_id != currency:
            raise UserError(
                _(
                    "Jurnal %(journal)s hanya menerima %(jcur)s, dokumen ini dalam %(cur)s.",
                    journal=journal.name,
                    jcur=journal.currency_id.name,
                    cur=currency.name,
                )
            )
        return journal

    def _efn_expense_journal(self):
        self.ensure_one()
        if not self.expense_journal_id:
            raise UserError(_("Jurnal beban untuk jenis %s belum dikonfigurasi.", self.display_name))
        return self.expense_journal_id

    def _efn_payment_journal(self):
        self.ensure_one()
        if not self.payment_journal_id:
            raise UserError(
                _(
                    "Jurnal pelunasan tagihan untuk jenis %s belum dikonfigurasi (wajib untuk pihak ketiga).",
                    self.display_name,
                )
            )
        return self.payment_journal_id

    def action_view_requests(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Pengajuan"),
            "res_model": "efn.petty.cash.request",
            "domain": [("advance_type_id", "=", self.id)],
            "view_mode": "list,form",
            "context": {"default_advance_type_id": self.id},
        }
