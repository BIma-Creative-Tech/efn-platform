"""Harga transfer cost-plus per pasangan PT (18-3; docs/10 W-15).

Hanya pasangan yang ditetapkan Finance (Bacha F&B <-> Bacha Retail, Holding <-> PT). Pasangan tanpa aturan = harga PO
apa adanya (mis. FARM -> GL memakai harga beli yang disepakati). Biaya = ``standard_price`` produk di PT PENJUAL.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_round


class EfnIcTransferPrice(models.Model):
    _name = "efn.ic.transfer.price"
    _description = "EFN Harga Transfer Antar-PT (cost-plus)"
    _order = "company_id, buyer_company_id, sequence, id"
    _check_company_auto = True

    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="PT penjual", required=True, default=lambda self: self.env.company, index=True
    )
    buyer_company_id = fields.Many2one("res.company", string="PT pembeli", required=True, index=True)
    categ_id = fields.Many2one(
        "product.category", string="Kategori produk", help="Kosong = semua produk. Aturan berkategori menang."
    )
    markup_pct = fields.Float(string="Markup (%)", required=True, digits=(16, 2))
    note = fields.Char(string="Dasar penetapan", help="Mis. nomor memo Finance / opini konsultan pajak (TP).")

    _efn_ic_tp_unique = models.Constraint(
        "UNIQUE(company_id, buyer_company_id, categ_id)",
        "Satu aturan per pasangan PT dan kategori.",
    )

    @api.constrains("company_id", "buyer_company_id", "markup_pct")
    def _check_pair(self):
        for rule in self:
            if rule.company_id == rule.buyer_company_id:
                raise ValidationError(_("efn_intercompany:tp_same_company — PT penjual dan pembeli harus berbeda."))
            if rule.markup_pct < 0 or rule.markup_pct > 1000:
                raise ValidationError(_("efn_intercompany:tp_markup_range — markup 0..1000 %%."))

    @api.model
    def _efn_rule_for(self, seller, buyer, product):
        rules = self.sudo().search(
            [("company_id", "=", seller.id), ("buyer_company_id", "=", buyer.id), ("active", "=", True)]
        )
        categ = product.categ_id
        while categ:
            match = rules.filtered(lambda r, c=categ: r.categ_id == c)
            if match:
                return match[:1]
            categ = categ.parent_id
        return rules.filtered(lambda r: not r.categ_id)[:1]

    @api.model
    def _efn_price_for(self, seller, buyer, product):
        """(harga, aturan) cost-plus atau (None, kosong) bila pasangan tanpa aturan."""
        rule = self._efn_rule_for(seller, buyer, product)
        if not rule:
            return None, rule
        cost = product.sudo().with_company(seller).standard_price
        price = cost * (1.0 + rule.markup_pct / 100.0)
        currency = seller.currency_id
        return float_round(price, precision_rounding=currency.rounding), rule
