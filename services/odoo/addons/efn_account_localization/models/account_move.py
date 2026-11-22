from odoo import fields, models

ACCOUNTING_READERS = "account.group_account_readonly,account.group_account_manager"


class AccountMove(models.Model):
    _inherit = "account.move"

    # G60-B: jurnal estimasi MDR yang dibuat saat tutup sesi POS (pos.session.efn_mdr_move_ids).
    efn_mdr_pos_session_id = fields.Many2one(
        "pos.session",
        string="Sesi POS (estimasi MDR)",
        index="btree_not_null",
        copy=False,
        readonly=True,
        ondelete="restrict",
        groups=ACCOUNTING_READERS,
    )
