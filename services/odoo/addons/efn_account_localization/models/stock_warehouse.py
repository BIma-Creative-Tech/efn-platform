from odoo import fields, models

from .account_move import ACCOUNTING_READERS


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    # G60-C (D-6): satu akun analitik plan Outlet per gudang outlet; dipakai semua pos.config gudang ini.
    efn_analytic_account_id = fields.Many2one(
        "account.analytic.account",
        string="Akun analitik Outlet",
        check_company=True,
        copy=False,
        ondelete="restrict",
        groups=ACCOUNTING_READERS,
        help="Laba-rugi jurnal sesi POS & default analitik PO gudang ini. Diisi wizard 'Siapkan Analitik Outlet'.",
    )
