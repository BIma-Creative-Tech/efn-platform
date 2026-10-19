"""Outlet online: override POS/KDS opsional + pengaman pajak F&B online (docs/08 §13.2 #2 dan #6).

- ``kds_config_id``: biasanya KOSONG — stasiun dicari lewat gudang outlet (``pos.config.picking_type_id.warehouse_id``
  = ``warehouse_id``, pola E-45). Diisi hanya bila outlet punya beberapa POS di gudang yang sama dan dapur online harus
  ke POS tertentu.
- E-29a-6: kanal ber-``require_outlet_fiscal_position`` (F&B) -> fiscal position outlet tidak boleh FP milik preset POS
  makan di tempat (``service_at='table'``), agar pesanan online tidak terkena Service Charge + PB1 dine-in karena salah
  pilih FP (18-1, ADR-027). ``service_at`` berasal dari ``pos_self_order`` (dicek ada saat runtime).
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class EfnOnlineOutlet(models.Model):
    _inherit = "efn.online.outlet"

    kds_config_id = fields.Many2one(
        "pos.config",
        string="POS dapur (override)",
        check_company=True,
        help="Opsional. Kosong = stasiun KDS dari semua POS di gudang outlet ini. Isi bila dapur pesanan online harus "
        "ke POS tertentu.",
    )

    @api.constrains("fiscal_position_id", "channel_id")
    def _check_efn_fnb_fiscal_position_not_dine_in(self):
        if "pos.preset" not in self.env or "service_at" not in self.env["pos.preset"]._fields:
            return
        Preset = self.env["pos.preset"].sudo().with_context(active_test=False)
        for outlet in self:
            fp = outlet.fiscal_position_id
            if not fp or not outlet.channel_id.require_outlet_fiscal_position:
                continue
            if Preset.search_count([("fiscal_position_id", "=", fp.id), ("service_at", "=", "table")], limit=1):
                raise ValidationError(
                    _(
                        "Fiscal position outlet online %(code)s dipakai preset POS makan di tempat; pesanan online "
                        "harus memakai fiscal position bawa pulang/ambil (tanpa service charge).",
                        code=outlet.code,
                    )
                )
