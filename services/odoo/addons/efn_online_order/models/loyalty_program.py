"""G19 E-36 (docs/04 §11.2 no. 7, docs/08 §10.2 no. 4): program ``sale_loyalty`` yang boleh berlaku di SO online.

``sale_ok=True`` hanya untuk program promo online:
- tipe ``promotion``, ``promo_code``, ``buy_x_get_y``, ``coupons`` (ADR-020: ``loyalty``/``ewallet``/``gift_card``/
  ``next_order_coupons`` ditolak — partner generik kanal akan mengumpulkan kartu/poin);
- company wajib dan ``pricelist_ids`` wajib, seluruhnya pricelist kanal online company itu (standar
  ``sale_loyalty``: program TANPA pricelist berlaku di semua pricelist -> promo POS/brand lain bocor ke online);
- bukan ``efn_requires_otp`` (OTP hanya alur POS, ``efn_pos_otp_gate``) dan tidak ``portal_visible``.

Default ``sale_ok`` = False di modul ini (standar ``sale_loyalty`` = True), sama dengan ``ir.default``
``efn_setup_online``, sehingga program POS baru tidak pernah berlaku di SO tanpa keputusan eksplisit Marketing.
Kanal tetap memfilter program saat runtime (``efn.online.channel._efn_online_promo_programs``).
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .efn_online_common import ONLINE_PROGRAM_TYPES


class LoyaltyProgram(models.Model):
    _inherit = "loyalty.program"

    sale_ok = fields.Boolean(default=False)

    def _efn_online_constrained_fields(self):
        names = ["sale_ok", "program_type", "pricelist_ids", "company_id", "portal_visible"]
        if "efn_requires_otp" in self._fields:  # efn_pos_otp_gate opsional (tanpa dependency)
            names.append("efn_requires_otp")
        return names

    def _efn_online_sale_violation(self):
        """Pesan pelanggaran aturan program online (None bila sah atau ``sale_ok`` mati)."""
        self.ensure_one()
        if not self.sale_ok:
            return None
        if self.program_type not in ONLINE_PROGRAM_TYPES:
            return _(
                "Program '%(name)s': hanya Promotion, Promo Code, Buy X Get Y, dan Coupons yang boleh berlaku di "
                "pesanan penjualan/kanal online (poin, eWallet, gift card, kupon pesanan berikut tidak; ADR-020).",
                name=self.name,
            )
        if not self.company_id:
            return _("Program '%(name)s' untuk pesanan penjualan wajib memiliki company.", name=self.name)
        channels = (
            self.env["efn.online.channel"]
            .sudo()
            .with_context(active_test=False)
            .search([("company_id", "=", self.company_id.id)])
        )
        allowed = channels.pricelist_id
        if not self.pricelist_ids or self.sudo().pricelist_ids - allowed:
            return _(
                "Program '%(name)s' untuk pesanan penjualan wajib dibatasi ke pricelist kanal online company yang sama "
                "(isi 'Pricelist'; program tanpa pricelist berlaku di semua pricelist).",
                name=self.name,
            )
        if "efn_requires_otp" in self._fields and self.efn_requires_otp:
            return _(
                "Program '%(name)s' wajib OTP (khusus POS) tidak boleh berlaku di pesanan penjualan online.",
                name=self.name,
            )
        if self.portal_visible:
            return _("Program '%(name)s' untuk pesanan online tidak boleh tampil di portal.", name=self.name)
        return None

    @api.constrains(lambda self: self._efn_online_constrained_fields())
    def _check_efn_online_sale_program(self):
        for program in self:
            message = program._efn_online_sale_violation()
            if message:
                raise ValidationError(message)
