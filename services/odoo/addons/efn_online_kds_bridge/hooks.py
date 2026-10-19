"""Hook uninstall: tiket KDS bersumber pesanan online (``order_id`` kosong) dihapus SEBELUM modul dilepas.

Alasan: modul ini melonggarkan ``efn.kds.ticket.order_id`` (``required`` -> opsional). Setelah uninstall, definisi
``efn_pos_kds`` (``required=True``) kembali berlaku dan Odoo memasang ``NOT NULL`` lagi; baris online yang tersisa
akan menggagalkan langkah itu. Tiket = data operasional dapur (retensi 90 hari), bukan dokumen keuangan.
"""

import logging

_logger = logging.getLogger(__name__)


def uninstall_hook(env):
    env.cr.execute("DELETE FROM efn_kds_ticket WHERE order_id IS NULL")
    _logger.info("EFN KDS bridge uninstall: %s tiket online dihapus", env.cr.rowcount)
