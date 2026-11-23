"""Penanda entri jurnal kas kecil (smart button, saldo fund, rekonsiliasi settle).

Port dari `custom_petty_cash/models/account_move.py` (ADR-049) + `efn_petty_cash_fund_id` (baru).
"""

from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    efn_petty_cash_request_id = fields.Many2one(
        "efn.petty.cash.request",
        string="Pengajuan Kas Kecil",
        index="btree_not_null",
        copy=False,
        readonly=True,
    )
    efn_petty_cash_realization_id = fields.Many2one(
        "efn.petty.cash.realization",
        string="Realisasi Kas Kecil",
        index="btree_not_null",
        copy=False,
        readonly=True,
    )
    efn_petty_cash_fund_id = fields.Many2one(
        "efn.petty.cash.fund",
        string="Dana Kas Kecil",
        index="btree_not_null",
        copy=False,
        readonly=True,
    )
