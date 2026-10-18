"""G19 E-36: program loyalty LAMA yang melanggar aturan program online (docs/04 §11.2 no. 7) dimatikan
``sale_ok``-nya saat install (post_init_hook) dan upgrade 19.0.1.2.0 (post-migrate). Constraint hanya berjalan saat
create/write; tanpa langkah ini program POS lama ber-``sale_ok`` (default ``sale_loyalty`` = True) bisa berlaku di
SO dan setiap penyuntingan berikutnya ditolak constraint. Program yang sudah sah tidak disentuh."""

import logging

_logger = logging.getLogger(__name__)


def efn_online_disable_invalid_sale_programs(env):
    Program = env["loyalty.program"].sudo().with_context(active_test=False)
    programs = Program.search([("sale_ok", "=", True)])
    invalid = programs.filtered(lambda p: p._efn_online_sale_violation())
    if invalid:
        invalid.write({"sale_ok": False})
    _logger.info("EFN online: sale_ok dimatikan pada %d program loyalty yang bukan program online", len(invalid))
    return invalid


def post_init_hook(env):
    efn_online_disable_invalid_sale_programs(env)
