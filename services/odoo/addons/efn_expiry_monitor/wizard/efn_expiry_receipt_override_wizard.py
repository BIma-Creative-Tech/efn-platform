"""Wizard override sisa umur penerimaan outlet (fit-gap S-10, Q-SM9): alasan wajib, teraudit efn.expiry.event."""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

REASON_MIN = 10


class EfnExpiryReceiptOverrideWizard(models.TransientModel):
    _name = "efn.expiry.receipt.override.wizard"
    _description = "EFN wizard override sisa umur penerimaan"

    picking_id = fields.Many2one("stock.picking", required=True, readonly=True)
    reason = fields.Text(string="Alasan override", required=True)
    summary = fields.Text(compute="_compute_summary", readonly=True)

    @api.depends("picking_id")
    def _compute_summary(self):
        for wiz in self:
            rows = wiz.picking_id._efn_short_life_violations()
            wiz.summary = wiz.picking_id._efn_short_life_text(rows) if rows else _("Tidak ada pelanggaran.")

    def action_confirm(self):
        """Catat override (grup Markdown Manager / Inventory Administrator + akses baca picking + company)."""
        self.ensure_one()
        picking = self.picking_id
        if not picking._efn_can_override_short_life():
            raise AccessError(_("efn_expiry:override_role — hanya Markdown Manager / Inventory Administrator."))
        picking.check_access("read")
        if picking.company_id not in self.env.user.company_ids:
            raise AccessError(_("efn_expiry:company — transfer PT lain."))
        reason = " ".join((self.reason or "").split())
        if len(reason) < REASON_MIN:
            raise UserError(_("efn_expiry:reason — alasan override minimal %s karakter.", REASON_MIN))
        rows = picking._efn_short_life_violations()
        if not picking._efn_short_life_uncovered():
            raise UserError(_("efn_expiry:nothing_to_override — tidak ada lot di bawah sisa umur minimum."))
        first_ml = rows[0][0]
        event = self.env["efn.expiry.event"]._efn_log(
            company_id=picking.company_id.id,
            kind="receipt_override",
            warehouse_id=first_ml.location_dest_id.warehouse_id.id,
            picking_id=picking.id,
            lot_id=first_ml.lot_id.id if len(rows) == 1 else False,
            product_id=first_ml.product_id.id if len(rows) == 1 else False,
            qty=sum(ml.quantity for ml, _left, _min in rows),
            reason=reason[:200],
            note=picking._efn_short_life_text(rows),
            # SEC-220: override hanya mencakup lot yang dievaluasi sekarang (termasuk yang sudah di-override).
            lot_ids=[(6, 0, sorted({ml.lot_id.id for ml, _left, _min in rows}))],
        )
        picking.sudo().write({"efn_short_life_override_id": event.id})
        picking.message_post(body=_("Override sisa umur penerimaan dicatat oleh %s.", self.env.user.name))
        return {"type": "ir.actions.act_window_close"}
