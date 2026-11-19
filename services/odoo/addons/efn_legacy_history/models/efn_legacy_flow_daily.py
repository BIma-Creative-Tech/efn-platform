"""Histori aliran produk harian ESB (G43, ADR-042 §3.1 butir 2, docs/04 §27.8) — arsip read-only, input forecast.

Grain: hari x company x gudang x produk storable. Sumber: ``efn_dwh.v_esb_flow_daily`` setelah pemetaan
``branch_id`` -> ``stock.warehouse`` dan ``product_code`` -> ``product.product`` (bundel migrasi). BUKAN transaksi
Odoo: tidak membuat ``stock.move``/valuasi/jurnal. Volume kecil (PB ±0,1 GB) → ``_log_access`` dipertahankan
(``create_uid``/``write_uid`` = user teknis migrasi, diisi SQL bulk).

``outflow`` disimpan langsung (bukan compute ORM): bulk SQL tidak menjalankan compute, jadi nilainya diisi helper
(``sold + st_out + ck_out``); jalur ORM (hanya konteks migrasi) mengisinya di ``create``/``write``.
"""

from odoo import api, fields, models

from ..bulk import bulk_replace_slice, bulk_upsert, check_import_allowed

QTY = (16, 3)
OUTFLOW_PARTS = ("sold", "st_out", "ck_out")


class EfnLegacyFlowDaily(models.Model):
    _name = "efn.legacy.flow.daily"
    _description = "Histori aliran produk ESB (arsip sistem lama — bukan transaksi Odoo)"
    _order = "day desc, id desc"
    _rec_name = "esb_product_code"
    _check_company_auto = True

    day = fields.Date(string="Tanggal", required=True, readonly=True)
    company_id = fields.Many2one("res.company", string="Company", required=True, readonly=True)
    warehouse_id = fields.Many2one(
        "stock.warehouse", string="Outlet/Gudang", required=True, readonly=True, check_company=True
    )
    product_id = fields.Many2one("product.product", string="Produk", required=True, readonly=True, check_company=True)
    uom_id = fields.Many2one(related="product_id.uom_id", string="Satuan")
    sold = fields.Float(string="Terjual", digits=QTY, default=0.0, readonly=True)
    ck_prod = fields.Float(string="Produksi CK", digits=QTY, default=0.0, readonly=True)
    store_prod = fields.Float(string="Produksi toko", digits=QTY, default=0.0, readonly=True)
    ck_out = fields.Float(string="Kirim dari CK", digits=QTY, default=0.0, readonly=True)
    in_ck = fields.Float(string="Terima dari CK", digits=QTY, default=0.0, readonly=True)
    in_other = fields.Float(string="Terima lainnya", digits=QTY, default=0.0, readonly=True)
    st_in = fields.Float(string="Transfer masuk", digits=QTY, default=0.0, readonly=True)
    st_out = fields.Float(string="Transfer keluar", digits=QTY, default=0.0, readonly=True)
    outflow = fields.Float(
        string="Keluar stok (forecast)",
        digits=QTY,
        default=0.0,
        readonly=True,
        help="sold + st_out + ck_out (definisi keluar stok forecast v1); disimpan oleh impor, bukan compute.",
    )
    esb_brand = fields.Char(string="Brand ESB", size=16, readonly=True)
    esb_branch_id = fields.Integer(string="ID cabang ESB", readonly=True)
    esb_product_code = fields.Char(string="Kode produk ESB", size=64, required=True, readonly=True)
    esb_product_name = fields.Char(string="Nama produk (ESB)", size=256, readonly=True)
    run_id = fields.Char(string="Run migrasi", size=36, readonly=True)

    _efn_grain_unique = models.Constraint(
        "UNIQUE(company_id, warehouse_id, product_id, day)",
        "Histori aliran ESB: satu baris per company x gudang x produk x hari.",
    )
    _efn_company_day_idx = models.Index("(company_id, day)")
    _efn_company_wh_day_idx = models.Index("(company_id, warehouse_id, day)")
    # Query forecast (gudang x produk x rentang hari), docs/04 §27.8.
    _efn_wh_product_day_idx = models.Index("(warehouse_id, product_id, day)")

    # Spesifikasi bulk: (field, jenis, wajib). Kunci upsert = constraint unik di atas.
    _efn_upsert_spec = (
        ("day", "date", True),
        ("company_id", "m2o", True),
        ("warehouse_id", "m2o", True),
        ("product_id", "m2o", True),
        ("sold", "num", False),
        ("ck_prod", "num", False),
        ("store_prod", "num", False),
        ("ck_out", "num", False),
        ("in_ck", "num", False),
        ("in_other", "num", False),
        ("st_in", "num", False),
        ("st_out", "num", False),
        ("esb_brand", "char:16", False),
        ("esb_branch_id", "int", False),
        ("esb_product_code", "char:64", True),
        ("esb_product_name", "char:256", False),
    )
    _efn_upsert_key = ("company_id", "warehouse_id", "product_id", "day")
    _efn_date_field = "day"
    _efn_months_window = False

    @api.model
    def _efn_upsert_derive(self, vals, row):
        vals["outflow"] = vals["sold"] + vals["st_out"] + vals["ck_out"]
        return vals

    @api.model
    def _efn_bulk_upsert(self, rows, run_id, company_ids, batch_size=5000, max_day=None):
        """Lihat ``efn_legacy_history.bulk.bulk_upsert`` (privat: bukan RPC)."""
        return bulk_upsert(self, rows, run_id, company_ids=company_ids, batch_size=batch_size, max_day=max_day)

    @api.model
    def _efn_bulk_replace_slice(self, company_id, warehouse_id, date_from, date_to, rows, run_id, **kwargs):
        """Lihat ``efn_legacy_history.bulk.bulk_replace_slice`` (privat: bukan RPC)."""
        return bulk_replace_slice(self, company_id, warehouse_id, date_from, date_to, rows, run_id, **kwargs)

    # ------------------------------------------------------------------ read-only (ADR-042 §3.1)
    @api.model_create_multi
    def create(self, vals_list):
        check_import_allowed(self.env, self._name)
        for vals in vals_list:
            if "outflow" not in vals:
                vals["outflow"] = sum(vals.get(f) or 0.0 for f in OUTFLOW_PARTS)
        return super().create(vals_list)

    def write(self, vals):
        check_import_allowed(self.env, self._name)
        res = super().write(vals)
        if "outflow" not in vals and any(f in vals for f in OUTFLOW_PARTS):
            for rec in self:
                super(EfnLegacyFlowDaily, rec).write({"outflow": rec.sold + rec.st_out + rec.ck_out})
        return res

    def unlink(self):
        check_import_allowed(self.env, self._name)
        return super().unlink()
