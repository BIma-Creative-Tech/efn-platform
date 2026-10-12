"""G18 W-01 / E-30: tautkan metode bayar "Poin EFN" PT ke POS yang redeem-nya aktif (docs/08 §9.2).

pos.config tidak dibuat lewat XML (default dihitung dari env.company), jadi data
``data/pos_loyalty_points_payment.xml`` hanya membuat jurnal, akun, dan metode per PT. Penautan ke
``payment_method_ids`` dilakukan di sini:

- saat POS dibuat / diubah dengan ``efn_loyalty_redeem_enabled = True`` (``create``/``write``),
  kecuali POS bersesi terbuka;
- setiap install/upgrade modul (``<function>`` noupdate=0) untuk POS yang sudah ada.

Hanya MENAMBAH metode Poin EFN aktif milik company POS bila POS belum punya; tidak pernah menghapus.
POS dengan sesi terbuka dilewati saat upgrade (metode bayar tidak boleh diubah selama sesi berjalan) dan
dicatat di log; admin menautkannya setelah sesi ditutup (README checklist). Tanpa metode, redeem ditolak
server ``efn_pos_loyalty_ledger`` (``_efn_loyalty_check_redeem``), jadi kelalaian tidak membuka celah.
"""

import logging

from odoo import Command, api, models

_logger = logging.getLogger(__name__)


class PosConfig(models.Model):
    _inherit = "pos.config"

    def _efn_setup_points_method_for_company(self):
        self.ensure_one()
        return (
            self.env["pos.payment.method"]
            .sudo()
            .search(
                [("efn_is_loyalty_points", "=", True), ("company_id", "=", self.company_id.id), ("active", "=", True)],
                limit=1,
            )
        )

    def _efn_setup_link_points_method(self, skip_open_sessions=True):
        linked = self.browse()
        for config in self.sudo().filtered("efn_loyalty_redeem_enabled"):
            if config._efn_loyalty_points_method_record():
                continue
            method = config._efn_setup_points_method_for_company()
            if not method:
                continue
            if skip_open_sessions and config.current_session_id:
                _logger.info("EFN setup POS: POS %s sesi terbuka, metode Poin EFN belum ditautkan", config.id)
                continue
            config.write({"payment_method_ids": [Command.link(method.id)]})
            linked |= config
        if linked:
            _logger.info("EFN setup POS: metode Poin EFN ditautkan ke POS %s", linked.ids)
        return linked

    @api.model
    def _efn_setup_link_points_methods_all(self):
        """Install/upgrade: semua POS redeem-aktif tanpa metode Poin EFN."""
        configs = self.sudo().with_context(active_test=False).search([("efn_loyalty_redeem_enabled", "=", True)])
        return configs._efn_setup_link_points_method()

    @api.model
    def _default_payment_methods(self):
        """CE ``_default_payment_methods`` memasang SEMUA metode non-tunai company ke POS baru (termasuk Poin EFN).
        Poin EFN hanya untuk POS redeem-aktif (ditautkan di bawah), bukan kiosk/POS tanpa redeem."""
        return super()._default_payment_methods().filtered(lambda m: not m.efn_is_loyalty_points)

    @api.model_create_multi
    def create(self, vals_list):
        configs = super().create(vals_list)
        configs._efn_setup_link_points_method()
        return configs

    def write(self, vals):
        res = super().write(vals)
        if vals.get("efn_loyalty_redeem_enabled") or "company_id" in vals:
            self._efn_setup_link_points_method()
        return res
