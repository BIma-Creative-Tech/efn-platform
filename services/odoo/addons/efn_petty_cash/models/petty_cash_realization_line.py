"""Satu belanja yang direalisasi: pembelian pihak ketiga (-> tagihan vendor) atau beban langsung.

Port dari `custom_petty_cash/models/petty_cash_realization_line.py` (ADR-049). Perubahan: PPh
(`x_custom_withholding_category_id`) dibuang; `company_id` related stored (rule multi-company); validasi pihak
ketiga (vendor + nota wajib) di baris; perubahan baris = penyuntingan realisasi (empat mata), hanya saat draft.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EfnPettyCashRealizationLine(models.Model):
    _name = "efn.petty.cash.realization.line"
    _description = "EFN Baris Realisasi Kas Kecil"
    _order = "sequence, id"
    _check_company_auto = True

    realization_id = fields.Many2one(
        "efn.petty.cash.realization",
        string="Realisasi",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(related="realization_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="realization_id.currency_id")
    sequence = fields.Integer(default=10)
    line_type = fields.Selection(
        [("third_party", "Pembelian Pihak Ketiga"), ("expense", "Beban")],
        string="Tipe",
        required=True,
        default="expense",
        help="Pihak ketiga: tagihan vendor (PPN masukan) dilunasi dari uang muka. Beban: Dr Beban / Cr Uang Muka.",
    )
    name = fields.Char(string="Keterangan", required=True)
    partner_id = fields.Many2one("res.partner", string="Vendor")
    product_id = fields.Many2one("product.product", string="Produk", check_company=True)
    account_id = fields.Many2one("account.account", string="Akun", required=True, check_company=True)
    tax_ids = fields.Many2many(
        "account.tax",
        string="Pajak",
        check_company=True,
        domain="[('type_tax_use', '=', 'purchase')]",
    )
    quantity = fields.Float(string="Kuantitas", default=1.0)
    price_unit = fields.Monetary(string="Jumlah (sebelum pajak)", currency_field="currency_id", required=True)
    price_subtotal = fields.Monetary(
        string="DPP",
        currency_field="currency_id",
        compute="_compute_amounts",
        store=True,
    )
    price_tax = fields.Monetary(
        string="Pajak",
        currency_field="currency_id",
        compute="_compute_amounts",
        store=True,
    )
    price_total = fields.Monetary(
        string="Total",
        currency_field="currency_id",
        compute="_compute_amounts",
        store=True,
    )
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "efn_petty_cash_realization_line_attachment_rel",
        "line_id",
        "attachment_id",
        string="Nota / Bukti",
        help="Nota pemasok (wajib untuk pihak ketiga) atau bukti beban.",
    )

    @api.depends("price_unit", "quantity", "tax_ids", "currency_id")
    def _compute_amounts(self):
        for line in self:
            subtotal = (line.price_unit or 0.0) * (line.quantity or 1.0)
            if line.tax_ids:
                taxes = line.tax_ids.compute_all(
                    subtotal,
                    currency=line.currency_id,
                    quantity=1.0,
                    partner=line.partner_id,
                )
                line.price_subtotal = taxes["total_excluded"]
                line.price_total = taxes["total_included"]
                line.price_tax = taxes["total_included"] - taxes["total_excluded"]
            else:
                line.price_subtotal = line.price_total = subtotal
                line.price_tax = 0.0

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for line in self.filtered("product_id"):
            if not line.name:
                line.name = line.product_id.display_name
            if not line.account_id:
                line.account_id = line.product_id.product_tmpl_id.get_product_accounts()["expense"]

    @api.onchange("line_type")
    def _onchange_line_type(self):
        for line in self.filtered(lambda ln: ln.line_type == "expense"):
            line.partner_id = False

    @api.constrains("line_type", "partner_id", "attachment_ids")
    def _check_third_party(self):
        for line in self.filtered(lambda ln: ln.line_type == "third_party"):
            if not line.partner_id:
                raise UserError(_("Baris pihak ketiga '%s' wajib punya vendor.", line.name or "?"))
            if not line.attachment_ids:
                raise UserError(_("Baris pihak ketiga '%s' wajib melampirkan nota pemasok.", line.name or "?"))

    @api.constrains("price_unit", "quantity")
    def _check_positive(self):
        for line in self:
            if line.price_unit <= 0 or line.quantity <= 0:
                raise UserError(_("Jumlah dan kuantitas baris '%s' harus positif.", line.name or "?"))

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.realization_id._efn_mark_editor()
        return lines

    def write(self, vals):
        realizations = self.realization_id
        res = super().write(vals)
        (realizations | self.realization_id)._efn_mark_editor()
        return res

    def unlink(self):
        realizations = self.realization_id
        res = super().unlink()
        realizations.exists()._efn_mark_editor()
        return res
