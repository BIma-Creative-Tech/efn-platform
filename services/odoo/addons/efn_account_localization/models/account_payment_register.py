"""G60-A: wizard Register Payment menahan pembayaran keluar di atas ambang (draft + ``to_approve``).

CE 19 ``_create_payments`` = ``_init_payments`` -> ``_post_payments`` -> ``_reconcile_payments``; payment yang tidak
di-post tidak direkonsiliasi (filter ``parent_state = posted``) tetapi tetap ditautkan ke dokumen lewat
``matched_payment_ids``. Rekonsiliasi dilakukan saat disetujui (``account.payment._efn_reconcile_matched_moves``).
"""

from odoo import models


class AccountPaymentRegister(models.TransientModel):
    _inherit = "account.payment.register"

    def _post_payments(self, to_process, edit_mode=False):
        held = self.env["account.payment"]
        for vals in to_process:
            if vals["payment"]._efn_needs_approval():
                held |= vals["payment"]
        if held:
            # CE 19 `create` dengan `write_off_line_vals` (selalu ada di mode edit wizard) memanggil
            # `_generate_journal_entry` yang langsung menulis state `in_process` (jurnal masih draft), tanpa lewat
            # `action_post`. Kembalikan ke draft; jurnal draft di-post saat disetujui (`action_post` -> write state).
            held.filtered(lambda p: p.state != "draft").write({"state": "draft"})
            held._efn_set_to_approve()
        remaining = [vals for vals in to_process if vals["payment"] not in held]
        return super()._post_payments(remaining, edit_mode=edit_mode)
