"""Mitra tani (kelompok/perorangan) = vendor ``res.partner`` + data kemitraan.

Data pribadi (nama, HP, alamat) tetap di ``res.partner`` (dienkripsi ``efn_pii`` bila terpasang); di sini hanya data
usaha: kelompok, luas lahan, komoditas, harga dasar kontrak.
"""

from odoo import api, fields, models


class EfnAgriFarmer(models.Model):
    _name = "efn.agri.farmer"
    _description = "EFN mitra tani"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"
    _check_company_auto = True

    name = fields.Char(related="partner_id.name", store=True)
    code = fields.Char(string="Kode mitra", required=True, copy=False, default=lambda s: s._efn_next_code())
    partner_id = fields.Many2one("res.partner", string="Vendor", required=True, ondelete="restrict")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    group_name = fields.Char(string="Kelompok tani")
    region = fields.Char(string="Asal daerah", help="Kab/kota asal panen (ditampilkan di traceability).")
    land_area_ha = fields.Float(string="Luas lahan (ha)", digits=(10, 2))
    commodity_ids = fields.Many2many("efn.agri.commodity", string="Komoditas")
    contract_start = fields.Date(string="Kontrak mulai")
    contract_end = fields.Date(string="Kontrak selesai")
    state = fields.Selection(
        [("prospect", "Calon"), ("active", "Aktif"), ("inactive", "Nonaktif")], default="active", tracking=True
    )
    harvest_ids = fields.One2many("efn.agri.harvest", "farmer_id", string="Prediksi panen")
    inspection_ids = fields.One2many("efn.agri.inspection", "farmer_id", string="Inspeksi")
    delivered_qty = fields.Float(string="Total setoran lolos", compute="_compute_stats")
    reject_rate = fields.Float(string="% reject", compute="_compute_stats", digits=(5, 1))
    purchase_count = fields.Integer(compute="_compute_stats")

    _efn_code_unique = models.Constraint("UNIQUE(code, company_id)", "Kode mitra unik per PT.")

    @api.model
    def _efn_next_code(self):
        return self.env["ir.sequence"].next_by_code("efn.agri.farmer") or "/"

    def _compute_stats(self):
        for farmer in self:
            done = farmer.inspection_ids.filtered(lambda i: i.state == "done")
            total = sum(done.mapped("qty_received"))
            accepted = sum(done.mapped("qty_accepted"))
            farmer.delivered_qty = accepted
            farmer.reject_rate = 100.0 * (total - accepted) / total if total else 0.0
            farmer.purchase_count = self.env["purchase.order"].search_count([("partner_id", "=", farmer.partner_id.id)])

    def action_view_purchases(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "purchase.order",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.partner_id.id)],
            "context": {"default_partner_id": self.partner_id.id},
        }
