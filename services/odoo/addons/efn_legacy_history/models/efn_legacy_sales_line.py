"""Histori baris transaksi penjualan ESB (G43, Q-ESB20, docs/04 §27.8 v19.1) — arsip read-only, volume besar.

Grain: satu baris per baris transaksi ESB (``fact_esb_sales_line``: ``sales_num`` + ``line_no``). Rentang
``[awal bulan(cutover) − N bulan, cutover − 1]``, ``N = efn_mig_esb.sales_line_months`` (default 24; 0 = nonaktif).
Dimuat BULK per irisan company × gudang × bulan (``DELETE`` + ``INSERT`` multi-baris, docs/04 §27.8a), bukan ORM.
``sales_num`` = nomor dokumen ESB (bukan data pribadi; sumber tidak memuat data pelanggan).

Hemat ruang (PB ±8 juta baris): **tanpa** constraint unik (idempotensi = ganti irisan), **tanpa** ``run_id`` per
baris (jejak di ``import_report.json`` importer), ``_log_access = False`` (±24 byte/baris), angka Rupiah Float(16,2)
(numeric, tanpa kolom mata uang per baris), indeks hanya ``(company_id, date)`` dan ``(company_id, warehouse_id,
date)``; ``sales_num`` tidak diindeks (pencarian wajib disertai filter tanggal/outlet).
"""

from odoo import api, fields, models

from ..bulk import bulk_replace_slice, check_import_allowed

QTY = (16, 3)
IDR = (16, 2)

ROW_TYPES = [
    ("ALACARTE", "A la carte"),
    ("PACKAGE_HEADER", "Paket (induk)"),
    ("PACKAGE_COMPONENT", "Paket (komponen)"),
]


class EfnLegacySalesLine(models.Model):
    _name = "efn.legacy.sales.line"
    _description = "Histori baris transaksi ESB (arsip sistem lama — bukan transaksi Odoo)"
    _order = "date desc, id desc"
    _rec_name = "sales_num"
    _check_company_auto = True
    _log_access = False

    date = fields.Date(string="Tanggal", required=True, readonly=True)
    company_id = fields.Many2one("res.company", string="Company", required=True, readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", string="Outlet", required=True, readonly=True, check_company=True)
    product_id = fields.Many2one(
        "product.product", string="Produk menu (Odoo)", readonly=True, check_company=True, ondelete="set null"
    )
    esb_menu_code = fields.Char(string="Kode menu ESB", size=32, required=True, readonly=True)
    sales_num = fields.Char(string="No. transaksi ESB", size=32, required=True, readonly=True)
    line_no = fields.Integer(string="Baris", required=True, readonly=True)
    qty = fields.Float(string="Qty", digits=QTY, default=0.0, readonly=True)
    price = fields.Float(string="Harga", digits=IDR, default=0.0, readonly=True, aggregator="avg")
    total = fields.Float(string="Total", digits=IDR, default=0.0, readonly=True)
    discount = fields.Float(string="Diskon", digits=IDR, default=0.0, readonly=True)
    cogs = fields.Float(string="HPP (ESB)", digits=IDR, default=0.0, readonly=True)
    row_type = fields.Selection(ROW_TYPES, string="Jenis baris", readonly=True)
    is_takeaway = fields.Boolean(string="Takeaway", default=False, readonly=True)

    _efn_company_date_idx = models.Index("(company_id, date)")
    _efn_company_wh_date_idx = models.Index("(company_id, warehouse_id, date)")

    _efn_upsert_spec = (
        ("date", "date", True),
        ("company_id", "m2o", True),
        ("warehouse_id", "m2o", True),
        ("product_id", "m2o", False),
        ("esb_menu_code", "char:32", True),
        ("sales_num", "char:32", True),
        ("line_no", "int", True),
        ("qty", "num", False),
        ("price", "num", False),
        ("total", "num", False),
        ("discount", "num", False),
        ("cogs", "num", False),
        ("row_type", "sel:" + "|".join(key for key, _label in ROW_TYPES), False),
        ("is_takeaway", "bool", False),
    )
    _efn_upsert_key = ()  # tanpa kunci unik -> hanya bulk_replace_slice
    _efn_date_field = "date"
    _efn_months_window = True

    @api.model
    def _efn_upsert_derive(self, vals, row):
        return vals

    @api.model
    def _efn_bulk_replace_slice(self, company_id, warehouse_id, date_from, date_to, rows, run_id=None, **kwargs):
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
