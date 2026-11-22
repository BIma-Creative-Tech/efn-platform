"""Ringkasan akun reconcilable yang masih punya item terbuka, PER COMPANY (port ADR-049 dari
`custom_account_reconcile/models/reconcile_overview.py`).

Perubahan wajib (G58 §3.1): sumber menjumlah semua company dalam satu baris per akun (nilai PT lain bocor). EFN:
`GROUP BY ml.company_id, aa.id`, kolom `company_id` + record rule company, mata uang = mata uang company baris,
`id` = id item terbuka terkecil kelompok (stabil selama item itu masih terbuka, unik antar-kelompok).
"""

from odoo import fields, models, tools
from odoo.tools import SQL


class EfnReconcileAccount(models.Model):
    _name = "efn.reconcile.account"
    _description = "EFN Ringkasan Rekonsiliasi Akun"
    _auto = False
    _rec_name = "account_id"
    _order = "company_id, account_id"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", string="Company", readonly=True)
    account_id = fields.Many2one("account.account", string="Akun", readonly=True)
    currency_id = fields.Many2one("res.currency", string="Mata Uang", readonly=True)
    line_count = fields.Integer(string="Item Terbuka", readonly=True)
    debit = fields.Monetary(string="Debit", currency_field="currency_id", readonly=True)
    credit = fields.Monetary(string="Kredit", currency_field="currency_id", readonly=True)
    residual = fields.Monetary(
        string="Sisa",
        currency_field="currency_id",
        readonly=True,
        help="Jumlah residual item terbuka (mata uang company). Nol = semua saling meniadakan, tinggal dicocokkan.",
    )
    oldest_date = fields.Date(string="Item Terbuka Tertua", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            SQL(
                """
            CREATE OR REPLACE VIEW %s AS (
                SELECT
                    MIN(ml.id)               AS id,
                    ml.company_id            AS company_id,
                    aa.id                    AS account_id,
                    rc.currency_id           AS currency_id,
                    COUNT(ml.id)             AS line_count,
                    SUM(ml.debit)            AS debit,
                    SUM(ml.credit)           AS credit,
                    SUM(ml.amount_residual)  AS residual,
                    MIN(ml.date)             AS oldest_date
                FROM account_move_line ml
                JOIN account_account aa ON aa.id = ml.account_id
                JOIN res_company rc ON rc.id = ml.company_id
                WHERE aa.reconcile
                  AND NOT ml.reconciled
                  AND ml.parent_state = 'posted'
                GROUP BY ml.company_id, aa.id, rc.currency_id
            )
            """,
                SQL.identifier(self._table),
            )
        )

    def action_open_lines(self):
        """Buka item terbuka akun ini di company baris (ACL + record rule CE berlaku; tanpa sudo)."""
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": self.account_id.display_name,
            "res_model": "account.move.line",
            "view_mode": "list",
            "views": [(False, "list")],
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("account_id", "=", self.account_id.id),
                ("reconciled", "=", False),
                ("parent_state", "=", "posted"),
            ],
            "context": {"search_default_group_by_partner": 1, "create": 0},
        }
