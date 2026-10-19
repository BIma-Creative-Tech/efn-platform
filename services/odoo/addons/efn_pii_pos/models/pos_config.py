"""Partner terlindungi tidak di-preload ke PC kasir (ADR-036 §0.6, 18-30, L-36-3).

Odoo 19.0: `pos.config.get_limited_partners_loading(offset=0)` (SQL, `ORDER BY order_count DESC, name`,
limit `point_of_sale.limited_customer_count`) dipakai oleh `res.partner._load_pos_data_domain` (preload sesi)
dan `res.partner.get_new_partner(config_id, domain=[], offset)` (gulir daftar pelanggan tanpa kueri).
Hasilnya disaring di sini; partner order draft yang dimuat (`data['pos.order']`) dan partner user kasir tetap
ikut preload lewat `_load_pos_data_domain` standar. Partner terlindungi muncul di kasir hanya setelah dicari
(Enter di daftar pelanggan -> `get_new_partner` dengan domain).

`@api.private`: override ini tidak dapat dipanggil lewat RPC. Asumsi (belum terverifikasi di JS POS 19):
klien POS tidak memanggil metode ini langsung; pemanggilnya hanya kode server di atas.
"""

from odoo import api, models
from odoo.tools import SQL, split_every


class PosConfig(models.Model):
    _inherit = "pos.config"

    @api.private
    def get_limited_partners_loading(self, offset=0):
        rows = super().get_limited_partners_loading(offset)
        ids = [row[0] for row in rows]
        if not ids:
            return rows
        protected = set()
        for chunk in split_every(1000, ids):
            self.env.cr.execute(SQL("SELECT id FROM res_partner WHERE id IN %s AND efn_pii_protected", tuple(chunk)))
            protected.update(row[0] for row in self.env.cr.fetchall())
        return [row for row in rows if row[0] not in protected]
