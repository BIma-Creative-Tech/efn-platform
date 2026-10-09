"""F-73 / temuan #37: program loyalty yang SUDAH ada (POS) tidak boleh berlaku di SO online.

``sale_loyalty`` memberi ``loyalty.program.sale_ok`` default True dan memanggil ``_update_programs_and_rewards`` saat
konfirmasi SO -> total bisa berubah setelah pelanggan membayar. Data XML tidak dapat menyasar record tanpa xmlid,
sehingga dipakai hook kecil ini. Program BARU mendapat default False lewat ``ir.default`` (data XML).
E-28 tetap membandingkan total sebelum/sesudah konfirmasi (jaring pengaman bila program diaktifkan manual).
"""

import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    programs = env["loyalty.program"].with_context(active_test=False).search([("sale_ok", "=", True)])
    programs.write({"sale_ok": False})
    _logger.info("EFN setup online: sale_ok dimatikan pada %d program loyalty", len(programs))
