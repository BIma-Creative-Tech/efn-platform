"""Akun jurnal akrual Poin EFN per PT (docs/04 §13.1 no. 6, docs/08 §11.4 no. 4).

Semua akun = PLACEHOLDER yang dipilih Finance per PT (ADR-027: tanpa kode akun di modul). Tidak ada default
otomatis: tanpa akun yang dibutuhkan, cron akrual mencatat galat + aktivitas Finance dan TIDAK membuat jurnal.
Saran awal liabilitas = akun ``21990091`` *Liabilitas / Kliring Poin EFN* yang dibuat ``efn_setup_pos`` (akun
``outstanding_account_id`` metode bayar Poin EFN), agar penukaran POS (§9.2) dan akrual memakai akun yang sama dan
rekonsiliasi saldo bermakna.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

LIABILITY_TYPES = ("liability_current", "liability_non_current")
EXPENSE_TYPES = ("expense", "expense_direct_cost", "expense_other")
CLEARING_TYPES = ("asset_current", "asset_receivable", "liability_current")
INCOME_TYPES = ("income", "income_other")
PAYABLE_TYPES = ("liability_current", "liability_payable")


class ResCompany(models.Model):
    _inherit = "res.company"

    efn_loyalty_liability_account_id = fields.Many2one(
        "account.account",
        string="Akun Liabilitas Poin EFN",
        copy=False,
        help="Cr saat earn, Dr saat clawback/kedaluwarsa. Disarankan sama dengan akun outstanding metode bayar "
        "Poin EFN PT ini (21990091 PLACEHOLDER) agar penukaran POS mengurangi akun yang sama.",
    )
    efn_loyalty_expense_account_id = fields.Many2one(
        "account.account",
        string="Akun Beban Program Poin",
        copy=False,
        help="Dr saat earn (poin diterbitkan PT ini), Cr saat clawback refund.",
    )
    efn_loyalty_clearing_account_id = fields.Many2one(
        "account.account",
        string="Akun Piutang Poin Antar-PT (kliring)",
        copy=False,
        help="PT PENEBUS: Dr saat pelanggan menukar di PT ini poin yang diterbitkan PT lain (PT penerbit "
        "menanggung; settlement antar-PT W-15); Cr saat penukaran itu dikembalikan lewat refund POS di PT ini (L-68).",
    )
    efn_loyalty_interco_payable_account_id = fields.Many2one(
        "account.account",
        string="Akun Utang Poin Antar-PT",
        copy=False,
        help="L-66, PT PENERBIT: Cr saat poin terbitan PT ini ditukar di PT lain (Dr Liabilitas Poin); Dr saat "
        "penukaran itu dikembalikan (restore). Lawan akun Piutang Poin Antar-PT di PT penebus (settlement W-15).",
    )
    efn_loyalty_breakage_account_id = fields.Many2one(
        "account.account",
        string="Akun Pendapatan Poin Kedaluwarsa",
        copy=False,
        help="L-67 (PT penerbit): Cr saat poin kedaluwarsa (breakage) dan saat poin hangus karena penghapusan data "
        "(DSAR) / penyesuaian ledger BFF; lawan Dr Liabilitas Poin. Wajib bila ada nilai tersebut (tanpa akun -> "
        "akrual missing_config).",
    )
    efn_loyalty_journal_id = fields.Many2one(
        "account.journal",
        string="Jurnal Akrual Poin EFN",
        copy=False,
        help="Jurnal Miscellaneous untuk akrual harian. Kosong = jurnal Miscellaneous pertama PT ini.",
    )

    @api.constrains(
        "efn_loyalty_liability_account_id",
        "efn_loyalty_expense_account_id",
        "efn_loyalty_clearing_account_id",
        "efn_loyalty_breakage_account_id",
        "efn_loyalty_interco_payable_account_id",
        "efn_loyalty_journal_id",
    )
    def _check_efn_loyalty_accounts(self):
        checks = (
            ("efn_loyalty_liability_account_id", LIABILITY_TYPES),
            ("efn_loyalty_expense_account_id", EXPENSE_TYPES),
            ("efn_loyalty_clearing_account_id", CLEARING_TYPES),
            ("efn_loyalty_interco_payable_account_id", PAYABLE_TYPES),
            ("efn_loyalty_breakage_account_id", INCOME_TYPES),
        )
        for company in self.sudo():
            for field_name, types in checks:
                account = company[field_name]
                if not account:
                    continue
                label = company._fields[field_name].string
                if company not in account.company_ids:
                    raise ValidationError(_("%(field)s harus akun milik company ini.", field=label))
                if account.account_type not in types:
                    allowed = ", ".join(types)
                    raise ValidationError(
                        _("%(field)s: tipe akun tidak sesuai (diizinkan: %(types)s).", field=label, types=allowed)
                    )
            journal = company.efn_loyalty_journal_id
            if journal and (journal.company_id != company or journal.type != "general"):
                raise ValidationError(_("Jurnal akrual poin harus jurnal Miscellaneous milik company ini."))

    def _efn_loyalty_bank_journal(self):
        """W-15: jurnal bank default settlement poin antar-PT (bank pertama PT ini; bisa diganti di dokumen)."""
        self.ensure_one()
        return (
            self.env["account.journal"]
            .sudo()
            .search([("company_id", "=", self.id), ("type", "=", "bank")], order="sequence, id", limit=1)
        )

    def _efn_loyalty_journal(self):
        self.ensure_one()
        company = self.sudo()
        if company.efn_loyalty_journal_id:
            return company.efn_loyalty_journal_id
        return (
            self.env["account.journal"]
            .sudo()
            .search([("company_id", "=", company.id), ("type", "=", "general")], order="sequence, id", limit=1)
        )
