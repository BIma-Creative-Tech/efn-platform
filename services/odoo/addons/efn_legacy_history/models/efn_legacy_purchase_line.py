"""Histori baris pembelian ESB (G65, ADR-042 §0b, docs/04 §27.13.6, fit-gap G65 §1.2) — arsip read-only.

Grain: satu baris per baris rekap pembelian ESB (``purchaseNum`` + ``line_no`` stabil, §27.13.1). Rentang
``[awal bulan(cutover) − 24 bulan, cutover − 1]`` (ditegakkan export/validator; helper menegakkan ``< cutover``).
Dimuat BULK per irisan **company × bulan** (``DELETE`` + ``INSERT``), bukan ORM — ``warehouse_id`` boleh kosong (HO /
cabang ``skip`` / tak terpetakan), sehingga irisan tidak bisa per gudang (varian ``bulk_replace_slice`` tanpa gudang).

BUKAN ``purchase.order``: tanpa relasi ke ``purchase.order``/``stock.move``/``account.move``; tidak dibaca
``purchase.report`` maupun ETL ``fact_dc_purchase_line`` (stok, jurnal, pajak, nomor dokumen ganda; fit-gap §1.1).
``supplier_name`` = nama perusahaan (L-110); supplier perorangan = data pribadi tak terenkripsi (inventaris PDP
§2.8.14).
"""

from odoo import api, fields, models

from ..bulk import bulk_replace_slice, check_import_allowed

QTY = (16, 3)
IDR = (16, 2)


class EfnLegacyPurchaseLine(models.Model):
    _name = "efn.legacy.purchase.line"
    _description = "Histori baris pembelian ESB (arsip sistem lama — bukan transaksi Odoo)"
    _order = "date desc, id desc"
    _rec_name = "purchase_num"
    _check_company_auto = True
    _log_access = False

    company_id = fields.Many2one("res.company", string="Company", required=True, readonly=True)
    date = fields.Date(string="Tanggal PO", required=True, readonly=True)
    date_required = fields.Date(string="Tanggal diminta", readonly=True)
    purchase_num = fields.Char(string="No. PO ESB", size=64, required=True, readonly=True, index=True)
    line_no = fields.Integer(string="Baris", required=True, readonly=True)
    warehouse_id = fields.Many2one(
        "stock.warehouse", string="Gudang/outlet", readonly=True, check_company=True, ondelete="set null"
    )
    branch_name = fields.Char(string="Cabang ESB", size=64, readonly=True)
    supplier_id = fields.Many2one(
        "res.partner", string="Supplier (Odoo)", readonly=True, check_company=True, ondelete="set null"
    )
    supplier_name = fields.Char(string="Supplier ESB", size=128, readonly=True)
    product_id = fields.Many2one(
        "product.product", string="Produk (Odoo)", readonly=True, check_company=True, ondelete="set null"
    )
    esb_product_code = fields.Char(string="Kode produk ESB", size=64, required=True, readonly=True)
    product_name = fields.Char(string="Nama produk ESB", size=128, readonly=True)
    category_name = fields.Char(string="Kategori ESB", size=64, readonly=True)
    sub_category_name = fields.Char(string="Sub-kategori ESB", size=64, readonly=True)
    uom_name = fields.Char(string="Satuan ESB", size=16, readonly=True)
    qty = fields.Float(string="Qty", digits=QTY, default=0.0, readonly=True)
    receipt_qty = fields.Float(string="Qty diterima", digits=QTY, default=0.0, readonly=True)
    pricelist_price = fields.Float(string="Harga pricelist", digits=IDR, default=0.0, readonly=True)
    price_unit = fields.Float(string="Harga satuan", digits=IDR, default=0.0, readonly=True, aggregator="avg")
    # Nominal Rupiah atau persen: belum terverifikasi (Q-G65-2) -> disimpan apa adanya.
    discount = fields.Float(string="Diskon (ESB)", digits=IDR, default=0.0, readonly=True)
    vat_value = fields.Float(string="PPN", digits=IDR, default=0.0, readonly=True)
    total = fields.Float(string="Total", digits=IDR, default=0.0, readonly=True)
    status = fields.Char(string="Status PO ESB", size=32, readonly=True)
    map_source = fields.Char(string="Sumber pemetaan", size=8, readonly=True, help="header | name | none (§27.13.1)")
    # Disediakan fit-gap; TIDAK diisi G65 (costCenterName dibuang extractor, §27.13.1).
    cost_center = fields.Char(string="Cost center ESB", size=64, readonly=True)

    _efn_company_date_idx = models.Index("(company_id, date)")
    _efn_company_product_date_idx = models.Index("(company_id, product_id, date)")
    _efn_company_supplier_date_idx = models.Index("(company_id, supplier_id, date)")

    _efn_upsert_spec = (
        ("date", "date", True),
        ("date_required", "date", False),
        ("company_id", "m2o", True),
        ("warehouse_id", "m2o", False),
        ("purchase_num", "char:64", True),
        ("line_no", "int", True),
        ("branch_name", "char:64", False),
        ("supplier_id", "m2o", False),
        ("supplier_name", "char:128", False),
        ("product_id", "m2o", False),
        ("esb_product_code", "char:64", True),
        ("product_name", "char:128", False),
        ("category_name", "char:64", False),
        ("sub_category_name", "char:64", False),
        ("uom_name", "char:16", False),
        ("qty", "num", False),
        ("receipt_qty", "num", False),
        ("pricelist_price", "num", False),
        ("price_unit", "num", False),
        ("discount", "num", False),
        ("vat_value", "num", False),
        ("total", "num", False),
        ("status", "char:32", False),
        ("map_source", "sel:header|name|none", False),
    )
    _efn_upsert_key = ()  # tanpa kunci unik -> hanya bulk_replace_slice
    _efn_date_field = "date"
    _efn_months_window = False  # rentang 24 bulan ditegakkan export/validator (tanpa parameter baru, §27.13.4)
    _efn_slice_by_warehouse = False  # irisan company × bulan (warehouse_id nullable, §27.13.6)

    @api.model
    def _efn_upsert_derive(self, vals, row):
        return vals

    @api.model
    def _efn_bulk_replace_slice(self, company_id, date_from, date_to, rows, run_id=None, **kwargs):
        """Lihat ``efn_legacy_history.bulk.bulk_replace_slice`` dengan ``warehouse_id=None`` (privat: bukan RPC)."""
        return bulk_replace_slice(self, company_id, None, date_from, date_to, rows, run_id, **kwargs)

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
