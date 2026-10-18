"""19.0.1.3.0 (r12, L-41 / SEC-131): kanal mode ``confirm`` wajib ``max_order_amount > 0`` + ``max_line_qty``.

Constraint baru hanya berjalan saat field ditulis, sehingga kanal lama ber-``stock_hold_mode='confirm'`` dengan
``max_order_amount`` 0 (= tanpa batas) tetap tersimpan. Migrasi ini mengisi placeholder pilot keputusan lead L-41
(Rp5.000.000; ``max_line_qty`` 20 sudah diisi default kolom ``required``) dan mencatat WARNING per kanal agar
Marketing/Ops mengganti nilai placeholder. Kanal mode ``none`` tidak disentuh.
"""

import logging

_logger = logging.getLogger(__name__)

PILOT_MAX_ORDER_AMOUNT = 5_000_000  # PLACEHOLDER L-41 (docs/04 §11.7), bukan konfigurasi bisnis final
PILOT_MAX_LINE_QTY = 20


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        "UPDATE efn_online_channel SET max_line_qty = %s WHERE max_line_qty IS NULL OR max_line_qty < 1",
        (PILOT_MAX_LINE_QTY,),
    )
    cr.execute(
        """
        UPDATE efn_online_channel
           SET max_order_amount = %s
         WHERE stock_hold_mode = 'confirm' AND COALESCE(max_order_amount, 0) <= 0
     RETURNING code
        """,
        (PILOT_MAX_ORDER_AMOUNT,),
    )
    for (code,) in cr.fetchall():
        _logger.warning(
            "EFN online kanal %s (mode confirm): max_order_amount kosong -> placeholder %s (L-41); ganti dengan "
            "nilai bisnis sebelum go-live",
            code,
            PILOT_MAX_ORDER_AMOUNT,
        )
