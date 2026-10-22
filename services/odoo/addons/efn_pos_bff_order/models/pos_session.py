"""Tutup sesi POS -> semua sesi meja QR config itu ditutup (docs/08 §13.1b "Siklus sesi meja")."""

from odoo import models


class PosSession(models.Model):
    _inherit = "pos.session"

    def write(self, vals):
        result = super().write(vals)
        if vals.get("state") == "closed":
            sessions = (
                self.env["efn.pos.table.session"]
                .sudo()
                .search([("config_id", "in", self.config_id.ids), ("state", "in", ("open", "expired"))])
            )
            for table_session in sessions:
                table_session._efn_close("paid" if table_session._efn_paid_orders() else "closed_by_staff")
        return result
