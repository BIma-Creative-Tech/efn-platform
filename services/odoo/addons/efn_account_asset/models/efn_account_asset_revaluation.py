"""Riwayat revaluasi (PSAK 16 / IAS 16). Port `custom.fixed.asset.revaluation` (ADR-049). Dibuat wizard saja."""

from odoo import fields, models


class EfnAccountAssetRevaluation(models.Model):
    _name = "efn.account.asset.revaluation"
    _description = "EFN Asset Revaluation"
    _order = "revaluation_date desc, id desc"

    asset_id = fields.Many2one("efn.account.asset", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="asset_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="asset_id.currency_id", store=True)
    name = fields.Char(string="Referensi", readonly=True)
    revaluation_date = fields.Date(required=True)
    net_book_value_before = fields.Monetary(string="Nilai Buku Sebelum", currency_field="currency_id", readonly=True)
    new_value = fields.Monetary(string="Nilai Baru", currency_field="currency_id", readonly=True)
    revaluation_amount = fields.Monetary(
        string="Penyesuaian",
        currency_field="currency_id",
        readonly=True,
        help="Nilai baru dikurangi nilai buku sebelum. Positif = revaluasi naik.",
    )
    surplus_movement = fields.Monetary(string="Mutasi Surplus", currency_field="currency_id", readonly=True)
    pl_movement = fields.Monetary(string="Mutasi L/R", currency_field="currency_id", readonly=True)
    surplus_balance_after = fields.Monetary(string="Saldo Surplus Sesudah", currency_field="currency_id", readonly=True)
    loss_recognized_after = fields.Monetary(string="Rugi Diakui Sesudah", currency_field="currency_id", readonly=True)
    useful_life_before = fields.Integer(string="Umur Sebelum (bulan)", readonly=True)
    remaining_life_after = fields.Integer(string="Sisa Umur Sesudah (bulan)", readonly=True)
    surplus_account_id = fields.Many2one("account.account", string="Akun Surplus Revaluasi", readonly=True)
    loss_account_id = fields.Many2one("account.account", string="Akun Rugi Revaluasi", readonly=True)
    income_account_id = fields.Many2one("account.account", string="Akun Pendapatan Revaluasi", readonly=True)
    journal_id = fields.Many2one("account.journal", string="Jurnal", readonly=True)
    move_id = fields.Many2one("account.move", string="Jurnal Entri", readonly=True, copy=False)
    note = fields.Text()
