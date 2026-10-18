"""19.0.1.2.0 (G19 E-36): program loyalty lama yang bukan program online -> ``sale_ok=False`` (docs/04 §11.2 no. 7).

Upgrade yang sama memasang dependency baru ``sale_loyalty`` bila belum ada (Odoo memasang dependency yang hilang saat
upgrade), sehingga ``loyalty.program`` tersedia di langkah post-migrate. Kanal lama mempertahankan nilai tersimpan
``payment_timeout_minutes`` (default field baru 70 hanya untuk kanal baru) dan ``stock_hold_mode='none'``.
"""

from odoo import SUPERUSER_ID, api

from odoo.addons.efn_online_order.hooks import efn_online_disable_invalid_sale_programs


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    efn_online_disable_invalid_sale_programs(env)
