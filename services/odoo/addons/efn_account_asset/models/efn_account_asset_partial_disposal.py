"""Riwayat pelepasan sebagian aset pool. Port `custom.fixed.asset.partial.disposal` (ADR-049). Dibuat wizard saja."""

from odoo import fields, models

RETIREMENT_REASONS = [
    ("scrap", "Rusak / dibuang"),
    ("sale", "Dijual"),
    ("loss", "Hilang / dicuri"),
    ("transfer", "Dipindahkan keluar"),
    ("other", "Lainnya"),
]


class EfnAccountAssetPartialDisposal(models.Model):
    _name = "efn.account.asset.partial.disposal"
    _description = "EFN Asset Partial Retirement"
    _order = "disposal_date desc, id desc"

    name = fields.Char(string="Referensi", readonly=True)
    asset_id = fields.Many2one("efn.account.asset", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="asset_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="asset_id.currency_id", store=True)
    disposal_date = fields.Date(required=True)
    reason = fields.Selection(RETIREMENT_REASONS, default="scrap", required=True)
    quantity = fields.Float(string="Unit Dilepas", required=True, digits="Product Unit of Measure")
    quantity_before = fields.Float(string="Unit Sebelum", readonly=True, digits="Product Unit of Measure")
    quantity_after = fields.Float(string="Unit Sesudah", readonly=True, digits="Product Unit of Measure")
    cost_removed = fields.Monetary(string="Nilai Dikeluarkan", currency_field="currency_id", readonly=True)
    accumulated_removed = fields.Monetary(string="Akumulasi Dikeluarkan", currency_field="currency_id", readonly=True)
    net_book_value_removed = fields.Monetary(
        string="Nilai Buku Dikeluarkan", currency_field="currency_id", readonly=True
    )
    proceeds = fields.Monetary(string="Hasil", currency_field="currency_id", readonly=True)
    gain_loss = fields.Monetary(string="Laba / (Rugi)", currency_field="currency_id", readonly=True)
    move_id = fields.Many2one("account.move", string="Jurnal", readonly=True, copy=False)
    note = fields.Text()
