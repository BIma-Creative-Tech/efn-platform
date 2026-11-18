"""Wizard override assortment per transfer (Assortment Manager, alasan wajib; L-150, Q-SM22)."""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..models.efn_assortment import GROUP_MANAGER, is_overridable


class EfnAssortmentOverrideWizard(models.TransientModel):
    _name = "efn.assortment.override.wizard"
    _description = "EFN wizard override assortment"

    picking_id = fields.Many2one("stock.picking", required=True, readonly=True)
    reason = fields.Text(string="Alasan override", required=True)
    summary = fields.Text(compute="_compute_summary", readonly=True)

    @api.depends("picking_id")
    def _compute_summary(self):
        for wiz in self:
            rows = []
            for move, outlet, reason in wiz.picking_id.move_ids._efn_assortment_violations():
                flag = _("bisa di-override") if is_overridable(reason) else _("TIDAK bisa di-override")
                rows.append(f"{move.product_id.display_name} -> {outlet.code}: {reason} ({flag})")
            wiz.summary = "\n".join(rows) or _("Tidak ada pelanggaran.")

    def action_confirm(self):
        """Buat override untuk pelanggaran yang boleh di-override. Cek grup + akses picking + company."""
        self.ensure_one()
        if not self.env.user.has_group(GROUP_MANAGER):
            raise AccessError(_("efn_assortment:manager_only — hanya Assortment Manager."))
        picking = self.picking_id
        picking.check_access("read")
        if picking.company_id not in self.env.user.company_ids:
            raise AccessError(_("efn_assortment:company — transfer PT lain."))
        reason = (self.reason or "").strip()
        if len(reason) < 10:
            raise UserError(_("efn_assortment:reason — alasan override minimal 10 karakter."))
        violations = picking.move_ids._efn_assortment_violations()
        allowed = [(m, o, r) for m, o, r in violations if is_overridable(r)]
        if not allowed:
            raise UserError(_("efn_assortment:nothing_to_override — tidak ada pelanggaran yang bisa di-override."))
        Override = self.env["efn.assortment.override"].sudo()
        for move, outlet, code in allowed:
            Override.create(
                {
                    "company_id": picking.company_id.id,
                    "picking_id": picking.id,
                    "warehouse_id": outlet.id,
                    "product_id": move.product_id.id,
                    "reason_code": code,
                    "reason": reason,
                    "user_id": self.env.uid,
                }
            )
        blocked = len(violations) - len(allowed)
        if blocked:
            picking.message_post(
                body=_("Override assortment dicatat; %s baris tetap terblokir (izin alkohol / non-halal).", blocked)
            )
        else:
            picking.message_post(body=_("Override assortment dicatat oleh %s.", self.env.user.name))
        return {"type": "ir.actions.act_window_close"}
