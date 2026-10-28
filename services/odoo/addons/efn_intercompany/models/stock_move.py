"""Perbaikan CE: batal SO oleh user penjualan tanpa hak Pembelian (regresi sejak purchase_stock terpasang, G28).

``sale_stock`` ``sale.order._action_cancel`` menelusuri dokumen hulu untuk aktivitas peringatan
(``_log_activity_get_documents`` 'UP') -> ``purchase_stock`` membaca ``stock.move.created_purchase_line_ids`` ->
AccessError bagi manajer penjualan/outlet tanpa grup Purchase. Penelusuran itu hanya untuk menjadwalkan aktivitas
peringatan di PO (CE sudah memakai ``sudo`` saat menjadwalkannya), jadi pembacaan hulu dilakukan sebagai superuser
hanya untuk user tanpa hak baca baris PO. Tidak ada data PO yang dikembalikan ke user.
"""

from odoo import models


class StockMove(models.Model):
    _inherit = "stock.move"

    def _get_upstream_documents_and_responsibles(self, visited):
        if self.env.su or self.env["purchase.order.line"].has_access("read"):
            return super()._get_upstream_documents_and_responsibles(visited)
        return super(StockMove, self.sudo())._get_upstream_documents_and_responsibles(visited.sudo())
