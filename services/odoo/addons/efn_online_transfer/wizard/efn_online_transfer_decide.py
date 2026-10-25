"""G26 §17.5: wizard keputusan Verifikator (setujui / tolak bukti transfer)."""

from odoo import fields, models

from ..models.efn_online_transfer_proof import REJECT_REASONS


class EfnOnlineTransferDecide(models.TransientModel):
    _name = "efn.online.transfer.decide"
    _description = "EFN Online - Keputusan bukti transfer"

    proof_id = fields.Many2one("efn.online.transfer.proof", required=True, readonly=True, ondelete="cascade")
    decision = fields.Selection([("approve", "Setujui"), ("reject", "Tolak")], required=True, default="approve")
    currency_id = fields.Many2one(related="proof_id.currency_id")
    amount_due = fields.Monetary(related="proof_id.amount_due")
    bank_date = fields.Date(string="Tanggal mutasi")
    bank_amount = fields.Monetary(string="Nominal mutasi")
    bank_ref = fields.Char(string="Referensi mutasi", size=64)
    reject_reason = fields.Selection(REJECT_REASONS, string="Alasan penolakan")
    note = fields.Text(string="Catatan internal", help="Tidak dikirim ke pelanggan.")

    def action_confirm(self):
        self.ensure_one()
        self.proof_id._efn_decide(
            self.decision,
            reason=self.reject_reason,
            bank_date=self.bank_date,
            bank_amount=self.bank_amount,
            bank_ref=self.bank_ref,
            note=self.note,
        )
        return {"type": "ir.actions.act_window_close"}
