"""E-41 (18-15): default retur tanpa struk berubah `cash` -> `manager_approval`.

Parameter dibuat `noupdate=1` oleh 19.0.1.0.0–19.0.1.2.0 dengan nilai `cash` (default lama L-23). DB yang masih
memakai nilai lama dipindah ke default baru; nilai lain (`deny`, atau sudah `manager_approval`) tidak disentuh.
"""

import logging

_logger = logging.getLogger(__name__)

PARAM = "efn.pos.refund.unlinked_return_mode"


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        "UPDATE ir_config_parameter SET value = 'manager_approval' WHERE key = %s AND value = 'cash'",
        (PARAM,),
    )
    if cr.rowcount:
        _logger.warning("efn_pos_refund_origin: %s 'cash' -> 'manager_approval' (E-41, 18-15)", PARAM)
