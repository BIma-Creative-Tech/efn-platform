"""Sidik baris impor untuk dedup antar-file (G58 §4.2 butir 2). CE 19 tidak punya `unique_import_id` (Enterprise)."""

from odoo import fields, models


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    efn_import_fingerprint = fields.Char(
        string="EFN: Sidik Impor",
        index=True,
        copy=False,
        readonly=True,
        help="sha256(jurnal | tanggal | nominal | narasi | saldo | kemunculan ke-n dalam file). Baris dengan sidik "
        "yang sudah ada di jurnal yang sama dilewati saat impor berikutnya.",
    )
    efn_import_log_id = fields.Many2one(
        "efn.bank.import.log", string="EFN: Log Impor", readonly=True, copy=False, index="btree_not_null"
    )
