"""Upgrade efn_setup_supermarket 19.0.1.x (tahap A) -> 19.0.2.0.0 (tahap B, G45).

Mengapa perlu: semua file data/demo modul ini ``noupdate="1"``. Saat upgrade, Odoo (tools/convert.py 19.0 [V]) melewati
``<record>`` yang xmlid-nya sudah ada dan melewati semua ``<function>`` di blok noupdate. Akibatnya pada DB tahap A:
- field ambang pada 16 kategori lama tidak terisi;
- ``efn_is_dc`` DC (bila efn_assortment belum pernah terpasang sebelumnya, hook-nya yang mengisi; di sini cadangan);
- DB demo: capability/kebijakan pada 5 outlet lama, putaway rule, jadwal kirim, dan parameter demo tidak terpasang.

Skrip ini menerapkan ulang file tahap B dengan ``mode="init"`` (record diperbarui walau xmlid ada, ``<function>``
dijalankan), dengan penjaga agar idempoten dan tidak menimpa nilai yang sudah diisi pengguna. Data tetap tunggal di XML
(tidak diduplikasi di sini). Tidak menyentuh file tahap A (nama/alamat/kategori yang mungkin diubah pengguna).
"""

import logging

from odoo import SUPERUSER_ID, api
from odoo.tools import convert_file

_logger = logging.getLogger(__name__)

MODULE = "efn_setup_supermarket"
THRESHOLD_FIELDS = ("efn_near_expiry_days", "efn_markdown_days", "efn_min_receipt_days")


def _apply(env, filename):
    convert_file(env, MODULE, filename, {}, mode="init", noupdate=True)
    _logger.info("efn_setup_supermarket 19.0.2.0.0: %s diterapkan", filename)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    dc = env.ref(f"{MODULE}.warehouse_smd_dc", raise_if_not_found=False)
    if dc and not dc.efn_is_dc:
        dc.efn_is_dc = True

    # Ambang kategori: hanya bila belum ada satu pun nilai (mis. efn_expiry_monitor dipasang lebih dulu dan category
    # manager sudah mengisi -> jangan ditimpa).
    imd = env["ir.model.data"].search([("module", "=", MODULE), ("model", "=", "product.category")])
    categories = env["product.category"].browse(imd.mapped("res_id")).exists()
    if not any(categ[field] for categ in categories for field in THRESHOLD_FIELDS):
        _apply(env, "data/product_category_expiry_data.xml")

    module = env["ir.module.module"].search([("name", "=", MODULE)], limit=1)
    company = env.ref(f"{MODULE}.company_smd", raise_if_not_found=False)
    if not (module.demo and company and env.ref(f"{MODULE}.warehouse_smd01", raise_if_not_found=False)):
        return

    # Record ber-xmlid: aman diterapkan ulang. Capability hanya bila outlet demo belum punya capability apa pun.
    codes = ("SMD01", "SMD02", "SMD03", "SMD04", "SMD05")
    outlets = env["stock.warehouse"].search([("company_id", "=", company.id), ("code", "in", codes)])
    if not outlets.efn_capability_ids:
        _apply(env, "demo/efn_assortment_demo.xml")
    # <function> create: hanya bila belum ada (cegah duplikat putaway/jadwal).
    if not env["stock.putaway.rule"].search_count([("location_in_id", "in", outlets.lot_stock_id.ids)]):
        _apply(env, "demo/stock_location_demo.xml")
    if not env["efn.delivery.schedule"].with_context(active_test=False).search_count([("company_id", "=", company.id)]):
        _apply(env, "demo/efn_replenishment_demo.xml")
    _apply(env, "demo/res_users_demo.xml")
    _apply(env, "demo/ir_config_parameter_demo.xml")
