"""Tiket KDS bersumber pesanan online (E-29a, docs/08 §10.6 + §13.2; docs/04 §14.B B-6).

Skema (tanpa mengubah kode ``efn_pos_kds``):
- ``order_id`` (``pos.order``) menjadi OPSIONAL; ``efn_online_order_id`` (``efn.online.order``) baru.
- CHECK tepat satu sumber: ``(order_id IS NULL) <> (efn_online_order_id IS NULL)``.
- Unik per sumber: ``UNIQUE (order_id, station_id, sequence_no)`` (bawaan, NULL tidak bentrok) +
  ``UNIQUE (efn_online_order_id, station_id, sequence_no)`` (baru).
- Migrasi = saat install: Odoo melepas ``NOT NULL`` kolom ``order_id`` karena definisi gabungan tidak lagi ``required``
  (baris lama semuanya punya ``order_id`` -> CHECK lolos). Uninstall: ``hooks.uninstall_hook``.

``efn_online_order_id`` diberi ``groups`` = grup yang punya ACL baca ``efn.online.order`` (Sales/Inventory/Billing),
sehingga akun perangkat dapur (allowlist SEC-74) tidak bisa membaca pesanan online lewat tiket (§13.2 #5).
Payload layar KDS (``_efn_kds_payload``) TIDAK berubah: label = nomor SO pendek, tanpa kode ambil/bayar/PII.
"""

from odoo import fields, models


class EfnKdsTicket(models.Model):
    _inherit = "efn.kds.ticket"

    order_id = fields.Many2one(required=False)
    efn_online_order_id = fields.Many2one(
        "efn.online.order",
        string="Pesanan online",
        index="btree_not_null",
        ondelete="cascade",
        readonly=True,
        groups="sales_team.group_sale_salesman,stock.group_stock_user,account.group_account_invoice",
    )

    _efn_single_source = models.Constraint(
        "CHECK ((order_id IS NULL) <> (efn_online_order_id IS NULL))",
        "Tiket KDS harus bersumber tepat satu: order POS atau pesanan online.",
    )
    _efn_online_station_seq_uniq = models.Constraint(
        "UNIQUE (efn_online_order_id, station_id, sequence_no)",
        "Tiket KDS ganda untuk pesanan online yang sama.",
    )

    def _efn_kds_after_change(self, kind="update"):
        """Setelah bump/recall/baris selesai: sinkronkan pesanan online sumber (``preparing`` / ``ready``)."""
        result = super()._efn_kds_after_change(kind=kind)
        if kind != "cancel":
            orders = self.sudo().efn_online_order_id
            if orders:
                orders._efn_kds_sync_from_tickets()
        return result
