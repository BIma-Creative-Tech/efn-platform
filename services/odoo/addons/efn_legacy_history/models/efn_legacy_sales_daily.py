"""Histori penjualan harian per menu ESB (G43, keputusan user 18 Nov 2026 Q-ESB20, docs/04 §27.8 v19.1) — arsip.

Grain: hari x company x outlet (gudang) x menu ESB. Sumber: ``efn_dwh.v_esb_sales_daily`` setelah pemetaan
``branch_id`` -> ``stock.warehouse`` (bundel migrasi). BUKAN ``pos.order``: tidak muncul di laporan POS/akuntansi
standar dan tidak menyentuh stok, pajak, atau penomoran (ADR-042).

Kunci grain = ``(company_id, warehouse_id, day, esb_menu_code)`` — kode menu ESB selalu tersimpan (menu takeaway
``" T"`` = kode tersendiri, ``is_takeaway`` dari sumber) sehingga ``product_id`` **boleh kosong** (menu tak terpetakan
tetap tercatat; keputusan efn-lead 18 Nov 2026). Setara dengan kunci kontrak ``(product, day, is_takeaway)`` bila
semua menu terpetakan. ``net`` disimpan langsung (nilai export; bila kosong = gross − discount), bukan compute.
``_log_access`` dipertahankan (docs/04 §27.8: default), diisi SQL bulk.
"""

from odoo import api, fields, models

from ..bulk import bulk_replace_slice, bulk_upsert, check_import_allowed

QTY = (16, 3)


class EfnLegacySalesDaily(models.Model):
    _name = "efn.legacy.sales.daily"
    _description = "Histori penjualan harian ESB (arsip sistem lama — bukan transaksi Odoo)"
    _order = "day desc, id desc"
    _rec_name = "esb_menu_code"
    _check_company_auto = True

    day = fields.Date(string="Tanggal", required=True, readonly=True)
    company_id = fields.Many2one("res.company", string="Company", required=True, readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id", string="Mata uang")
    warehouse_id = fields.Many2one("stock.warehouse", string="Outlet", required=True, readonly=True, check_company=True)
    pos_config_id = fields.Many2one(
        "pos.config", string="Kasir POS", readonly=True, check_company=True, ondelete="set null"
    )
    product_id = fields.Many2one(
        "product.product", string="Produk menu (Odoo)", readonly=True, check_company=True, ondelete="set null"
    )
    esb_brand = fields.Char(string="Brand ESB", size=16, readonly=True)
    esb_branch_id = fields.Integer(string="ID cabang ESB", readonly=True)
    esb_menu_code = fields.Char(string="Kode menu ESB", size=64, required=True, readonly=True)
    menu_name = fields.Char(string="Nama menu (ESB)", size=256, readonly=True)
    menu_category = fields.Char(string="Kategori menu (ESB)", size=128, readonly=True)
    is_takeaway = fields.Boolean(string="Takeaway", default=False, readonly=True)
    qty = fields.Float(string="Qty", digits=QTY, default=0.0, readonly=True)
    gross = fields.Monetary(string="Bruto", currency_field="currency_id", default=0.0, readonly=True)
    discount = fields.Monetary(string="Diskon", currency_field="currency_id", default=0.0, readonly=True)
    net = fields.Monetary(
        string="Neto", currency_field="currency_id", default=0.0, readonly=True, help="Bruto - diskon (semantik G41)."
    )
    cogs = fields.Monetary(string="HPP (ESB)", currency_field="currency_id", default=0.0, readonly=True)
    trx_count = fields.Integer(string="Jumlah transaksi", default=0, readonly=True)
    run_id = fields.Char(string="Run migrasi", size=36, readonly=True)

    _efn_grain_unique = models.Constraint(
        "UNIQUE(company_id, warehouse_id, day, esb_menu_code)",
        "Histori penjualan ESB: satu baris per company x outlet x hari x menu.",
    )
    _efn_company_day_idx = models.Index("(company_id, day)")
    _efn_company_wh_day_idx = models.Index("(company_id, warehouse_id, day)")

    _efn_upsert_spec = (
        ("day", "date", True),
        ("company_id", "m2o", True),
        ("warehouse_id", "m2o", True),
        ("pos_config_id", "m2o", False),
        ("product_id", "m2o", False),
        ("esb_brand", "char:16", False),
        ("esb_branch_id", "int", False),
        ("esb_menu_code", "char:64", True),
        ("menu_name", "char:256", False),
        ("menu_category", "char:128", False),
        ("is_takeaway", "bool", False),
        ("qty", "num", False),
        ("gross", "num", False),
        ("discount", "num", False),
        ("net", "num", False),
        ("cogs", "num", False),
        ("trx_count", "int", False),
    )
    _efn_upsert_key = ("company_id", "warehouse_id", "day", "esb_menu_code")
    _efn_date_field = "day"
    _efn_months_window = False

    @api.model
    def _efn_upsert_derive(self, vals, row):
        # Kontrak: net dihitung saat export. Bila tidak dikirim -> gross - discount (semantik v_esb_sales_daily).
        if row.get("net") in (None, False, ""):
            vals["net"] = vals["gross"] - vals["discount"]
        return vals

    @api.model
    def _efn_bulk_upsert(self, rows, run_id, company_ids, batch_size=5000, max_day=None):
        """Lihat ``efn_legacy_history.bulk.bulk_upsert`` (privat: bukan RPC)."""
        return bulk_upsert(self, rows, run_id, company_ids=company_ids, batch_size=batch_size, max_day=max_day)

    @api.model
    def _efn_bulk_replace_slice(self, company_id, warehouse_id, date_from, date_to, rows, run_id, **kwargs):
        """Lihat ``efn_legacy_history.bulk.bulk_replace_slice`` (privat: bukan RPC)."""
        return bulk_replace_slice(self, company_id, warehouse_id, date_from, date_to, rows, run_id, **kwargs)

    @api.model_create_multi
    def create(self, vals_list):
        check_import_allowed(self.env, self._name)
        return super().create(vals_list)

    def write(self, vals):
        check_import_allowed(self.env, self._name)
        return super().write(vals)

    def unlink(self):
        check_import_allowed(self.env, self._name)
        return super().unlink()
