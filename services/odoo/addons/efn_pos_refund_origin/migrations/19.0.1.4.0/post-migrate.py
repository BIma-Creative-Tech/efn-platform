"""SEC-151: baris wizard PIN dari versi lama bisa masih memuat teks PIN (disimpan ``web_save`` sebelum ``action_apply``
yang gagal). Kosongkan semuanya; sejak 19.0.1.4.0 ``create``/``write`` wizard tidak pernah menyimpan teks PIN.
Catatan: salinan lama tetap ada di WAL/backup sampai rotasi retensi (README, batasan)."""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        "UPDATE efn_pos_return_pin_wizard SET pin = NULL, pin_confirm = NULL "
        "WHERE pin IS NOT NULL OR pin_confirm IS NOT NULL"
    )
    if cr.rowcount:
        _logger.warning("efn_pos_refund_origin: %d baris wizard PIN dikosongkan (SEC-151)", cr.rowcount)
