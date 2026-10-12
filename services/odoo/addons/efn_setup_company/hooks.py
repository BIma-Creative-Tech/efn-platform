"""W-03 / Q-ON6: presisi IDR global Rp1 (docs/08 §9.1; R-T17).

Satu-satunya Python modul ini (hook kecil yang tak terhindarkan): Odoo menolak menaikkan ``rounding`` mata uang
yang sudah dipakai jurnal (``account/models/res_currency.py`` ``write`` -> UserError). Pada DB baru tanpa demo belum
ada jurnal sehingga presisi langsung diubah. Pada DB **demo** (``--with-demo``), demo ``l10n_id`` sudah membuat jurnal
IDR untuk "ID Company" sebelum modul ini dipasang -> perubahan tidak mungkin; install tetap jalan dengan WARNING agar
DB demo/dev bisa dibuat, dan presisi tetap 0,01. DB produksi dibuat tanpa demo (dijaga test
``tests/test_currency_idr.py`` di CI dan cek pra-go-live di README).
"""

import logging

_logger = logging.getLogger(__name__)

IDR_ROUNDING = 1.0


def pre_init_hook(env):
    idr = env.ref("base.IDR")
    if idr.rounding == IDR_ROUNDING:
        return
    if idr._has_accounting_entries():
        _logger.warning(
            "EFN setup company: IDR sudah dipakai jurnal (DB demo/lama) -> presisi tetap %s, bukan Rp1 (W-03). "
            "DB produksi wajib dibuat baru tanpa demo.",
            idr.rounding,
        )
        return
    idr.write({"rounding": IDR_ROUNDING})
    _logger.info("EFN setup company: presisi IDR diubah ke Rp1 (W-03)")
