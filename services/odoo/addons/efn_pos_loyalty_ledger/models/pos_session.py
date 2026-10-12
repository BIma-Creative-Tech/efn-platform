"""E-30: selisih tutup sesi untuk metode Poin EFN ditolak (docs/08 §9.2 "Tutup sesi").

Nilai Poin EFN sudah pasti (divalidasi terhadap reservasi BFF saat sync, toleransi 0); kasir tidak boleh
membukukan "selisih" (`bank_payment_method_diffs`) pada metode ini. `_validate_session` dilewati semua jalur
penutupan (UI POS `close_session_from_ui`, kontrol penutupan backend, wizard).
"""

from odoo import _, models
from odoo.exceptions import UserError


class PosSession(models.Model):
    _inherit = "pos.session"

    def _validate_session(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        diffs = bank_payment_method_diffs or {}
        if diffs:
            methods = self.env["pos.payment.method"].sudo().browse(list(diffs)).filtered("efn_is_loyalty_points")
            for method in methods:
                if not self.currency_id.is_zero(diffs.get(method.id) or 0.0):
                    raise UserError(
                        _(
                            "Selisih tidak boleh diinput untuk metode %(name)s (nilai Poin EFN dicatat otomatis "
                            "dari reservasi). Kosongkan selisih metode ini.",
                            name=method.name,
                        )
                    )
        return super()._validate_session(balancing_account, amount_to_balance, bank_payment_method_diffs)
