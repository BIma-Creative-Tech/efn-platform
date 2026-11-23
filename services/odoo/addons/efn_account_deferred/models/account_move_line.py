"""G59-B: tanggal mulai/selesai tangguhan per baris tagihan/faktur (port `custom_account_deferred`, ADR-049)."""

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    efn_deferred_start_date = fields.Date(
        string="Mulai Tangguhan",
        copy=False,
        help="Nilai laba-rugi baris ini dibagi mulai tanggal ini (inklusif).",
    )
    efn_deferred_end_date = fields.Date(
        string="Selesai Tangguhan",
        copy=False,
        help="...sampai tanggal ini (inklusif). Satu entri pengakuan per bulan.",
    )

    @api.constrains("efn_deferred_start_date", "efn_deferred_end_date")
    def _check_efn_deferred_dates(self):
        for line in self:
            if bool(line.efn_deferred_start_date) != bool(line.efn_deferred_end_date):
                raise ValidationError(self.env._("Tanggal tangguhan: isi mulai dan selesai, atau kosongkan keduanya."))
            if line.efn_deferred_start_date and line.efn_deferred_end_date < line.efn_deferred_start_date:
                raise ValidationError(self.env._("Tanggal selesai tangguhan harus sama atau setelah tanggal mulai."))

    def _efn_is_deferrable(self):
        """Baris produk laba-rugi (bukan baris pajak, mis. PPh 4(2)) dengan rentang tanggal terisi."""
        self.ensure_one()
        return bool(
            self.efn_deferred_start_date
            and self.efn_deferred_end_date
            and self.display_type == "product"
            and self.account_id.internal_group in ("income", "expense")
        )
