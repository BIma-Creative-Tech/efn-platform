"""Upgrade efn_setup_supermarket 19.0.2.x -> 19.0.3.0.0 (G47).

Hanya DB demo (outlet SMD01..SMD05 dan user demo adalah data demo). Data non-demo tidak berubah.

1. Outlet demo -> penerimaan dua langkah (keputusan pemilik proyek 21 Nov 2026, Q-G47-2). Record warehouse
   ``noupdate`` tidak ditulis ulang saat upgrade (tools/convert.py 19.0 [V]), jadi ``reception_steps`` ditulis lewat
   ORM. Hanya outlet yang masih ``one_step``: nilai lain (mis. ``three_steps`` yang dipilih pengguna) tidak disentuh.
   ``stock.warehouse.write`` mengaktifkan lokasi Input, memperbarui picking type penerimaan, dan rule rute penerimaan;
   rute resupply DC -> outlet
   tidak berubah (rule-nya bertujuan ``lot_stock_id``) [V source]. Transfer masuk yang sudah terbuka tetap satu langkah
   (dibuat dengan tujuan lama); hanya pengadaan baru yang dua langkah.
2. Persona Insight (G47) di user demo: menerapkan ulang ``demo/res_users_insight_demo.xml`` dengan ``mode="init"``.
   Isi file idempoten (``Command.link`` untuk user lama; user baru ber-xmlid dibuat/diperbarui).
"""

import logging

from odoo import SUPERUSER_ID, api
from odoo.tools import convert_file

_logger = logging.getLogger(__name__)

MODULE = "efn_setup_supermarket"
OUTLET_XMLIDS = tuple(f"warehouse_smd0{i}" for i in range(1, 6))


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    module = env["ir.module.module"].search([("name", "=", MODULE)], limit=1)
    if not module.demo:
        return

    outlets = env["stock.warehouse"]
    for xmlid in OUTLET_XMLIDS:
        outlets |= env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False) or env["stock.warehouse"]
    pending = outlets.filtered(lambda w: w.reception_steps == "one_step")
    if pending:
        pending.write({"reception_steps": "two_steps"})
        _logger.info("efn_setup_supermarket 19.0.3.0.0: %s outlet demo -> two_steps", len(pending))

    if env.ref(f"{MODULE}.user_smd01_store_head", raise_if_not_found=False):
        convert_file(env, MODULE, "demo/res_users_insight_demo.xml", {}, mode="init", noupdate=True)
        _logger.info("efn_setup_supermarket 19.0.3.0.0: persona Insight demo diterapkan")
