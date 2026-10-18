"""19.0.2.0.0 (G18 E-30): redeem Poin EFN = metode bayar, bukan produk diskon.

Data lama DIBIARKAN (baris produk "Poin EFN" pada order historis tetap ada untuk audit/ETL; belum ada produksi):
- produk ``product_efn_points`` diarsipkan (tidak bisa dijual/dimuat POS; server menolak baris baru);
- filter audit pengganti E-24 dihapus dan filter ``efn_setup_pos`` "Baris negatif non-promo" diaktifkan kembali
  (baris poin negatif tidak akan muncul lagi).
Order lama dengan baris poin TIDAK dikonversi ke pembayaran (refund atas order lama berpoin: proses manual
supervisor; tidak relevan sebelum go-live).
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    from odoo import SUPERUSER_ID, api

    env = api.Environment(cr, SUPERUSER_ID, {})
    product = env.ref("efn_pos_loyalty_ledger.product_efn_points", raise_if_not_found=False)
    if product:
        product.with_context(active_test=False).write({"sale_ok": False, "available_in_pos": False})
        product.product_tmpl_id.write({"active": False})
        product.write({"active": False})
    old_filter = env.ref(
        "efn_pos_loyalty_ledger.filter_pos_line_negative_non_reward_no_points", raise_if_not_found=False
    )
    if old_filter:
        old_filter.unlink()
    setup_filter = env.ref("efn_setup_pos.filter_pos_line_negative_non_reward", raise_if_not_found=False)
    if setup_filter:
        setup_filter.write({"active": True})
    _logger.info("EFN loyalty 19.0.2.0.0: produk Poin EFN lama diarsipkan, filter audit E-24 dipulihkan")
