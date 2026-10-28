"""Komoditas & standar mutu (K25-13: ditentukan user, diukur manual, tanpa integrasi alat ukur).

* ``efn.agri.commodity``: satu komoditas = satu atau beberapa produk (cabai merah, beras, sayur organik).
* ``efn.agri.qc.param``: parameter ukur per komoditas (angka dengan batas min/max, atau lulus/gagal). Parameter
  ``blocking`` yang gagal = inspeksi gagal (barang tidak boleh masuk stok).
* ``efn.agri.grade``: grade (A/B/C/Reject) dengan aturan batas per parameter + harga beli per kg/unit. Grade dipilih
  otomatis: grade pertama (urutan) yang semua batasnya terpenuhi.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class EfnAgriCommodity(models.Model):
    _name = "efn.agri.commodity"
    _description = "EFN komoditas pertanian"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    product_ids = fields.Many2many("product.product", string="Produk", required=True)
    uom_id = fields.Many2one(
        "uom.uom", string="Satuan harga", required=True, default=lambda s: s.env.ref("uom.product_uom_kgm")
    )
    floor_price = fields.Monetary(
        string="Harga dasar (floor price)",
        currency_field="currency_id",
        tracking=True,
        help="Harga beli minimum yang dijamin EFN kepada mitra tani (guaranteed offtaker).",
    )
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id, required=True)
    param_ids = fields.One2many("efn.agri.qc.param", "commodity_id", string="Parameter QC", copy=True)
    grade_ids = fields.One2many("efn.agri.grade", "commodity_id", string="Grade", copy=True)
    shelf_life_days = fields.Integer(string="Umur simpan (hari)", help="Mengisi tanggal kedaluwarsa lot saat diterima.")

    _efn_code_unique = models.Constraint("UNIQUE(code, company_id)", "Kode komoditas harus unik per PT.")

    @api.model
    def _efn_for_product(self, product):
        return self.search([("product_ids", "in", product.id)], limit=1)


class EfnAgriQcParam(models.Model):
    _name = "efn.agri.qc.param"
    _description = "EFN parameter QC komoditas"
    _order = "commodity_id, sequence, id"

    commodity_id = fields.Many2one("efn.agri.commodity", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    code = fields.Char(required=True, help="Kode pendek, mis. MOIST, REJECT.")
    kind = fields.Selection([("number", "Angka"), ("pass", "Lulus/Gagal")], default="number", required=True)
    unit = fields.Char(string="Satuan", help="Mis. %, cm, gram.")
    min_value = fields.Float(string="Min")
    max_value = fields.Float(string="Max")
    has_min = fields.Boolean(string="Pakai min")
    has_max = fields.Boolean(string="Pakai max")
    blocking = fields.Boolean(string="Wajib lolos", default=True, help="Gagal = barang ditolak (tidak masuk stok).")

    _efn_code_unique = models.Constraint("UNIQUE(commodity_id, code)", "Kode parameter unik per komoditas.")

    def _efn_check(self, value_num, value_pass):
        """True bila nilai memenuhi batas parameter."""
        self.ensure_one()
        if self.kind == "pass":
            return bool(value_pass)
        if self.has_min and value_num < self.min_value:
            return False
        return not (self.has_max and value_num > self.max_value)


class EfnAgriGrade(models.Model):
    _name = "efn.agri.grade"
    _description = "EFN grade komoditas"
    _order = "commodity_id, sequence, id"

    commodity_id = fields.Many2one("efn.agri.commodity", required=True, ondelete="cascade")
    sequence = fields.Integer(
        default=10, help="Grade terbaik di atas. Grade pertama yang semua batasnya terpenuhi dipakai."
    )
    name = fields.Char(required=True)
    price = fields.Monetary(string="Harga beli", currency_field="currency_id")
    currency_id = fields.Many2one(related="commodity_id.currency_id")
    is_reject = fields.Boolean(string="Reject", help="Grade ini = barang ditolak, tidak masuk stok.")
    rule_ids = fields.One2many("efn.agri.grade.rule", "grade_id", string="Batas", copy=True)

    @api.constrains("price", "commodity_id")
    def _check_floor(self):
        for grade in self:
            floor = grade.commodity_id.floor_price
            if not grade.is_reject and floor and grade.price < floor:
                raise ValidationError(
                    _(
                        "efn_agri:below_floor — harga grade %(grade)s di bawah harga dasar %(floor)s.",
                        grade=grade.name,
                        floor=floor,
                    )
                )


class EfnAgriGradeRule(models.Model):
    _name = "efn.agri.grade.rule"
    _description = "EFN batas parameter untuk grade"

    grade_id = fields.Many2one("efn.agri.grade", required=True, ondelete="cascade")
    param_id = fields.Many2one(
        "efn.agri.qc.param", required=True, domain="[('commodity_id', '=', parent.commodity_id)]", ondelete="cascade"
    )
    max_value = fields.Float(string="Maks")
    min_value = fields.Float(string="Min")
    has_min = fields.Boolean(string="Pakai min")
    has_max = fields.Boolean(string="Pakai maks", default=True)

    def _efn_ok(self, value):
        self.ensure_one()
        if self.has_min and value < self.min_value:
            return False
        return not (self.has_max and value > self.max_value)
